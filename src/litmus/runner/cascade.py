"""Runner-neutral cascade walk — sidecar + profile → merged :class:`TestEntry`.

Re-exports :func:`litmus.execution.sidecar.merged_test_entry` and adds
:func:`cascade_for` that combines sidecar lookup, profile lookup, and
merge into a single call. Each runner's plugin invokes this once per
test to get the typed :class:`TestEntry` it needs to inject markers
from.

Inputs are runner-neutral (a :class:`SidecarConfig` and the active
:class:`ProfileConfig`, plus class / function names). Pytest's
plugin reads the sidecar via ``load_sidecar(module_file)`` and the
profile via ``get_active_profile()``; another runner reads its
host's equivalents and calls the same function.
"""

from __future__ import annotations

from litmus.config.test_config import SidecarConfig, TestEntry
from litmus.execution.sidecar import _merge_entry_into, merged_test_entry
from litmus.models.project import ProfileConfig

__all__ = ["cascade_for", "merged_test_entry"]


def cascade_for(
    sidecar: SidecarConfig | None,
    profile: ProfileConfig | None,
    cls_name: str | None,
    func_name: str | None,
) -> TestEntry:
    """Build the merged :class:`TestEntry` for one test.

    Cascade order: sidecar (file → class → leaf) then profile
    (root → class → leaf), with last-wins per field. Either source
    may be ``None``.
    """
    merged = TestEntry()
    if sidecar is not None:
        _merge_entry_into(merged, merged_test_entry(sidecar, cls_name, func_name))
    if profile is not None:
        _merge_entry_into(merged, merged_test_entry(profile, cls_name, func_name))
    return merged
