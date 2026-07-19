"""Observer registry for driver-library-specific event interpretation.

Extend via entry points in pyproject.toml::

    [project.entry-points."testerkit.observers"]
    mydriver = "my_package.observers:MyDriverObserver"
"""

import logging
from importlib.metadata import entry_points as _entry_points

# Import built-in observers (triggers __init_subclass__ registration)
import testerkit.instruments.observers.daqmx  # noqa: F401
import testerkit.instruments.observers.generic  # noqa: F401
import testerkit.instruments.observers.lantz  # noqa: F401
import testerkit.instruments.observers.modbus  # noqa: F401
import testerkit.instruments.observers.motion  # noqa: F401
import testerkit.instruments.observers.ni_modular  # noqa: F401
import testerkit.instruments.observers.ophyd  # noqa: F401
import testerkit.instruments.observers.pymeasure  # noqa: F401
import testerkit.instruments.observers.qcodes  # noqa: F401
import testerkit.instruments.observers.scpi  # noqa: F401
import testerkit.instruments.observers.tektronix  # noqa: F401
import testerkit.instruments.observers.visa  # noqa: F401
from testerkit.instruments.observers._base import detect_protocol, get_observer_class

logger = logging.getLogger(__name__)

# Load third-party observer plugins via entry points
for _ep in _entry_points(group="testerkit.observers"):
    try:
        _ep.load()
    except Exception as _exc:
        logger.debug("Observer plugin %s failed to load: %s", _ep.name, _exc)

__all__ = ["detect_protocol", "get_observer_class"]
