"""Class-method disambiguation in sidecar ``tests:`` keys.

Two classes in one file can share a method name (e.g. ``test_rail``).
With the recursive tree shape, methods nest under their class branch
(``tests.TestClass.tests.test_rail``), so the disambiguation is
structural — no dotted-key parsing needed. A bare ``tests.test_rail``
shorthand still matches a method when the class branch is absent.
"""

from __future__ import annotations

from litmus.config.test_config import MarkerSpec, SidecarConfig, TestEntry
from litmus.execution.sidecar import sidecar_markers_for as _sidecar_markers_for


def _spec_marker(char: str) -> MarkerSpec:
    return MarkerSpec(name="litmus_spec", kwargs={"characteristic": char})


def _first_char(markers: list[MarkerSpec]) -> str | None:
    for spec in markers:
        if spec.name == "litmus_spec":
            return spec.kwargs.get("characteristic")
    return None


class TestQualifiedClassMethodKeying:
    def test_two_classes_same_method_resolve_distinctly(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "TestA": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_spec_marker("char_a")])},
                ),
                "TestB": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_spec_marker("char_b")])},
                ),
            }
        )
        a = _sidecar_markers_for(sidecar, "TestA", "test_rail")
        b = _sidecar_markers_for(sidecar, "TestB", "test_rail")
        assert _first_char(a) == "char_a"
        assert _first_char(b) == "char_b"

    def test_shorthand_matches_module_level_test(self) -> None:
        sidecar = SidecarConfig(
            tests={"test_rail": TestEntry(markers=[_spec_marker("bare_match")])},
        )
        markers = _sidecar_markers_for(sidecar, None, "test_rail")
        assert _first_char(markers) == "bare_match"

    def test_shorthand_matches_method_when_no_class_branch(self) -> None:
        """Bare method name applies to a classed test when no class branch exists."""
        sidecar = SidecarConfig(
            tests={"test_rail": TestEntry(markers=[_spec_marker("shorthand_ok")])},
        )
        markers = _sidecar_markers_for(sidecar, "TestRails", "test_rail")
        assert _first_char(markers) == "shorthand_ok"

    def test_class_branch_method_wins_over_shorthand(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "test_rail": TestEntry(markers=[_spec_marker("shorthand")]),
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_spec_marker("nested")])},
                ),
            }
        )
        markers = _sidecar_markers_for(sidecar, "TestRails", "test_rail")
        assert _first_char(markers) == "nested"

    def test_class_branch_markers_apply_to_method(self) -> None:
        """Class-scoped markers (on the branch) flow down to nested methods."""
        sidecar = SidecarConfig(
            tests={
                "TestRails": TestEntry(
                    markers=[_spec_marker("class_scope")],
                    tests={"test_rail": TestEntry()},
                ),
            }
        )
        markers = _sidecar_markers_for(sidecar, "TestRails", "test_rail")
        assert _first_char(markers) == "class_scope"

    def test_module_level_test_does_not_match_class_branch(self) -> None:
        sidecar = SidecarConfig(
            tests={
                "Nonexistent": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_spec_marker("no_match")])},
                ),
            }
        )
        markers = _sidecar_markers_for(sidecar, None, "test_rail")
        assert _first_char(markers) is None

    def test_missing_sidecar_returns_empty(self) -> None:
        assert _sidecar_markers_for(None, None, "test_rail") == []
