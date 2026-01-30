"""NiceGUI-based operator UI.

This module initializes the NiceGUI app and imports all page modules
which register their routes via @ui.page decorators.
"""

from pathlib import Path

from nicegui import app

# Serve static files
_static_dir = Path(__file__).parent / "static"
app.add_static_files("/static", _static_dir)

# Import pages module which imports all page submodules and registers routes
# This must happen after the app is initialized
from litmus.ui import pages  # noqa: F401, E402
