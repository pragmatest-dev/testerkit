"""Platform-level parallel slot execution for multi-UUT testing.

SlotRunner manages per-slot **subprocesses** with environment-based isolation.
Each slot gets its own OS process with slot-specific env vars. The parent
process coordinates sync points via EventStore.

Usage:
    runner = SlotRunner(
        slots=resolved_slots,
        uuts={"slot_1": uut1, "slot_2": uut2},
        session_id=session_id,
    )
    results = runner.run(["pytest", "tests/", "-v"])
    # results = {"slot_1": SlotResult(outcome="passed"), ...}
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from litmus.data.models import UUT
from litmus.execution.slots import ResolvedSlot

if TYPE_CHECKING:
    from litmus.data.event_store import EventStore
    from litmus.execution.sync import SyncCoordinator

logger = logging.getLogger(__name__)


def _build_slot_env(
    slot_id: str,
    uut: UUT,
    slot: ResolvedSlot,
    base_env: dict[str, str],
) -> dict[str, str]:
    """Build environment variables for a slot subprocess.

    Environment variables set:
    - ``_LITMUS_SLOT_ID`` — which slot this process handles
    - ``LITMUS_UUT_SERIAL`` — UUT serial for this slot
    - ``LITMUS_UUT_PART_NUMBER`` — optional UUT part number
    - ``LITMUS_UUT_REVISION`` — optional UUT revision
    - ``LITMUS_UUT_LOT_NUMBER`` — optional UUT lot/batch number
    - ``LITMUS_FIXTURE_SLOT`` — JSON-serialized slot config
    """
    env = base_env.copy()
    env["_LITMUS_SLOT_ID"] = slot_id
    env["LITMUS_UUT_SERIAL"] = uut.serial
    if uut.part_number:
        env["LITMUS_UUT_PART_NUMBER"] = uut.part_number
    if uut.revision:
        env["LITMUS_UUT_REVISION"] = uut.revision
    if uut.lot_number:
        env["LITMUS_UUT_LOT_NUMBER"] = uut.lot_number
    if slot.uut_resource:
        env["LITMUS_UUT_RESOURCE"] = slot.uut_resource
    env["LITMUS_FIXTURE_SLOT"] = slot.model_dump_json()
    return env


@dataclass
class SlotResult:
    """Outcome of a single slot's subprocess execution."""

    slot_id: str
    outcome: str  # "passed", "failed", "errored"
    returncode: int | None = None
    output_lines: list[str] = field(default_factory=list, repr=False)


class SlotRunner:
    """Runs a command for each UUT slot in parallel subprocesses.

    Each subprocess gets:
    - ``_LITMUS_SESSION_ID`` — shared across all slots
    - ``_LITMUS_SLOT_ID`` — which slot this process handles
    - ``_LITMUS_SLOT_COUNT`` — total slot count (for sync)
    - ``LITMUS_UUT_SERIAL`` — UUT serial for this slot
    - ``LITMUS_UUT_PART_NUMBER`` — optional UUT metadata
    - ``LITMUS_UUT_RESOURCE`` — UUT driver connection string
    - ``LITMUS_FIXTURE_SLOT`` — JSON-serialized slot config
    - ``_LITMUS_INSTRUMENT_SERVER`` — instrument server address (if shared instruments)
    - ``_LITMUS_SHARED_ROLES`` — comma-separated roles served remotely

    Sync points use EventStore events.
    """

    def __init__(
        self,
        slots: dict[str, ResolvedSlot],
        uuts: dict[str, UUT],
        *,
        session_id: UUID | None = None,
        instrument_server_address: str | None = None,
        shared_roles: set[str] | None = None,
        child_grace_seconds: float = 5.0,
    ) -> None:
        if not slots:
            raise ValueError("At least one slot is required")

        missing = set(slots) - set(uuts)
        if missing:
            raise ValueError(f"Missing UUT identity for slots: {', '.join(sorted(missing))}")

        self._slots = slots
        self._uuts = uuts
        self._session_id = session_id or uuid4()
        self._instrument_server_address = instrument_server_address
        self._shared_roles = shared_roles or set()
        self._child_grace_seconds = child_grace_seconds
        # Live child processes, keyed by slot id. Populated as each child
        # is spawned in :meth:`run`; consulted by
        # :meth:`_propagate_termination` (called from
        # ``pytest_keyboard_interrupt``) to forward SIGTERM and by the
        # ``finally`` cleanup path to kill survivors past the grace
        # budget. Empty outside of a ``run()`` call.
        self._processes: dict[str, subprocess.Popen] = {}

    def _propagate_termination(self) -> None:
        """Forward SIGTERM to every live child.

        Called from the orchestrator's ``pytest_keyboard_interrupt``
        path so each child gets a chance to run its own cleanup chain
        (``pytest_keyboard_interrupt`` → fixture teardown →
        ``Terminated``) *before* the orchestrator's existing
        ``finally`` block falls through to ``proc.kill()``.

        Idempotent — already-exited children are skipped, and a second
        call after the first SIGTERM has been delivered is a no-op.
        Errors raised by ``proc.terminate`` (e.g. process gone between
        ``poll`` and the syscall) are swallowed; the goal is best-effort
        propagation, not a strict delivery guarantee.
        """
        for slot_id, proc in self._processes.items():
            if proc.poll() is None:
                logger.info("Forwarding SIGTERM to slot '%s'", slot_id)
                try:
                    proc.terminate()
                except (ProcessLookupError, OSError):
                    pass

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
        self._processes = {}
        threads: list[threading.Thread] = []

        base_env = os.environ.copy()
        if env:
            base_env.update(env)

        # Set shared env vars
        base_env["_LITMUS_SESSION_ID"] = str(self._session_id)
        base_env["_LITMUS_SLOT_COUNT"] = str(len(self._slots))

        # Instrument server env vars for shared instruments
        if self._instrument_server_address and self._shared_roles:
            base_env["_LITMUS_INSTRUMENT_SERVER"] = self._instrument_server_address
            base_env["_LITMUS_SHARED_ROLES"] = ",".join(
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
                uut = self._uuts[slot_id]
                slot_env = _build_slot_env(slot_id, uut, slot, base_env)
                slot_env["_LITMUS_SLOT_INDEX"] = str(slot_ids.index(slot_id))

                result = SlotResult(slot_id=slot_id, outcome="errored")
                results[slot_id] = result

                logger.info(
                    "Spawning slot '%s' (UUT %s): %s",
                    slot_id,
                    uut.serial,
                    " ".join(cmd),
                )

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=slot_env,
                    text=True,
                )
                self._processes[slot_id] = proc

                # Emit SlotStarted event
                if event_log is not None:
                    from litmus.data.events import SlotStarted

                    event_log.emit(
                        SlotStarted(
                            session_id=self._session_id,
                            slot_id=slot_id,
                            uut_serial=uut.serial,
                        )
                    )

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
                    event_log.emit(
                        SlotCompleted(
                            session_id=self._session_id,
                            slot_id=slot_id,
                            outcome=result.outcome,
                            error_message=(
                                f"exit code {result.returncode}"
                                if result.outcome != "passed"
                                else None
                            ),
                        )
                    )

        finally:
            # Terminate any still-running worker processes. If
            # ``_propagate_termination`` already fired (KeyboardInterrupt
            # path), most children are already exiting and ``terminate``
            # below is a no-op; the wait/kill cascade then enforces the
            # grace budget for any laggard.
            for slot_id, proc in self._processes.items():
                if proc.poll() is None:
                    logger.warning("Terminating orphaned slot '%s'", slot_id)
                    proc.terminate()
                    try:
                        proc.wait(timeout=self._child_grace_seconds)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self._processes = {}
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
        result.outcome = "passed" if returncode == 0 else "failed"

        # Immediately notify coordinator so blocked sync points unblock
        if returncode != 0 and coordinator is not None:
            coordinator.mark_slot_dead(result.slot_id)


# ---------------------------------------------------------------------------
# Pytest-plugin orchestrator layer
#
# ``pytest_runtestloop`` in plugin.py detects orchestrator mode and delegates
# to :func:`run_multi_slot_session`, which spawns per-slot subprocesses via
# :class:`SlotRunner`, optionally stands up an :class:`InstrumentServer` for
# shared instruments, coordinates session-level events, and aggregates results.
# ---------------------------------------------------------------------------


def is_orchestrator_mode(config) -> bool:
    """Detect if this process should orchestrate multi-slot execution.

    Orchestrator mode activates when:

    1. ``_LITMUS_SLOT_ID`` is NOT set (we're not a worker child)
    2. ``--slot=N`` is NOT set (operator is targeting one specific
       slot in single-process mode — bypass orchestrator dispatch)
    3. A multi-slot fixture config is detected

    The ``--slot`` opt-out is what makes operator targeting work
    against a multi-slot fixture: the operator passes ``--slot=slot_2``
    (and a single ``--uut-serial``) and gets a single-process run that
    records as ``slot_id=slot_2`` instead of N parallel children.
    """
    if os.environ.get("_LITMUS_SLOT_ID"):
        return False  # Already a worker

    if config.getoption("--slot"):
        return False  # Operator targeting a single slot in single-process mode

    from litmus.pytest_plugin.helpers import find_fixture_file

    fixture_path = find_fixture_file(config)
    if fixture_path is None:
        return False

    try:
        from litmus.store import load_fixture

        fc = load_fixture(fixture_path)
        return fc.is_multi_slot
    except Exception:  # noqa: BLE001 — fall back to single-slot on any load error
        # Missing or invalid fixture file — fall back to single-slot mode
        # and let the normal config-loading path surface the real error.
        return False


def is_worker_mode() -> bool:
    """Detect if this process is a multi-slot worker child."""
    return bool(os.environ.get("_LITMUS_SLOT_ID"))


def _build_child_cmd(config) -> list[str]:
    """Build the pytest command for child processes.

    Reconstructs the original pytest invocation, stripping ``--uut-serial(s)``
    (each child gets its own ``--uut-serial`` via env var).
    """
    import sys

    args = list(config.invocation_params.args)

    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("--uut-serials="):
            continue
        if arg == "--uut-serials":
            skip_next = True
            continue
        if arg.startswith("--uut-serial="):
            continue
        if arg == "--uut-serial":
            skip_next = True
            continue
        filtered.append(arg)

    return [sys.executable, "-m", "pytest"] + filtered


def _extract_pytest_summary(output_lines: list[str]) -> str:
    """Extract the pytest summary line from worker output.

    Scans from the end looking for lines matching pytest's summary format
    (e.g., ``1 passed``, ``2 failed, 1 passed``).
    """
    import re

    pattern = re.compile(r"\d+ (passed|failed|error|warning|skipped|deselected)")
    for line in reversed(output_lines):
        if pattern.search(line):
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            clean = clean.strip("= ").strip()
            return clean
    return "(no summary)"


def _report_slot_results(session, results: dict[str, SlotResult]) -> None:
    """Report per-slot results from subprocess mode."""
    import sys

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write("Multi-UUT Results\n")
    sys.stdout.write("=" * 60 + "\n")
    for slot_id in results:
        r = results[slot_id]
        status = "PASS" if r.outcome == "passed" else "FAIL"
        summary = _extract_pytest_summary(r.output_lines)
        sys.stdout.write(f"  {slot_id}: {status}  {summary}\n")
    sys.stdout.write("=" * 60 + "\n\n")
    sys.stdout.flush()

    failed_slots = [sid for sid, r in results.items() if r.outcome != "passed"]
    session.testsfailed = len(failed_slots)


def _run_subprocess_mode(
    session,
    slots: dict[str, ResolvedSlot],
    uuts: dict[str, UUT],
    session_id: UUID,
    shared_roles: set[str] | None = None,
    station_instruments: dict[str, Any] | None = None,
    mock_all: bool = False,
    child_grace_seconds: float = 5.0,
) -> None:
    """Run multi-slot tests using subprocess-per-slot.

    If shared instruments are detected, starts an :class:`InstrumentServer` in
    the orchestrator process and passes the address to workers via env vars.
    Workers get ``RemoteInstrumentProxy`` objects for those roles.
    """
    import sys

    from litmus.data.events import SessionStarted
    from litmus.execution._state import get_current_logger
    from litmus.execution.session_scope import open_session

    server = None
    shared_drivers: dict[str, Any] = {}
    shared_roles = shared_roles or set()
    station_instruments = station_instruments or {}

    # Only serve non-mocked shared instruments through the server.
    # Mocked instruments get independent instances per worker so each
    # worker has its own mock state (per-test mock values don't leak).
    served_roles: set[str] = set()
    if shared_roles:
        from litmus.instruments.lifecycle import disconnect, load_and_connect
        from litmus.instruments.server import InstrumentServer
        from litmus.models.instrument import InstrumentRecord

        concurrent_roles: set[str] = set()
        resources: dict[str, str] = {}

        for role in shared_roles:
            inst_cfg = station_instruments.get(role)
            if inst_cfg is None:
                continue
            if mock_all or inst_cfg.mock:
                continue  # Workers get independent mocks

            try:
                record = InstrumentRecord(
                    role=role,
                    instrument_id=role,
                    driver=inst_cfg.driver,
                    resource=inst_cfg.resource or "",
                    protocol="visa",
                    mocked=False,
                )
                driver = load_and_connect(
                    record,
                    mock=False,
                    mock_config=inst_cfg.mock_config,
                )
                shared_drivers[role] = driver
                served_roles.add(role)
                if inst_cfg.resource:
                    resources[role] = inst_cfg.resource
                if inst_cfg.type == "switch":
                    concurrent_roles.add(role)
            except Exception as exc:
                for cleanup_role, cleanup_driver in shared_drivers.items():
                    disconnect(cleanup_driver, cleanup_role)
                raise RuntimeError(f"Failed to connect shared instrument {role!r}: {exc}") from exc

        if shared_drivers:
            server = InstrumentServer(
                shared_drivers,
                resources=resources,
                concurrent_roles=concurrent_roles,
            )
            server.start()

    current_logger = get_current_logger()

    station_id = ""
    station_name = None
    station_type = None
    station_location = None
    operator_id = None
    operator_name = None
    fixture_id = None
    if current_logger:
        tr = current_logger.test_run
        station_id = tr.station_id
        station_name = tr.station_name
        station_type = tr.station_type
        station_location = tr.station_location
        operator_id = tr.operator_id
        operator_name = tr.operator_name
        fixture_id = tr.fixture_id

    # Open the session via the shared primitive — the orchestrator owns it
    # (reuse the store if the logger fixture set one, else create + own). Emits
    # SessionStarted(slot_count); workers attach to this injected session id.
    scope = open_session(
        SessionStarted.from_station(
            session_id=session_id,
            station_id=station_id,
            station_name=station_name,
            station_type=station_type,
            station_location=station_location,
            operator_id=operator_id,
            operator_name=operator_name,
            fixture_id=fixture_id,
            slot_count=len(slots),
        ),
        session_id=session_id,
        reuse_existing=True,
        emit_lifecycle=True,
    )

    try:

        def _stream_output(slot_id: str, line: str) -> None:
            sys.stdout.write(f"[{slot_id}] {line}")
            sys.stdout.flush()

        from litmus.execution._state import set_active_slot_runner

        runner = SlotRunner(
            slots,
            uuts,
            session_id=session_id,
            instrument_server_address=server.address_str if server else None,
            shared_roles=served_roles if server else None,
            child_grace_seconds=child_grace_seconds,
        )
        child_cmd = _build_child_cmd(session.config)
        # Expose the runner via ContextVar so
        # ``pytest_keyboard_interrupt`` can forward SIGTERM to live
        # children before the orchestrator's own teardown unwinds.
        set_active_slot_runner(runner)
        try:
            results = runner.run(
                child_cmd,
                on_output=_stream_output,
                event_store=scope.event_store,
            )
        finally:
            set_active_slot_runner(None)

        _report_slot_results(session, results)

        scope.emit_ended()
    finally:
        scope.close_stores()  # closes log + (owned) EventStore client connection; daemon untouched

        if server is not None:
            server.stop(force=True)

        from litmus.instruments.lifecycle import disconnect

        for role, driver in shared_drivers.items():
            disconnect(driver, role)


def run_multi_slot_session(
    session,
    station_config=None,
) -> bool:
    """Orchestrate a multi-slot pytest session.

    Called from ``pytest_runtestloop`` when :func:`is_orchestrator_mode` is
    true. Loads the fixture config, resolves per-slot UUT identities, stands
    up an :class:`InstrumentServer` for any shared roles, then drives
    :class:`SlotRunner` and reports per-slot summaries.

    Returns ``True`` to signal the caller should suppress pytest's default
    test-execution loop.
    """
    import warnings

    from litmus.execution._state import get_current_logger
    from litmus.execution.slots import detect_shared_instruments, resolve_fixture_slots
    from litmus.execution.uut_provider import CLIUUTProvider
    from litmus.pytest_plugin import _mocks_active
    from litmus.pytest_plugin.helpers import find_fixture_file
    from litmus.store import load_fixture, load_project_config

    fixture_path = find_fixture_file(session.config)
    if fixture_path is None:
        return False
    fixture_config = load_fixture(fixture_path)

    slots = resolve_fixture_slots(fixture_config)
    slot_ids = list(slots.keys())

    shared_roles = detect_shared_instruments(slots)
    station_instruments = station_config.instruments if station_config else {}

    uut_serial = session.config.getoption("--uut-serial")
    uut_serials_raw = session.config.getoption("--uut-serials")
    provider = CLIUUTProvider.from_cli_args(
        uut_serial=uut_serial,
        uut_serials=uut_serials_raw,
        slot_ids=slot_ids,
    )
    uuts = {sid: provider.get_uut(sid) for sid in slot_ids}

    if uut_serial and not uut_serials_raw and len(slot_ids) > 1:
        warnings.warn(
            f"Single --uut-serial '{uut_serial}' applied to all {len(slot_ids)} slots. "
            f"Use --uut-serials for per-slot assignment.",
            stacklevel=1,
        )

    current_logger = get_current_logger()
    session_id = current_logger.test_run.session_id if current_logger else uuid4()

    project_config = load_project_config()

    _run_subprocess_mode(
        session,
        slots,
        uuts,
        session_id,
        shared_roles=shared_roles,
        station_instruments=station_instruments,
        mock_all=_mocks_active(session.config),
        child_grace_seconds=project_config.multi_slot.child_grace_seconds,
    )

    return True
