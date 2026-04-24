"""Class-method disambiguation in sidecar ``tests:`` keys.

Two classes in one file can share a method name (e.g. ``test_rail``).
Keying sidecar entries by bare ``node.originalname`` collides. The
qualified form ``tests.TestClass.test_rail`` disambiguates, with the
exact-class form winning over the shorthand when both are present.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from litmus.execution.plugin import _load_test_binding


def _fake_node(method: str, cls_name: str | None = None) -> pytest.Item:
    """Build a minimal stand-in for a :class:`pytest.Item` the resolver reads."""
    cls = type(cls_name, (), {}) if cls_name is not None else None
    return cast(pytest.Item, SimpleNamespace(originalname=method, name=method, cls=cls))


def _sidecar(tests: dict[str, Any]) -> dict[str, Any]:
    return {"tests": tests}


class TestQualifiedClassMethodKeying:
    def test_two_classes_same_method_bind_distinctly(self) -> None:
        sidecar = _sidecar(
            {
                "TestA.test_rail": {"characteristic": "char_a"},
                "TestB.test_rail": {"characteristic": "char_b"},
            }
        )
        bind_a = _load_test_binding(_fake_node("test_rail", "TestA"), sidecar)
        bind_b = _load_test_binding(_fake_node("test_rail", "TestB"), sidecar)
        assert bind_a is not None and bind_a.characteristic == "char_a"
        assert bind_b is not None and bind_b.characteristic == "char_b"

    def test_shorthand_matches_module_level_test(self) -> None:
        sidecar = _sidecar({"test_rail": {"characteristic": "bare_match"}})
        bind = _load_test_binding(_fake_node("test_rail"), sidecar)
        assert bind is not None and bind.characteristic == "bare_match"

    def test_shorthand_matches_method_when_no_qualified_entry(self) -> None:
        """Bare method name still binds a classed test when the sidecar
        only has the shorthand key."""
        sidecar = _sidecar({"test_rail": {"characteristic": "shorthand_ok"}})
        bind = _load_test_binding(_fake_node("test_rail", "TestRails"), sidecar)
        assert bind is not None and bind.characteristic == "shorthand_ok"

    def test_qualified_wins_over_shorthand_when_both_present(self) -> None:
        sidecar = _sidecar(
            {
                "test_rail": {"characteristic": "shorthand"},
                "TestRails.test_rail": {"characteristic": "qualified"},
            }
        )
        bind = _load_test_binding(_fake_node("test_rail", "TestRails"), sidecar)
        assert bind is not None and bind.characteristic == "qualified"

    def test_module_level_test_does_not_match_qualified_key(self) -> None:
        sidecar = _sidecar({"Nonexistent.test_rail": {"characteristic": "no_match"}})
        bind = _load_test_binding(_fake_node("test_rail"), sidecar)
        assert bind is None

    def test_missing_sidecar_returns_none(self) -> None:
        assert _load_test_binding(_fake_node("test_rail"), None) is None

    def test_non_dict_entry_raises(self) -> None:
        sidecar = _sidecar({"test_rail": "not a dict"})
        with pytest.raises(ValueError, match="test_rail"):
            _load_test_binding(_fake_node("test_rail"), sidecar)
