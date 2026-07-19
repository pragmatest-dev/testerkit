"""Example driver classes (DMM, PSU) — see examples/06-station-catalog for
the full docstring on why TesterKit doesn't ship instrument drivers.

Same minimal placeholder classes as stage 6, copied here so this example
stands alone (each ``examples/NN-*`` package is independently installable).
"""

from drivers.dmm import DMM
from drivers.psu import PSU

__all__ = ["DMM", "PSU"]
