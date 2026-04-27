"""Runner-neutral execution logic.

Pulls the parts of test execution that don't depend on a specific
runner (pytest / OpenHTF / Robot / unittest) into typed shared modules,
so each runner's plugin shrinks to a thin shim that:

* hooks into its host's lifecycle,
* reads markers / fixtures / parametrize via its host's API, and
* delegates everything else to this package.

Currently exposes:

* :mod:`litmus.runner.markers` — translate a :class:`TestEntry` into
  a runner-neutral list of :class:`MarkerSpec` instances; normalize
  inline payloads; enforce the no-stacking rule for ``litmus_X``
  decorators.
* :mod:`litmus.runner.sweeps` — translate :class:`SweepEntry` (and
  ``runner.markers.parametrize`` entries) into ``(argnames, argvalues)``
  tuples a runner can hand to its parametrize-equivalent.
* :mod:`litmus.runner.cascade` — re-exports the sidecar + profile
  cascade walk so the pytest plugin and any future runner share one
  call site.

Intentionally kept import-light: imports must not pull in pytest,
OpenHTF, or any other host runner, so this package is safe to import
from any runner plugin.
"""

from __future__ import annotations
