"""Behavior contract for ``parts_with_provenance``.

Mirrors ``test_stations_provenance`` — same merged-with-badge pattern,
different entity. Monkeypatches ``discover_parts`` (dict-returning)
and ``usage_stats_by`` and asserts the union + classification.

Observation keys on the hardware ``uut_part_number``, not the config
slug ``part_id`` — so configured parts join to run history by their
declared ``part_number``, and an observed part number with no config
becomes an ``observed_only`` row identified by that part number. See
``docs/_internal/explorations/best-available-identity.md`` (Phase 0).
"""

from __future__ import annotations

from datetime import datetime

from testerkit.ui.shared import services


def _fake_part(
    part_id: str,
    part_number: str = "",
    name: str = "",
    revision: str = "",
    n_chars: int = 0,
):
    """Shape matching what discover_parts() returns (a dict, not a model)."""
    return {
        "id": part_id,
        "part_number": part_number,
        "name": name,
        "revision": revision,
        "characteristics": {f"c{i}": object() for i in range(n_chars)},
    }


def test_configured_no_runs(monkeypatch):
    monkeypatch.setattr(
        services, "discover_parts", lambda: [_fake_part("tps54302", part_number="TPS54302RGYR")]
    )
    monkeypatch.setattr(services, "usage_stats_by", lambda _field: {})

    rows = services.parts_with_provenance()
    assert len(rows) == 1
    assert rows[0].id == "tps54302"
    assert rows[0].provenance == "configured"
    assert rows[0].runs == 0


def test_configured_with_runs_stays_configured(monkeypatch):
    """YAML part with runs stays 'Configured' — chip is binary.

    Configured part joins observed runs by its ``part_number``, not its id.
    """
    monkeypatch.setattr(
        services,
        "discover_parts",
        lambda: [
            _fake_part(
                "tps54302", part_number="TPS54302RGYR", name="3A Buck", revision="A", n_chars=4
            )
        ],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "TPS54302RGYR": {
                "runs": 7,
                "passed": 6,
                "failed": 1,
                "last_run": datetime(2026, 5, 25, 14, 0, 0),
            }
        },
    )

    rows = services.parts_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "tps54302"
    assert r.provenance == "configured"
    assert r.runs == 7
    assert r.characteristics == 4
    assert r.name == "3A Buck"
    assert r.revision == "A"


def test_observed_only(monkeypatch):
    """A uut_part_number appears in runs with no YAML declaring it —
    observed_only row, identified by the part number itself."""
    monkeypatch.setattr(services, "discover_parts", lambda: [])
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {"DEMO-MYSTERY-99": {"runs": 2, "passed": 1, "failed": 1, "last_run": None}},
    )

    rows = services.parts_with_provenance()
    assert len(rows) == 1
    r = rows[0]
    assert r.id == "DEMO-MYSTERY-99"
    assert r.provenance == "observed_only"
    assert r.runs == 2
    assert r.name == ""
    assert r.revision == ""
    assert r.characteristics == 0


def test_mixed_configured_and_observed(monkeypatch):
    monkeypatch.setattr(
        services,
        "discover_parts",
        lambda: [
            _fake_part("tps54302", part_number="TPS54302RGYR", name="One"),
            _fake_part("lm317", part_number="LM317KTTR", name="Two"),
        ],
    )
    monkeypatch.setattr(
        services,
        "usage_stats_by",
        lambda _field: {
            "LM317KTTR": {"runs": 9, "passed": 9, "failed": 0, "last_run": None},
            "UNKNOWN-UUT-PN": {"runs": 1, "passed": 0, "failed": 1, "last_run": None},
        },
    )

    rows = services.parts_with_provenance()
    by_id = {r.id: r for r in rows}
    assert by_id["tps54302"].provenance == "configured"
    assert by_id["tps54302"].runs == 0  # no matching part_number in run history
    assert by_id["lm317"].provenance == "configured"  # matched by part_number
    assert by_id["lm317"].runs == 9
    assert by_id["UNKNOWN-UUT-PN"].provenance == "observed_only"
    assert len(rows) == 3
