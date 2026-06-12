"""Behavior contract for ``fixtures_with_provenance``.

Same shape as the stations/parts provenance tests. Fixtures carry
an extra ``part`` display label resolved against ``discover_parts``;
this test asserts that resolution and the three provenance values.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from litmus.ui.shared import services


def _fake_fixture(
    fixture_id: str,
    name: str = "",
    part_id: str = "",
    n_connections: int = 0,
):
    return SimpleNamespace(
        id=fixture_id,
        name=name,
        part_id=part_id,
        part_family=None,
        part_revision="",
        connections={f"c{i}": object() for i in range(n_connections)},
    )


def _fake_part(part_id: str, name: str = ""):
    return {"id": part_id, "name": name}


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(services, "discover_fixtures", lambda: [_fake_fixture("fx-01")])
    monkeypatch.setattr(services, "discover_parts", lambda: [])
    monkeypatch.setattr(services, "usage_stats_by", lambda _field: {})

    rows = services.fixtures_with_provenance()
    assert len(rows) == 1
    assert rows[0].provenance == "configured"


def test_configured_with_runs_resolves_part_label(monkeypatch):
    """A YAML fixture with runs stays 'configured'; part label resolves."""
    monkeypatch.setattr(
        services,
        "discover_fixtures",
        lambda: [_fake_fixture("fx-01", name="Bench fixture", part_id="tps54302", n_connections=8)],
    )
    monkeypatch.setattr(services, "discover_parts", lambda: [_fake_part("tps54302", "3A Buck")])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "fx-01": {"runs": 3, "passed": 3, "failed": 0, "last_run": datetime(2026, 5, 25)}
        },
    )

    rows = services.fixtures_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.provenance == "configured"
    assert r.runs == 3
    assert r.part == "3A Buck"
    assert r.connections == 8


def test_observed_only_has_no_part_label(monkeypatch):
    monkeypatch.setattr(services, "discover_fixtures", lambda: [])
    monkeypatch.setattr(services, "discover_parts", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {"ghost-fixture": {"runs": 1, "passed": 0, "failed": 1, "last_run": None}},
    )

    rows = services.fixtures_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.provenance == "observed_only"
    assert r.part == ""
    assert r.connections == 0


def test_mixed_configured_and_observed(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_fixtures",
        lambda: [_fake_fixture("fx-01"), _fake_fixture("fx-02")],
    )
    monkeypatch.setattr(services, "discover_parts", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "fx-02": {"runs": 4, "passed": 4, "failed": 0, "last_run": None},
            "fx-ghost": {"runs": 1, "passed": 0, "failed": 1, "last_run": None},
        },
    )

    rows = services.fixtures_with_provenance()
    by_id = {r.id: r for r in rows}
    assert by_id["fx-01"].provenance == "configured"
    assert by_id["fx-02"].provenance == "configured"  # YAML wins
    assert by_id["fx-ghost"].provenance == "observed_only"
    assert len(rows) == 3
