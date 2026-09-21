"""Unit tests for ``testerkit.pytest_plugin.helpers`` pure functions."""

from __future__ import annotations

from types import SimpleNamespace

from testerkit.pytest_plugin.helpers import join_marker_names


def _marker(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


class TestJoinMarkerNames:
    def test_empty_or_none_returns_none(self) -> None:
        assert join_marker_names(None) is None
        assert join_marker_names([]) is None

    def test_joins_distinct_names_preserving_source_order(self) -> None:
        markers = [_marker("testerkit_sweeps"), _marker("testerkit_limits")]
        assert join_marker_names(markers) == "testerkit_sweeps,testerkit_limits"

    def test_sort_true_sorts_the_joined_names(self) -> None:
        markers = [_marker("zeta"), _marker("alpha")]
        assert join_marker_names(markers, sort=True) == "alpha,zeta"

    def test_dedups_a_marker_yielded_more_than_once_by_iter_markers(self) -> None:
        """docs/36 P4 — the bug this guards: `item.iter_markers()` walks the
        marker chain and yields the SAME name twice when a marker is applied
        at more than one level (e.g. directly on the function and again via
        a fixture/plugin that re-applies it) — it does not de-dupe itself.
        Two marker objects sharing a name are one semantic tag, so the
        rendered string must not repeat it (`testerkit_limits,testerkit_limits`
        was the observed symptom on the run-detail Steps table)."""
        markers = [_marker("testerkit_limits"), _marker("testerkit_limits")]
        assert join_marker_names(markers) == "testerkit_limits"

    def test_dedup_keeps_first_occurrence_position(self) -> None:
        markers = [_marker("a"), _marker("b"), _marker("a"), _marker("c"), _marker("b")]
        assert join_marker_names(markers) == "a,b,c"

    def test_dedup_and_sort_compose(self) -> None:
        markers = [_marker("zeta"), _marker("alpha"), _marker("zeta")]
        assert join_marker_names(markers, sort=True) == "alpha,zeta"
