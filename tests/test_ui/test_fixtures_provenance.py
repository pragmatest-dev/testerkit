"""Behavior contract for ``fixtures_with_provenance``.

Same shape as the stations/products provenance tests. Fixtures carry
an extra ``product`` display label resolved against ``discover_products``;
this test asserts that resolution and the three provenance values.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from litmus.ui.shared import services


def _fake_fixture(
    fixture_id: str,
    name: str = "",
    product_id: str = "",
    n_connections: int = 0,
):
    return SimpleNamespace(
        id=fixture_id,
        name=name,
        product_id=product_id,
        product_family=None,
        product_revision="",
        connections={f"c{i}": object() for i in range(n_connections)},
    )


def _fake_product(product_id: str, name: str = ""):
    return {"id": product_id, "name": name}


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(services, "discover_fixtures", lambda: [_fake_fixture("fx-01")])
    monkeypatch.setattr(services, "discover_products", lambda: [])
    monkeypatch.setattr(services, "usage_stats_by", lambda _field: {})

    rows = services.fixtures_with_provenance()
    assert len(rows) == 1
    assert rows[0].provenance == "configured"


def test_configured_with_runs_resolves_product_label(monkeypatch):
    """A YAML fixture with runs stays 'configured'; product label resolves."""
    monkeypatch.setattr(
        services,
        "discover_fixtures",
        lambda: [
            _fake_fixture("fx-01", name="Bench fixture", product_id="tps54302", n_connections=8)
        ],
    )
    monkeypatch.setattr(
        services, "discover_products", lambda: [_fake_product("tps54302", "3A Buck")]
    )
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
    assert r.product == "3A Buck"
    assert r.connections == 8


def test_observed_only_has_no_product_label(monkeypatch):
    monkeypatch.setattr(services, "discover_fixtures", lambda: [])
    monkeypatch.setattr(services, "discover_products", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {"ghost-fixture": {"runs": 1, "passed": 0, "failed": 1, "last_run": None}},
    )

    rows = services.fixtures_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.provenance == "observed_only"
    assert r.product == ""
    assert r.connections == 0


def test_mixed_configured_and_observed(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_fixtures",
        lambda: [_fake_fixture("fx-01"), _fake_fixture("fx-02")],
    )
    monkeypatch.setattr(services, "discover_products", lambda: [])
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
