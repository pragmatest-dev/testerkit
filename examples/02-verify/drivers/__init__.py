"""Example driver classes (DMM, PSU) used across the curriculum.

**TesterKit does not ship instrument drivers.** Bring your own — any of:

* `PyMeasure <https://pymeasure.readthedocs.io/>`_ — 100+ ready-made
  drivers for common bench instruments. ``from pymeasure.instruments.keysight
  import Keysight34461A`` and you're done.
* `PyVISA <https://pyvisa.readthedocs.io/>`_ — raw SCPI access; write
  your own thin class on top.
* Vendor SDKs — most major instrument vendors ship Python bindings.
* Hand-rolled — what these example files demonstrate.

The classes here are minimal placeholders with PyVISA-style shape
(resource string, ``connect`` / ``disconnect``, ``__enter__`` /
``__exit__``, SCPI-named methods). Method bodies raise
``NotImplementedError`` because there's no real instrument behind
them; replace each body with a real SCPI call (or swap the entire
class for a PyMeasure / vendor driver) when you wire to hardware.

``testerkit.instruments.Mock(cls, **return_values)`` works against any
driver class — your own, PyMeasure's, a vendor's. ``Mock`` returns
an instance that ``isinstance``-passes the original class so type
hints and downstream code don't notice.
"""

from drivers.dmm import DMM
from drivers.psu import PSU

__all__ = ["DMM", "PSU"]
