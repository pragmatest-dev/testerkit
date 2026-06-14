"""Shared test helpers for observer tests."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from litmus.instruments.observer import DriverObserver, InstrumentEventEmitter


class CollectingLog:
    """In-memory event log that collects emitted events."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


def make_emitter(role: str = "inst") -> tuple[InstrumentEventEmitter, CollectingLog]:
    """Create an InstrumentEventEmitter backed by a CollectingLog."""
    log = CollectingLog()
    emitter = InstrumentEventEmitter(event_log=log, session_id=uuid4(), role=role)  # type: ignore[arg-type]
    return emitter, log


def make_observer(
    observer_cls: type[DriverObserver],
    driver_class: type | None = None,
    role: str = "inst",
    yaml_overrides: dict[str, str] | None = None,
    driver_instance: Any = None,
) -> tuple[DriverObserver, CollectingLog]:
    """Create an observer instance backed by a CollectingLog."""
    emitter, log = make_emitter(role)
    obs = observer_cls(
        driver_class or object,
        role,
        emitter,
        yaml_overrides=yaml_overrides,
        driver_instance=driver_instance,
    )
    return obs, log
