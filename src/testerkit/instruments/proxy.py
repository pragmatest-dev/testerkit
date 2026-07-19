"""Transparent instrument proxy that delegates to a DriverObserver.

Wraps any driver object and intercepts attribute access. The proxy is
a dumb membrane — it intercepts everything and lets the observer decide
what to do with it. No policy decisions live here.
"""

from __future__ import annotations

from typing import Any

from testerkit.instruments.observer import DriverObserver


class InstrumentProxy:
    """Transparent proxy that delegates event interpretation to an observer.

    Lifecycle methods (``connect``, ``disconnect``) flow through the
    same ``__getattr__`` path as measurement methods. The observer
    sees them via ``on_call`` and decides whether to record (the
    observer's ``LIFECYCLE_METHODS`` set typically excludes them from
    measurement event emission).
    """

    def __init__(self, driver: Any, role: str, observer: DriverObserver) -> None:
        object.__setattr__(self, "_driver", driver)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_observer", observer)

    def __getattr__(self, name: str) -> Any:
        driver = object.__getattribute__(self, "_driver")
        observer = object.__getattribute__(self, "_observer")
        attr = getattr(driver, name)

        if callable(attr):

            def wrapper(*args: Any, **kwargs: Any) -> Any:
                result = attr(*args, **kwargs)
                observer.on_call(name, args, kwargs, result)
                return result

            return wrapper

        return observer.on_getattr(name, attr)

    def __setattr__(self, name: str, value: Any) -> None:
        driver = object.__getattribute__(self, "_driver")
        observer = object.__getattribute__(self, "_observer")
        observer.on_setattr(name, value)
        setattr(driver, name, value)

    def __delattr__(self, name: str) -> None:
        driver = object.__getattribute__(self, "_driver")
        delattr(driver, name)

    def __repr__(self) -> str:
        driver = object.__getattribute__(self, "_driver")
        role = object.__getattribute__(self, "_role")
        return f"InstrumentProxy({role!r}, {driver!r})"
