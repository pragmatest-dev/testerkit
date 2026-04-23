"""Conftest for the advanced PMIC-A23 demo.

Adds the parent ``demo/`` directory to ``sys.path`` so tests can
``from demo.drivers import DMM, PSU, ELoad`` — this advanced demo
reuses the driver wrappers that ship under ``demo/drivers/`` rather
than vendoring a copy here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEMO_ROOT = Path(__file__).resolve().parent.parent
if str(_DEMO_ROOT) not in sys.path:
    sys.path.insert(0, str(_DEMO_ROOT.parent))
