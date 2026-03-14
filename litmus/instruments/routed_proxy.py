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

    For shared instruments, the instrument may not be connected at proxy
    creation time. Use ``resolver`` to lazily resolve the instrument
    from the route manager's shared live instruments.

    Args:
        instrument: The real instrument instance to proxy (or None for shared).
        point_name: Fixture point name for route activation.
        route_manager: RouteManager that handles activation/deactivation.
        resolver: Optional callable that returns the instrument instance.
            Used for shared instruments where the driver is connected
            on-demand during route activation.
    """

    __slots__ = ("_instrument", "_resolver", "_point_name", "_route_manager", "_activated")

    def __init__(
        self,
        instrument: Any,
        point_name: str,
        route_manager: Any,
        resolver: Any = None,
    ) -> None:
        object.__setattr__(self, "_instrument", instrument)
        object.__setattr__(self, "_resolver", resolver)
        object.__setattr__(self, "_point_name", point_name)
        object.__setattr__(self, "_route_manager", route_manager)
        object.__setattr__(self, "_activated", False)

    def _resolve(self) -> Any:
        """Return the instrument, using resolver if set.

        Uses ``object.__getattribute__`` to bypass ``__getattr__`` which
        would trigger route activation — necessary with ``__slots__``.
        """
        resolver = object.__getattribute__(self, "_resolver")
        if resolver is not None:
            return resolver()
        return object.__getattribute__(self, "_instrument")

    def __getattr__(self, name: str) -> Any:
        if not object.__getattribute__(self, "_activated"):
            rm = object.__getattribute__(self, "_route_manager")
            if rm is None:
                pn = object.__getattribute__(self, "_point_name")
                raise ValueError(
                    f"RoutedProxy for '{pn}' has no route_manager — "
                    f"cannot activate route"
                )
            pn = object.__getattribute__(self, "_point_name")
            rm.activate(pn)
            object.__setattr__(self, "_activated", True)
        resolved = self._resolve()
        if resolved is None:
            pn = object.__getattribute__(self, "_point_name")
            raise ValueError(
                f"RoutedProxy for '{pn}' could not resolve instrument — "
                f"shared instrument may not be connected"
            )
        return getattr(resolved, name)

    def __repr__(self) -> str:
        pn = object.__getattribute__(self, "_point_name")
        activated = object.__getattribute__(self, "_activated")
        state = "active" if activated else "pending"
        try:
            inst = self._resolve()
        except (AttributeError, TypeError, RuntimeError, ValueError):
            inst = "<unresolved>"
        return f"<RoutedProxy({pn}, {state}) → {inst!r}>"
