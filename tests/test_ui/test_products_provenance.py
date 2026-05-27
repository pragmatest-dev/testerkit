"""Behavior contract for ``products_with_provenance``.

Mirrors ``test_stations_provenance`` — same merged-with-badge pattern,
different entity. Monkeypatches ``discover_products`` (dict-returning)
and ``usage_stats_by`` and asserts the union + classification.
"""

from __future__ import annotations

from datetime import datetime

from litmus.ui.shared import services


def _fake_product(product_id: str, name: str = "", revision: str = "", n_chars: int = 0):
    """Shape matching what discover_products() returns (a dict, not a model)."""
    return {
        "id": product_id,
        "name": name,
        "revision": revision,
        "characteristics": {f"c{i}": object() for i in range(n_chars)},
    }


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(services, "discover_products", lambda: [_fake_product("tps54302")])
    monkeypatch.setattr(services, "usage_stats_by", lambda _field: {})

    rows = services.products_with_provenance()
    assert len(rows) == 1
    assert rows[0].id == "tps54302"
    assert rows[0].provenance == "configured"


def test_configured_in_use(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_products",
        lambda: [_fake_product("tps54302", name="3A Buck", revision="A", n_chars=4)],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "tps54302": {
                "runs": 7,
                "passed": 6,
                "failed": 1,
                "last_run": datetime(2026, 5, 25, 14, 0, 0),
            }
        },
    )

    rows = services.products_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.provenance == "in_use"
    assert r.runs == 7
    assert r.characteristics == 4
    assert r.name == "3A Buck"
    assert r.revision == "A"


def test_observed_only(monkeypatch):
    """Product id appears in runs with no YAML — observed_only row."""
    monkeypatch.setattr(services, "discover_products", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {"mystery-part-99": {"runs": 2, "passed": 1, "failed": 1, "last_run": None}},
    )

    rows = services.products_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "mystery-part-99"
    assert r.provenance == "observed_only"
    assert r.runs == 2
    assert r.name == ""
    assert r.revision == ""
    assert r.characteristics == 0


def test_mixed_three_kinds(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_products",
        lambda: [
            _fake_product("tps54302", name="One"),
            _fake_product("lm317", name="Two"),
        ],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "lm317": {"runs": 9, "passed": 9, "failed": 0, "last_run": None},
            "unknown-dut": {"runs": 1, "passed": 0, "failed": 1, "last_run": None},
        },
    )

    rows = services.products_with_provenance()
    by_id = {r.id: r for r in rows}
    assert by_id["tps54302"].provenance == "configured"
    assert by_id["lm317"].provenance == "in_use"
    assert by_id["unknown-dut"].provenance == "observed_only"
    assert len(rows) == 3
