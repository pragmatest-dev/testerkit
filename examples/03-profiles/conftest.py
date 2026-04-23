"""Conftest for the PMIC-A23 profiles example.

Adds the parent ``examples/`` directory to ``sys.path`` so tests can
``from examples.drivers import DMM, PSU, ELoad`` — this tier reuses
the driver wrappers under ``examples/drivers/`` rather than vendoring
a copy here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLES_ROOT = Path(__file__).resolve().parent.parent
if str(_EXAMPLES_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES_ROOT.parent))
