"""Behavior contract for ``uuts_from_runs``.

Unlike the other entity helpers, UUTs are purely observed (never
declared in YAML), so there's no provenance discriminator. The test
monkeypatches ``RunsQuery`` to drive the SQL contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from litmus.ui.shared import services


class _FakeRunsQuery:
    """Stand-in for ``litmus.analysis.runs_query.RunsQuery``.

    The helper calls the protected ``_query_dicts`` method directly —
    the comment in services.py marks this as the documented escape
    hatch for ad-hoc SQL.
    """

    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        _ = sql
        return self._payload


def _install_fake(monkeypatch, payload: list[dict[str, Any]]) -> None:
    import litmus.analysis.runs_query as rq

    monkeypatch.setattr(rq, "RunsQuery", lambda *_a, **_kw: _FakeRunsQuery(payload))


def test_empty(monkeypatch):
    _install_fake(monkeypatch, [])
    assert services.uuts_from_runs() == []


def test_skips_blank_serials(monkeypatch):
    """Defensive — the SQL filters serials, but skip again on the read side."""
    _install_fake(
        monkeypatch,
        [
            {
                "serial": None,
                "part_number": "X",
                "lot_number": "L",
                "runs": 1,
                "passed": 1,
                "failed": 0,
                "last_run": None,
            },
            {
                "serial": "",
                "part_number": "X",
                "lot_number": "L",
                "runs": 1,
                "passed": 1,
                "failed": 0,
                "last_run": None,
            },
        ],
    )
    assert services.uuts_from_runs() == []


def test_typed_row_shape(monkeypatch):
    _install_fake(
        monkeypatch,
        [
            {
                "serial": "SN001",
                "part_number": "TPS54302",
                "lot_number": "LOT-A",
                "runs": 5,
                "passed": 4,
                "failed": 1,
                "last_run": datetime(2026, 5, 26),
            }
        ],
    )
    rows = services.uuts_from_runs()
    assert len(rows) == 1
    r = rows[0]
    assert r.serial == "SN001"
    assert r.part_number == "TPS54302"
    assert r.lot_number == "LOT-A"
    assert r.runs == 5
    assert r.passed == 4
    assert r.failed == 1
    assert r.last_run == datetime(2026, 5, 26)


def test_null_part_and_lot_become_empty(monkeypatch):
    _install_fake(
        monkeypatch,
        [
            {
                "serial": "SN001",
                "part_number": None,
                "lot_number": None,
                "runs": 1,
                "passed": 1,
                "failed": 0,
                "last_run": None,
            }
        ],
    )
    rows = services.uuts_from_runs()
    assert rows[0].part_number == ""
    assert rows[0].lot_number == ""
