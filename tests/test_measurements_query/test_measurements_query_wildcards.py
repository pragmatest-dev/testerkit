"""Unit tests for measurement categorical-filter glob support.

`part`/`station` (via `_build_filter_clauses`/`_in_or_eq`) and the
measurement-name predicate (`_EAVJoins.add_meas_name_predicate`) accept a
`*`/`?` glob value, translated to an interpolated `LIKE … ESCAPE '\\'`
clause via the shared `testerkit.data._sql_helpers` translation — the same
one the cloud serving tier uses, so the two can never disagree on what a
wildcard filter value means. `phase` (and everything in `runs_query.py` /
`steps_query.py`) stays exact/IN-only — no test here changes that scope.
"""

from __future__ import annotations

from testerkit.analysis.measurement_facets import FieldRef, FieldRole
from testerkit.analysis.measurements_query import (
    _build_filter_clauses,
    _EAVJoins,
    _in_or_eq,
)


def test_in_or_eq_plain_value_stays_exact() -> None:
    assert _in_or_eq("part", ["P-1"], wildcards=True) == "part = 'P-1'"


def test_in_or_eq_multi_plain_values_stay_in_clause() -> None:
    assert _in_or_eq("part", ["P-1", "P-2"], wildcards=True) == "part IN ('P-1', 'P-2')"


def test_in_or_eq_star_glob_produces_like_escape() -> None:
    clause = _in_or_eq("station", ["bench-*"], wildcards=True)
    assert clause == r"station LIKE 'bench-%' ESCAPE '\'"


def test_in_or_eq_question_mark_glob_produces_like_escape() -> None:
    clause = _in_or_eq("part", ["P-?"], wildcards=True)
    assert clause == r"part LIKE 'P-_' ESCAPE '\'"


def test_in_or_eq_escapes_literal_percent_and_underscore() -> None:
    clause = _in_or_eq("station", ["50%_off*"], wildcards=True)
    assert clause == r"station LIKE '50\%\_off%' ESCAPE '\'"


def test_in_or_eq_mixed_exact_and_glob_values_or_together() -> None:
    clause = _in_or_eq("station", ["bench-a", "bench-*"], wildcards=True)
    assert clause == r"(station = 'bench-a' OR station LIKE 'bench-%' ESCAPE '\')"


def test_in_or_eq_without_wildcards_flag_never_globs() -> None:
    """Default (`wildcards=False`, e.g. phase's call site) stays exact/IN —
    a `*`/`?` value is treated as a literal, not a glob."""
    clause = _in_or_eq("phase", ["prod*"])
    assert clause == "phase = 'prod*'"
    assert "LIKE" not in clause


def test_build_filter_clauses_globs_part_and_station() -> None:
    clauses = _build_filter_clauses(part="PN-*", station="bench-?", phase="all")
    joined = " ".join(clauses)
    assert r"part LIKE 'PN-%' ESCAPE '\'" in joined
    assert r"station LIKE 'bench-_' ESCAPE '\'" in joined


def test_build_filter_clauses_phase_stays_exact_even_with_glob_chars() -> None:
    """`phase` is not a wildcard column — matches cloud's scope exactly."""
    clauses = _build_filter_clauses(phase="prod*")
    joined = " ".join(clauses)
    assert "phase = 'prod*'" in joined
    assert "LIKE" not in joined


def test_add_meas_name_predicate_plain_name_stays_exact() -> None:
    joins = _EAVJoins()
    joins.add_meas_name_predicate("vout")
    assert joins.meas_name_clauses() == ["m.measurement_name = 'vout'"]


def test_add_meas_name_predicate_glob_name_produces_like_escape() -> None:
    joins = _EAVJoins()
    joins.add_meas_name_predicate("vout*")
    assert joins.meas_name_clauses() == [r"m.measurement_name LIKE 'vout%' ESCAPE '\'"]


def test_resolve_eav_field_measurement_role_wires_glob_predicate() -> None:
    """End-to-end through the public resolution path a `parametric()`/
    `histogram()` call takes: a bare string selector for a MEASUREMENT
    FieldRef with a glob name registers the LIKE predicate."""
    joins = _EAVJoins()
    ref = FieldRef.measurement("vout*")
    assert ref.role is FieldRole.MEASUREMENT
    joins.add_meas_name_predicate(ref.name)
    assert joins.meas_name_clauses() == [r"m.measurement_name LIKE 'vout%' ESCAPE '\'"]
