"""Transparent routing proxy for the ``pins[]`` access pattern.

Wraps an instrument so that the first attribute access triggers
route activation via the RouteManager. Subsequent accesses pass
through directly. Deactivation happens at test teardown via
``RouteManager.deactivate_all()``.
"""

from __future__ import annotations

from typing import Any


class RoutedProxy:
    """Lazy-activating proxy that routes signals before instrument use.

    When ``pins["VOUT"]`` returns a RoutedProxy, the first method call
    (e.g., ``.measure_voltage()``) activates the switch route. All
    subsequent calls on the same proxy pass through without re-routing.

    Args:
        instrument: The real instrument instance to proxy.
        point_name: Fixture point name for route activation.
        route_manager: RouteManager that handles activation/deactivation.
    """

    __slots__ = ("_instrument", "_point_name", "_route_manager", "_activated")

    def __init__(
        self,
        instrument: Any,
        point_name: str,
        route_manager: Any,
    ) -> None:
        object.__setattr__(self, "_instrument", instrument)
        object.__setattr__(self, "_point_name", point_name)
        object.__setattr__(self, "_route_manager", route_manager)
        object.__setattr__(self, "_activated", False)

    def __getattr__(self, name: str) -> Any:
        if not object.__getattribute__(self, "_activated"):
            rm = object.__getattribute__(self, "_route_manager")
            pn = object.__getattribute__(self, "_point_name")
            rm.activate(pn)
            object.__setattr__(self, "_activated", True)
        return getattr(object.__getattribute__(self, "_instrument"), name)

    def __repr__(self) -> str:
        inst = object.__getattribute__(self, "_instrument")
        pn = object.__getattribute__(self, "_point_name")
        activated = object.__getattribute__(self, "_activated")
        state = "active" if activated else "pending"
        return f"<RoutedProxy({pn}, {state}) → {inst!r}>"
