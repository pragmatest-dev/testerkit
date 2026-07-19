"""Behavior contract for ``instrument_assets_with_provenance``.

The observed side of instruments comes from ``instruments_materialized``
(see ``services._instrument_id_usage_stats``). These tests monkeypatch
both inputs and assert the union + classification — same pattern as the
other entity provenance tests, only the inventory tab is covered (the
catalog tab is intentionally templates-only).
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from testerkit.ui.shared import services


def _fake_asset(asset_id: str, manufacturer: str = "", model: str = "", driver: str = ""):
    return SimpleNamespace(
        id=asset_id,
        driver=driver,
        info=SimpleNamespace(manufacturer=manufacturer, model=model, serial=""),
        calibration=SimpleNamespace(due_date=None, lab=""),
    )


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(services, "discover_instrument_assets", lambda: [_fake_asset("dmm-001")])
    monkeypatch.setattr(services, "_instrument_id_usage_stats", lambda: {})

    rows = services.instrument_assets_with_provenance()
    assert len(rows) == 1
    assert rows[0].provenance == "configured"


def test_configured_with_runs_resolves_identity(monkeypatch):
    """A YAML asset with runs stays 'configured'; identity (mfr + model) resolves."""
    asset = _fake_asset("dmm-001", manufacturer="Keysight", model="34461A", driver="drivers.DMM")
    asset.calibration = SimpleNamespace(due_date=date(2026, 12, 31), lab="In-house")
    monkeypatch.setattr(services, "discover_instrument_assets", lambda: [asset])
    monkeypatch.setattr(
        services,
        "_instrument_id_usage_stats",
        lambda: {
            "dmm-001": {
                "runs": 8,
                "passed": 7,
                "failed": 1,
                "last_run": datetime(2026, 5, 25),
            }
        },
    )

    rows = services.instrument_assets_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.provenance == "configured"
    assert r.runs == 8
    assert r.identity == "Keysight 34461A"
    assert r.driver == "drivers.DMM"
    assert r.cal_due == "2026-12-31"
    assert r.cal_lab == "In-house"


def test_observed_only(monkeypatch):
    """Instrument id appears in instruments_materialized but has no asset YAML."""
    monkeypatch.setattr(services, "discover_instrument_assets", lambda: [])
    monkeypatch.setattr(
        services,
        "_instrument_id_usage_stats",
        lambda: {"phantom-scope": {"runs": 2, "passed": 2, "failed": 0, "last_run": None}},
    )

    rows = services.instrument_assets_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "phantom-scope"
    assert r.provenance == "observed_only"
    assert r.identity == ""
    assert r.driver == ""


def test_mixed_configured_and_observed(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_instrument_assets",
        lambda: [_fake_asset("dmm-001"), _fake_asset("psu-001")],
    )
    monkeypatch.setattr(
        services,
        "_instrument_id_usage_stats",
        lambda: {
            "psu-001": {"runs": 4, "passed": 4, "failed": 0, "last_run": None},
            "ghost-eload": {"runs": 1, "passed": 0, "failed": 1, "last_run": None},
        },
    )

    rows = services.instrument_assets_with_provenance()
    by_id = {r.id: r for r in rows}
    assert by_id["dmm-001"].provenance == "configured"
    assert by_id["psu-001"].provenance == "configured"  # YAML wins
    assert by_id["ghost-eload"].provenance == "observed_only"
    assert len(rows) == 3
