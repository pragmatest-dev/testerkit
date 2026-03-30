"""Observer registry for driver-library-specific event interpretation.

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.observers"]
    mydriver = "my_package.observers:MyDriverObserver"
"""

from importlib.metadata import entry_points as _entry_points

# Import built-in observers (triggers __init_subclass__ registration)
import litmus.instruments.observers.daqmx  # noqa: F401
import litmus.instruments.observers.generic  # noqa: F401
import litmus.instruments.observers.lantz  # noqa: F401
import litmus.instruments.observers.modbus  # noqa: F401
import litmus.instruments.observers.motion  # noqa: F401
import litmus.instruments.observers.ni_modular  # noqa: F401
import litmus.instruments.observers.ophyd  # noqa: F401
import litmus.instruments.observers.pymeasure  # noqa: F401
import litmus.instruments.observers.qcodes  # noqa: F401
import litmus.instruments.observers.scpi  # noqa: F401
import litmus.instruments.observers.tektronix  # noqa: F401
import litmus.instruments.observers.visa  # noqa: F401
from litmus.instruments.observers._base import detect_protocol, get_observer_class

# Load third-party observer plugins via entry points
for _ep in _entry_points(group="litmus.observers"):
    try:
        _ep.load()
    except Exception:
        pass

__all__ = ["detect_protocol", "get_observer_class"]
