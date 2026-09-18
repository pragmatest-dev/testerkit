"""Unit tests for `testerkit.data._sql_helpers` — the shared `*`/`?` glob ->
SQL LIKE translation used by measurement categorical filters, plus the
existing `sql_escape` quoting helper.
"""

from __future__ import annotations

from testerkit.data._sql_helpers import (
    glob_to_like_pattern,
    has_wildcard,
    partition_exact_and_glob,
)


def test_has_wildcard_detects_star_and_question_mark() -> None:
    assert has_wildcard("v_*")
    assert has_wildcard("P-?")
    assert not has_wildcard("v_out")
    assert not has_wildcard("")


def test_glob_to_like_translates_star_to_percent() -> None:
    assert glob_to_like_pattern("vout*") == "vout%"


def test_glob_to_like_translates_question_mark_to_underscore() -> None:
    assert glob_to_like_pattern("P-?") == "P-_"


def test_glob_to_like_escapes_literal_percent_and_underscore() -> None:
    assert glob_to_like_pattern("50%_off*") == r"50\%\_off%"


def test_glob_to_like_escapes_literal_backslash() -> None:
    assert glob_to_like_pattern("a\\b*") == "a\\\\b%"


def test_partition_exact_and_glob_splits_values() -> None:
    exact, globs = partition_exact_and_glob(["bench-a", "bench-*", "", "P-?"])
    assert exact == ["bench-a"]
    assert globs == ["bench-*", "P-?"]


def test_partition_exact_and_glob_empty_list() -> None:
    assert partition_exact_and_glob([]) == ([], [])
