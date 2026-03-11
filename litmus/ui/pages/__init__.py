"""UI page modules.

Import all page modules to register their @ui.page routes.
"""

import sys
import time

_start = time.perf_counter()


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


from litmus.ui.pages import dashboard  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - dashboard")

from litmus.ui.pages import designer  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - designer")

from litmus.ui.pages import docs  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - docs")

from litmus.ui.pages import fixtures  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - fixtures")

from litmus.ui.pages import instruments  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - instruments")

from litmus.ui.pages import launch  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - launch")

from litmus.ui.pages import live  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - live")

from litmus.ui.pages import products  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - products")

from litmus.ui.pages import results  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - results")

from litmus.ui.pages import sequences  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - sequences")

from litmus.ui.pages import stations  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - stations")

from litmus.ui.pages import tests  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - tests")

from litmus.ui.pages import yield_page  # noqa: F401, E402

_log(f"[pages] +{(time.perf_counter() - _start)*1000:.0f}ms - yield_page")
