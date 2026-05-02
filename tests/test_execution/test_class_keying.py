"""Class-method disambiguation in sidecar ``tests:`` keys.

Two classes in one file can share a method name (e.g. ``test_rail``).
With the recursive tree shape, methods nest under their class branch
(``tests.TestClass.tests.test_rail``), so the disambiguation is
structural — no dotted-key parsing needed. A bare ``tests.test_rail``
shorthand still matches a method when the class branch is absent.
"""

from __future__ import annotations

from litmus.execution.sidecar import merged_test_entry
from litmus.models.test_config import SidecarConfig, TestEntry


class TestQualifiedClassMethodKeying:
    def test_two_classes_same_method_resolve_distinctly(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "TestA": TestEntry(
                    tests={"test_rail": TestEntry(characteristics=["char_a"])},
                ),
                "TestB": TestEntry(
                    tests={"test_rail": TestEntry(characteristics=["char_b"])},
                ),
            }
        )
        a = merged_test_entry(sidecar, "TestA", "test_rail")
        b = merged_test_entry(sidecar, "TestB", "test_rail")
        assert a.characteristics == ["char_a"]
        assert b.characteristics == ["char_b"]

    def test_shorthand_matches_module_level_test(self) -> None:
        sidecar = SidecarConfig(
            tests={"test_rail": TestEntry(characteristics=["bare_match"])},
        )
        entry = merged_test_entry(sidecar, None, "test_rail")
        assert entry.characteristics == ["bare_match"]

    def test_shorthand_matches_method_when_no_class_branch(self) -> None:
        """Bare method name applies to a classed test when no class branch exists."""
        sidecar = SidecarConfig(
            tests={"test_rail": TestEntry(characteristics=["shorthand_ok"])},
        )
        entry = merged_test_entry(sidecar, "TestRails", "test_rail")
        assert entry.characteristics == ["shorthand_ok"]

    def test_class_branch_method_wins_over_shorthand(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "test_rail": TestEntry(characteristics=["shorthand"]),
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(characteristics=["nested"])},
                ),
            }
        )
        entry = merged_test_entry(sidecar, "TestRails", "test_rail")
        assert entry.characteristics == ["nested"]

    def test_class_branch_markers_apply_to_method(self) -> None:
        """Class-scoped markers (on the branch) flow down to nested methods."""
        sidecar = SidecarConfig(
            tests={
                "TestRails": TestEntry(
                    characteristics=["class_scope"],
                    tests={"test_rail": TestEntry()},
                ),
            }
        )
        entry = merged_test_entry(sidecar, "TestRails", "test_rail")
        assert entry.characteristics == ["class_scope"]

    def test_module_level_test_does_not_match_class_branch(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "Nonexistent": TestEntry(
                    tests={"test_rail": TestEntry(characteristics=["no_match"])},
                ),
            }
        )
        entry = merged_test_entry(sidecar, None, "test_rail")
        assert entry.characteristics == []

    def test_missing_sidecar_returns_empty(self) -> None:
        entry = merged_test_entry(None, None, "test_rail")
        assert entry == TestEntry()
