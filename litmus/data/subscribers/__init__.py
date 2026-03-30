"""Event subscriber registry for live data materialization.

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.subscribers"]
    myformat = "my_package.exporters:MyFormatSubscriber"
"""

from importlib.metadata import entry_points as _entry_points

# Import built-in subscribers (triggers __init_subclass__ registration)
import litmus.data.backends.parquet  # noqa: F401
import litmus.data.exporters.csv_exporter  # noqa: F401
import litmus.data.exporters.json_exporter  # noqa: F401
from litmus.data.event_log import EventSubscriber
from litmus.data.subscribers._base import get_subscriber_class, list_subscribers
from litmus.data.subscribers._output_file import OutputFile
from litmus.data.subscribers.replay import replay_to_subscriber

# Optional subscribers (may not have deps installed)
try:
    import litmus.data.exporters.atml  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.exporters.stdf  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.exporters.hdf5  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.exporters.tdms  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.exporters.mdf4  # noqa: F401
except ImportError:
    pass

# Load third-party subscriber plugins via entry points
for _ep in _entry_points(group="litmus.subscribers"):
    try:
        _ep.load()
    except Exception:
        pass

__all__ = [
    "EventSubscriber",
    "OutputFile",
    "get_subscriber_class",
    "list_subscribers",
    "replay_to_subscriber",
]
