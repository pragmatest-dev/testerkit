"""Lantz observer for Feat/DictFeat descriptor-based instruments.

Lantz uses ``Feat`` and ``DictFeat`` descriptors similar to PyMeasure
properties but with Pint units and validation. ``Action`` decorator for methods.

This observer reuses PyMeasureObserver's descriptor introspection since
Lantz descriptors follow the same ``__get__``/``__set__`` protocol.
"""

from __future__ import annotations

from typing import Any

from litmus.instruments.observer import InstrumentEventEmitter
from litmus.instruments.observers.descriptor import DescriptorObserver


class LantzObserver(DescriptorObserver):
    """Lantz Feat/DictFeat descriptor observer."""

    observer_protocols = ["lantz"]

    def __init__(
        self,
        driver_class: type,
        role: str,
        emit: InstrumentEventEmitter,
        yaml_overrides: dict[str, str] | None = None,
        driver_instance: Any = None,
    ) -> None:
        super().__init__(driver_class, role, emit, yaml_overrides, driver_instance)
