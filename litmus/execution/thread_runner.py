"""Thread-per-slot execution for persistent shared instruments.

When shared instruments require persistent connections (``persistent: true``),
all slots run as threads in a single process. Each thread gets its own
``contextvars`` context, dedicated instrument pool, and shared instrument
handles for mutex-protected access to shared resources.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from litmus.data.models import DUT
from litmus.execution.slots import ResolvedSlot
from litmus.instruments.shared import SharedInstrumentHandle

logger = logging.getLogger(__name__)


@dataclass
class ThreadSlotResult:
    """Outcome of a single slot's thread execution."""

    slot_id: str
    outcome: str = "error"  # "pass", "fail", "error"
    error: str | None = None
    test_count: int = 0
    failed_count: int = 0


class ThreadSlotRunner:
    """Runs slots as threads in a single process.

    Each thread:
    - Gets its own ``contextvars.copy_context()`` for slot-specific state
    - Has access to shared instrument handles for mutex-protected access
    - Runs pytest items for its slot

    Args:
        slots: Resolved fixture slots.
        duts: DUT identity per slot.
        shared_handles: Shared instrument handles keyed by role.
        session_id: Session UUID shared across all slots.
    """

    def __init__(
        self,
        slots: dict[str, ResolvedSlot],
        duts: dict[str, DUT],
        shared_handles: dict[str, SharedInstrumentHandle] | None = None,
        *,
        session_id: UUID | None = None,
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
        self._shared_handles = shared_handles or {}
        self._session_id = session_id or uuid4()

    @property
    def session_id(self) -> UUID:
        return self._session_id

    @property
    def shared_handles(self) -> dict[str, SharedInstrumentHandle]:
        return dict(self._shared_handles)

    def run(
        self,
        run_slot_fn: Callable[..., ThreadSlotResult],
        **kwargs: Any,
    ) -> dict[str, ThreadSlotResult]:
        """Spawn one thread per slot, run the provided function, collect results.

        Args:
            run_slot_fn: Callable receiving ``(slot_id, slot, dut, shared_handles)``.
                Called in each thread with its own context.
            **kwargs: Extra keyword arguments forwarded to ``run_slot_fn``.

        Returns:
            Dict mapping slot_id → ThreadSlotResult.
        """
        results: dict[str, ThreadSlotResult] = {}
        threads: list[threading.Thread] = []

        for slot_id, slot in self._slots.items():
            dut = self._duts[slot_id]
            result = ThreadSlotResult(slot_id=slot_id)
            results[slot_id] = result

            # Each thread gets its own contextvars context
            ctx = contextvars.copy_context()

            t = threading.Thread(
                target=ctx.run,
                args=(
                    self._run_in_thread,
                    slot_id,
                    slot,
                    dut,
                    result,
                    run_slot_fn,
                    kwargs,
                ),
                name=f"litmus-slot-{slot_id}",
                daemon=True,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        return results

    def _run_in_thread(
        self,
        slot_id: str,
        slot: ResolvedSlot,
        dut: DUT,
        result: ThreadSlotResult,
        run_slot_fn: Any,
        kwargs: dict[str, Any],
    ) -> None:
        """Execute the slot function in a thread context."""
        # Set env vars read by plugin.py (worker detection), sync.py
        # (SyncPoint factory), and dut_provider.py (EnvironmentDUTProvider).
        os.environ["LITMUS_SLOT_ID"] = slot_id
        os.environ["LITMUS_DUT_SERIAL"] = dut.serial
        os.environ["LITMUS_SESSION_ID"] = str(self._session_id)

        try:
            slot_result = run_slot_fn(
                slot_id=slot_id,
                slot=slot,
                dut=dut,
                shared_handles=self._shared_handles,
                **kwargs,
            )
            result.outcome = slot_result.outcome
            result.error = slot_result.error
            result.test_count = slot_result.test_count
            result.failed_count = slot_result.failed_count
        except Exception as exc:
            logger.exception("Slot '%s' thread failed", slot_id)
            result.outcome = "error"
            result.error = str(exc)
