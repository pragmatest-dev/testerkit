"""Internal event-subscriber registry — used by the ``testerkit export``
CLI replay path (csv/json/stdf/hdf5/tdms/mdf4 converters in
:mod:`testerkit.data.exporters`). The canonical run materializer
(parquet + DuckDB index) is owned by the runs daemon and does NOT
go through this registry.

Not a public extension protocol: third-party packages should not register
formats via entry points or any other mechanism. The set of supported
formats is fixed by the package and ships through ``testerkit export``.
"""

import testerkit.data.exporters.csv_exporter  # noqa: F401
import testerkit.data.exporters.json_exporter  # noqa: F401
from testerkit.data.event_log import EventSubscriber
from testerkit.data.subscribers._base import get_subscriber_class, list_subscribers
from testerkit.data.subscribers._output_file import OutputFile
from testerkit.data.subscribers.replay import replay_to_subscriber

# Optional industry-format subscribers — registered via ``__init_subclass__``
# at import time when their respective extras are installed
# (``testerkit[stdf]``, ``[hdf5]``, ``[tdms]``, ``[mdf4]``). When the
# extra isn't installed the import fails; swallow so the rest of the
# package still loads. The CLI surfaces a "format not registered" error
# pointing at the right extra.
try:
    import testerkit.data.exporters.stdf  # noqa: F401
except ImportError:
    pass
try:
    import testerkit.data.exporters.hdf5  # noqa: F401
except ImportError:
    pass
try:
    import testerkit.data.exporters.tdms  # noqa: F401
except ImportError:
    pass
try:
    import testerkit.data.exporters.mdf4  # noqa: F401
except ImportError:
    pass

__all__ = [
    "EventSubscriber",
    "OutputFile",
    "get_subscriber_class",
    "list_subscribers",
    "replay_to_subscriber",
]
