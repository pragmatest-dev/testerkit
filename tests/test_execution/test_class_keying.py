"""Class-method disambiguation in sidecar ``tests:`` keys.

Two classes in one file can share a method name (e.g. ``test_rail``).
Keying sidecar entries by bare ``node.originalname`` collides. The
qualified form ``tests.TestClass.test_rail`` disambiguates, with the
exact-class form winning over the shorthand when both are present.
"""

from __future__ import annotations

from litmus.config.test_config import MarkerSpec, SidecarConfig, TestMarkers
from litmus.execution.sidecar import sidecar_markers_for as _sidecar_markers_for


def _sidecar(tests: dict[str, list[MarkerSpec]]) -> SidecarConfig:
    return SidecarConfig(tests={k: TestMarkers(markers=v) for k, v in tests.items()})


def _binding_marker(char: str) -> MarkerSpec:
    return MarkerSpec(name="litmus_spec", kwargs={"characteristic": char})


def _first_char(markers: list[MarkerSpec]) -> str | None:
    for spec in markers:
        if spec.name == "litmus_spec":
            return spec.kwargs.get("characteristic")
    return None


class TestQualifiedClassMethodKeying:
    def test_two_classes_same_method_bind_distinctly(self) -> None:
        sidecar = _sidecar(
            {
                "TestA.test_rail": [_binding_marker("char_a")],
                "TestB.test_rail": [_binding_marker("char_b")],
            }
        )
        a = _sidecar_markers_for(sidecar, "TestA", "test_rail")
        b = _sidecar_markers_for(sidecar, "TestB", "test_rail")
        assert _first_char(a) == "char_a"
        assert _first_char(b) == "char_b"

    def test_shorthand_matches_module_level_test(self) -> None:
        sidecar = _sidecar({"test_rail": [_binding_marker("bare_match")]})
        markers = _sidecar_markers_for(sidecar, None, "test_rail")
        assert _first_char(markers) == "bare_match"

    def test_shorthand_matches_method_when_no_qualified_entry(self) -> None:
        """Bare method name still binds a classed test when the sidecar
        only has the shorthand key."""
        sidecar = _sidecar({"test_rail": [_binding_marker("shorthand_ok")]})
        markers = _sidecar_markers_for(sidecar, "TestRails", "test_rail")
        assert _first_char(markers) == "shorthand_ok"

    def test_qualified_wins_over_shorthand_when_both_present(self) -> None:
        sidecar = _sidecar(
            {
                "test_rail": [_binding_marker("shorthand")],
                "TestRails.test_rail": [_binding_marker("qualified")],
            }
        )
        markers = _sidecar_markers_for(sidecar, "TestRails", "test_rail")
        assert _first_char(markers) == "qualified"

    def test_module_level_test_does_not_match_qualified_key(self) -> None:
        sidecar = _sidecar({"Nonexistent.test_rail": [_binding_marker("no_match")]})
        markers = _sidecar_markers_for(sidecar, None, "test_rail")
        assert _first_char(markers) is None

    def test_missing_sidecar_returns_empty(self) -> None:
        assert _sidecar_markers_for(None, None, "test_rail") == []
