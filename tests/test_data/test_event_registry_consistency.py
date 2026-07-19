"""Drift guards for the event-type registries.

Several places enumerate "the set of event types": the ``Event``
discriminated union, the ``ALL_EVENTS`` set, the accumulator pool's
``_EVENT_CLASSES`` deserialize map, and the accumulator's ``on_event``
``isinstance`` chain. The source of truth is the ``EventBase``
subclasses themselves (each carries its own ``event_type``). These
tests fail loudly the instant any registry falls out of sync with that
source — so a newly-added event class can't silently slip through one
of them (the failure mode behind the RunMaterialized + Observation
gaps found in review).
"""

from __future__ import annotations

import inspect
import re
from typing import get_args

from testerkit.data._accumulator_pool import _EVENT_CLASSES
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.events import ALL_EVENTS, Event, EventBase


def _concrete_event_classes() -> set[type]:
    """Every concrete event: an ``EventBase`` subclass with a fixed ``event_type``."""
    found: set[type] = set()
    stack = list(EventBase.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        field = cls.model_fields.get("event_type")
        if field is not None and isinstance(field.default, str):
            found.add(cls)
    return found


def test_event_union_covers_every_event_class() -> None:
    concrete = _concrete_event_classes()
    union_members = set(get_args(get_args(Event)[0]))
    assert concrete == union_members, (
        f"Event union out of sync with EventBase subclasses — "
        f"missing: {concrete - union_members}, extra: {union_members - concrete}"
    )


def test_all_events_covers_every_event_class() -> None:
    concrete = _concrete_event_classes()
    assert concrete == set(ALL_EVENTS), (
        f"ALL_EVENTS out of sync with EventBase subclasses — "
        f"missing: {concrete - set(ALL_EVENTS)}, extra: {set(ALL_EVENTS) - concrete}"
    )


def test_pool_deserializes_every_event_the_accumulator_consumes() -> None:
    # ``on_event`` is the source of truth for which events the in-flight
    # overlay needs; ``_EVENT_CLASSES`` is the pool's deserialize gate before
    # dispatch. An event ``on_event`` handles but the pool doesn't deserialize
    # is silently dropped from the overlay (overlay/parquet drift).
    src = inspect.getsource(EventAccumulator.on_event)
    consumed = set(re.findall(r"isinstance\(event,\s*(\w+)\)", src))
    pool_classes = {cls.__name__ for cls in _EVENT_CLASSES.values()}
    missing = consumed - pool_classes
    assert not missing, (
        f"EventAccumulator.on_event consumes {missing} but the pool's "
        f"_EVENT_CLASSES doesn't deserialize them — they would be silently "
        f"dropped from the in-flight overlay."
    )


def test_event_classes_map_keys_match_their_event_type() -> None:
    for event_type, cls in _EVENT_CLASSES.items():
        assert cls.model_fields["event_type"].default == event_type, (
            f"_EVENT_CLASSES['{event_type}'] -> {cls.__name__}, whose event_type "
            f"is '{cls.model_fields['event_type'].default}'"
        )
