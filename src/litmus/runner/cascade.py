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

Also exposes :func:`find_unmatched_profile_keys` — a profile typo
detector. A profile that lists ``tests.test_typo`` when no test by
that name exists in the collected suite is a production-screen
silent no-op; warn loudly. The runner's plugin gathers the test IDs
its host collected and asks this module which profile keys map to
nothing.
"""

from __future__ import annotations

from collections.abc import Iterable

from litmus.config.test_config import SidecarConfig, TestEntry
from litmus.execution.sidecar import _merge_entry_into, merged_test_entry
from litmus.models.project import ProfileConfig

__all__ = ["cascade_for", "find_unmatched_profile_keys", "merged_test_entry"]


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


def find_unmatched_profile_keys(
    profile: ProfileConfig,
    test_ids: Iterable[tuple[str | None, str]],
) -> list[str]:
    """Return profile.tests keys that match no collected test.

    ``test_ids`` is the iterable of ``(class_name, func_name)`` pairs
    the runner collected. Classless module-level tests pass
    ``class_name=None``. Returns formatted ``profile.tests[...]``
    paths in declared order — caller emits a warning if non-empty.
    """
    func_names: set[str] = set()
    methods_by_class: dict[str, set[str]] = {}
    class_names: set[str] = set()
    for cls_name, func_name in test_ids:
        func_names.add(func_name)
        if cls_name is not None:
            class_names.add(cls_name)
            methods_by_class.setdefault(cls_name, set()).add(func_name)

    unmatched: list[str] = []
    for key, entry in profile.tests.items():
        if key in class_names:
            for nested_key in entry.tests:
                if nested_key in methods_by_class[key]:
                    continue
                unmatched.append(f"  profile.tests[{key!r}].tests[{nested_key!r}]")
            continue
        if key in func_names:
            continue
        unmatched.append(f"  profile.tests[{key!r}]")
    return unmatched
