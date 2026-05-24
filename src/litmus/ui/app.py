"""NiceGUI-based operator UI.

This module initializes the NiceGUI app and imports all page modules
which register their routes via @ui.page decorators.
"""

import importlib.resources
from pathlib import Path

from nicegui import app

# Serve static files
_static_dir = Path(__file__).parent / "static"
app.add_static_files("/static", _static_dir)

# Docs assets (cropped operator-UI screenshots etc.) are referenced from
# markdown like ``![alt](../../_assets/...)`` which the browser resolves
# to ``/docs/_assets/...``. Mounting the directory here means those
# requests are served as PNGs instead of falling through to the docs
# catch-all route (which would render an HTML page-not-found body and
# break image loading). Match the wheel/editable resolution that
# docs/page.py uses for ``DOCS_DIR``.
_pkg_root = Path(str(importlib.resources.files("litmus")))
_bundled_docs = _pkg_root / "_docs"
_docs_root = _bundled_docs if _bundled_docs.exists() else _pkg_root.parent.parent / "docs"
_docs_assets_dir = _docs_root / "_assets"
if _docs_assets_dir.exists():
    app.add_static_files("/docs/_assets", _docs_assets_dir)

# Import pages module which imports all page submodules and registers routes
# This must happen after the app is initialized
from litmus.ui import pages  # noqa: F401, E402
