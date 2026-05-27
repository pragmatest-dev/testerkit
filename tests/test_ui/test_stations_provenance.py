"""Behavior contract for ``stations_with_provenance``.

The helper merges YAML-configured stations with stations observed in
run history and tags each row with a ``provenance`` discriminator.
Monkeypatches the two inputs (``discover_stations``, ``usage_stats_by``)
and asserts the union + classification.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from litmus.ui.shared import services


def _fake_station(station_id: str, name: str = "", location: str = "", n_instr: int = 0):
    """Build a minimal StationConfig-shaped object for the discover_stations stub.

    Real ``StationConfig`` has validation that would force a full YAML;
    these tests only exercise the attributes ``stations_with_provenance``
    reads off the object.
    """
    return SimpleNamespace(
        id=station_id,
        name=name,
        location=location,
        instruments={f"i{i}": object() for i in range(n_instr)},
    )


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(services, "discover_stations", lambda: [_fake_station("bench-01")])
    monkeypatch.setattr(services, "usage_stats_by", lambda _field: {})

    rows = services.stations_with_provenance()
    assert len(rows) == 1
    assert rows[0].id == "bench-01"
    assert rows[0].provenance == "configured"
    assert rows[0].runs == 0


def test_configured_in_use(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_stations",
        lambda: [_fake_station("bench-01", name="Bench 1", n_instr=3)],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "bench-01": {
                "runs": 12,
                "passed": 10,
                "failed": 2,
                "last_run": datetime(2026, 5, 25, 10, 0, 0),
            }
        },
    )

    rows = services.stations_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "bench-01"
    assert r.provenance == "in_use"
    assert r.runs == 12
    assert r.passed == 10
    assert r.failed == 2
    assert r.instruments == 3
    assert r.name == "Bench 1"


def test_observed_only(monkeypatch):
    """Station appears in runs but has no YAML — should render as observed_only."""
    monkeypatch.setattr(services, "discover_stations", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "unknown-bench": {
                "runs": 4,
                "passed": 3,
                "failed": 1,
                "last_run": datetime(2026, 5, 26, 9, 0, 0),
            }
        },
    )

    rows = services.stations_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "unknown-bench"
    assert r.provenance == "observed_only"
    assert r.runs == 4
    # observed-only rows have no YAML side
    assert r.name == ""
    assert r.location == ""
    assert r.instruments == 0


def test_mixed_three_kinds(monkeypatch):
    """All three provenance values represented in one call."""
    monkeypatch.setattr(
        services,
        "discover_stations",
        lambda: [
            _fake_station("bench-01", name="One"),
            _fake_station("bench-02", name="Two"),
        ],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "bench-02": {"runs": 5, "passed": 5, "failed": 0, "last_run": None},
            "ghost-bench": {"runs": 1, "passed": 0, "failed": 1, "last_run": None},
        },
    )

    rows = services.stations_with_provenance()
    by_id = {r.id: r for r in rows}
    assert by_id["bench-01"].provenance == "configured"
    assert by_id["bench-02"].provenance == "in_use"
    assert by_id["ghost-bench"].provenance == "observed_only"
    assert len(rows) == 3
