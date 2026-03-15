"""Platform-level parallel slot execution for multi-DUT testing.

SlotRunner manages per-slot **subprocesses** with environment-based isolation.
Each slot gets its own OS process with slot-specific env vars. The parent
process coordinates sync points via EventStore.

Usage:
    runner = SlotRunner(
        slots=resolved_slots,
        duts={"slot_1": dut1, "slot_2": dut2},
        session_id=session_id,
    )
    results = runner.run(["pytest", "tests/", "-v"])
    # results = {"slot_1": SlotResult(outcome="pass"), ...}
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from litmus.data.models import DUT
from litmus.execution.slots import ResolvedSlot

if TYPE_CHECKING:
    from litmus.data.event_store import EventStore
    from litmus.execution.sync import SyncCoordinator

logger = logging.getLogger(__name__)


def _build_slot_env(
    slot_id: str,
    dut: DUT,
    slot: ResolvedSlot,
    base_env: dict[str, str],
) -> dict[str, str]:
    """Build environment variables for a slot subprocess.

    Environment variables set:
    - ``LITMUS_SLOT_ID`` — which slot this process handles
    - ``LITMUS_DUT_SERIAL`` — DUT serial for this slot
    - ``LITMUS_DUT_PART_NUMBER`` — optional DUT part number
    - ``LITMUS_DUT_REVISION`` — optional DUT revision
    - ``LITMUS_DUT_LOT_NUMBER`` — optional DUT lot/batch number
    - ``LITMUS_FIXTURE_SLOT`` — JSON-serialized slot config
    """
    env = base_env.copy()
    env["LITMUS_SLOT_ID"] = slot_id
    env["LITMUS_DUT_SERIAL"] = dut.serial
    if dut.part_number:
        env["LITMUS_DUT_PART_NUMBER"] = dut.part_number
    if dut.revision:
        env["LITMUS_DUT_REVISION"] = dut.revision
    if dut.lot_number:
        env["LITMUS_DUT_LOT_NUMBER"] = dut.lot_number
    if slot.dut_resource:
        env["LITMUS_DUT_RESOURCE"] = slot.dut_resource
    env["LITMUS_FIXTURE_SLOT"] = slot.model_dump_json()
    return env


@dataclass
class SlotResult:
    """Outcome of a single slot's subprocess execution."""

    slot_id: str
    outcome: str  # "pass", "fail", "error"
    returncode: int | None = None
    output_lines: list[str] = field(default_factory=list, repr=False)


class SlotRunner:
    """Runs a command for each DUT slot in parallel subprocesses.

    Each subprocess gets:
    - ``LITMUS_SESSION_ID`` — shared across all slots
    - ``LITMUS_SLOT_ID`` — which slot this process handles
    - ``LITMUS_SLOT_COUNT`` — total slot count (for sync)
    - ``LITMUS_DUT_SERIAL`` — DUT serial for this slot
    - ``LITMUS_DUT_PART_NUMBER`` — optional DUT metadata
    - ``LITMUS_DUT_RESOURCE`` — DUT driver connection string
    - ``LITMUS_FIXTURE_SLOT`` — JSON-serialized slot config
    - ``LITMUS_INSTRUMENT_SERVER`` — instrument server address (if shared instruments)
    - ``LITMUS_SHARED_ROLES`` — comma-separated roles served remotely

    Sync points use EventStore events.
    """

    def __init__(
        self,
        slots: dict[str, ResolvedSlot],
        duts: dict[str, DUT],
        *,
        session_id: UUID | None = None,
        instrument_server_address: str | None = None,
        shared_roles: set[str] | None = None,
    ) -> None:
        if not slots:
            raise ValueError("At least one slot is required")

        missing = set(slots) - set(duts)
        if missing:
            raise ValueError(
                f"Missing DUT identity for slots: {', '.join(sorted(missing))}"
            )

        self._slots = slots
        self._duts = duts
        self._session_id = session_id or uuid4()
        self._instrument_server_address = instrument_server_address
        self._shared_roles = shared_roles or set()

    @property
    def session_id(self) -> UUID:
        return self._session_id

    def run(  # noqa: PLR0912
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        sync: bool = True,
        on_output: Callable[[str, str], None] | None = None,
        event_store: EventStore | None = None,
    ) -> dict[str, SlotResult]:
        """Spawn one subprocess per slot, optionally coordinate sync.

        Args:
            cmd: Command to run in each slot (e.g., ["pytest", "tests/", "-v"]).
            env: Extra environment variables to pass to all children.
            sync: If True, coordinate sync points via EventStore.
            on_output: Callback ``(slot_id, line)`` for each stdout line.
                If None, output is collected silently in SlotResult.
            event_store: Pre-existing EventStore for sync coordination.
                If None, a new one is created when ``sync=True``.

        Returns:
            Dict mapping slot_id -> SlotResult.
        """
        results: dict[str, SlotResult] = {}
        processes: dict[str, subprocess.Popen] = {}
        threads: list[threading.Thread] = []

        base_env = os.environ.copy()
        if env:
            base_env.update(env)

        # Set shared env vars
        base_env["LITMUS_SESSION_ID"] = str(self._session_id)
        base_env["LITMUS_SLOT_COUNT"] = str(len(self._slots))

        # Instrument server env vars for shared instruments
        if self._instrument_server_address and self._shared_roles:
            base_env["LITMUS_INSTRUMENT_SERVER"] = self._instrument_server_address
            base_env["LITMUS_SHARED_ROLES"] = ",".join(
                sorted(self._shared_roles),
            )

        # Start sync coordinator if needed
        coordinator: SyncCoordinator | None = None
        owns_event_store = False
        if sync and len(self._slots) > 1:
            try:
                from litmus.data.event_store import EventStore
                from litmus.execution.sync import SyncCoordinator

                if event_store is None:
                    event_store = EventStore()
                    owns_event_store = True
                coordinator = SyncCoordinator(
                    slot_count=len(self._slots),
                    session_id=self._session_id,
                    event_store=event_store,
                )
                coordinator.start()
            except (ImportError, ValueError, OSError) as exc:
                logger.warning("Sync coordinator unavailable: %s", exc)

        # Get event log for emitting slot lifecycle events
        event_log = None
        if event_store is not None:
            event_log = event_store.get_event_log(self._session_id)

        slot_ids = list(self._slots.keys())

        try:
            # Spawn one subprocess per slot
            for slot_id, slot in self._slots.items():
                dut = self._duts[slot_id]
                slot_env = _build_slot_env(slot_id, dut, slot, base_env)
                slot_env["LITMUS_SLOT_INDEX"] = str(slot_ids.index(slot_id))

                result = SlotResult(slot_id=slot_id, outcome="error")
                results[slot_id] = result

                logger.info(
                    "Spawning slot '%s' (DUT %s): %s",
                    slot_id, dut.serial, " ".join(cmd),
                )

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=slot_env,
                    text=True,
                )
                processes[slot_id] = proc

                # Emit SlotStarted event
                if event_log is not None:
                    from litmus.data.events import SlotStarted

                    event_log.emit(SlotStarted(
                        session_id=self._session_id,
                        slot_id=slot_id,
                        dut_serial=dut.serial,
                    ))

                # Monitor thread: reads stdout, waits for exit, notifies
                # coordinator *immediately* when a child dies so sync points
                # don't deadlock.
                t = threading.Thread(
                    target=self._monitor_slot,
                    args=(proc, result, coordinator, on_output),
                    name=f"litmus-slot-{slot_id}",
                    daemon=True,
                )
                t.start()
                threads.append(t)

            # Wait for all monitor threads to finish
            for t in threads:
                t.join()

            # Emit SlotCompleted events after all workers finish
            if event_log is not None:
                from litmus.data.events import SlotCompleted

                for slot_id, result in results.items():
                    event_log.emit(SlotCompleted(
                        session_id=self._session_id,
                        slot_id=slot_id,
                        outcome=result.outcome,
                        error_message=(
                            f"exit code {result.returncode}"
                            if result.outcome != "pass" else None
                        ),
                    ))

        finally:
            # Terminate any still-running worker processes
            for slot_id, proc in processes.items():
                if proc.poll() is None:
                    logger.warning("Terminating orphaned slot '%s'", slot_id)
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if coordinator:
                coordinator.stop()
            if owns_event_store and event_store is not None:
                event_store.close()

        return results

    @staticmethod
    def _monitor_slot(
        proc: subprocess.Popen,
        result: SlotResult,
        coordinator: SyncCoordinator | None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> None:
        """Read stdout and wait for exit. Notifies coordinator immediately
        when a child dies so blocked sync points don't deadlock."""
        # Read stdout line by line
        if proc.stdout is not None:
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                result.output_lines.append(stripped)
                if on_output is not None:
                    on_output(result.slot_id, line)

        # Process has exited (stdout EOF implies exit)
        proc.wait()
        returncode = proc.returncode
        result.returncode = returncode
        result.outcome = "pass" if returncode == 0 else "fail"

        # Immediately notify coordinator so blocked sync points unblock
        if returncode != 0 and coordinator is not None:
            coordinator.mark_slot_dead(result.slot_id)
