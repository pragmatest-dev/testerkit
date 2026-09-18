"""Shared SQL helpers for DuckDB query construction."""

from __future__ import annotations

_ESCAPE = "\\"
_WILDCARD_CHARS = frozenset({"*", "?"})


def sql_escape(value: str) -> str:
    """Escape single quotes in SQL string literals."""
    return value.replace("'", "''")


def has_wildcard(value: str) -> bool:
    """True if `value` contains a `*` or `?` glob metacharacter."""
    return any(c in _WILDCARD_CHARS for c in value)


def glob_to_like_pattern(value: str) -> str:
    """Translate one `*`/`?` glob value into a backslash-escaped SQL LIKE
    pattern: `*` -> `%`, `?` -> `_`, and a literal `\\`, `%`, or `_` already
    present in `value` is prefixed with `\\` so it matches literally instead
    of acting as a LIKE metacharacter. Pair with ``ESCAPE '\\'`` (DuckDB) —
    or bind as a query parameter on a backend whose ``LIKE`` already treats
    `\\` as its escape character (e.g. BigQuery Standard SQL, which has no
    ``ESCAPE`` clause at all)."""
    out: list[str] = []
    for ch in value:
        if ch == _ESCAPE:
            out.append(_ESCAPE * 2)
        elif ch == "%":
            out.append(_ESCAPE + "%")
        elif ch == "_":
            out.append(_ESCAPE + "_")
        elif ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        else:
            out.append(ch)
    return "".join(out)


def partition_exact_and_glob(values: list[str]) -> tuple[list[str], list[str]]:
    """Split non-empty filter values into `(exact_values, glob_values)` —
    `glob_values` still need `glob_to_like_pattern` applied by the caller
    before being bound or interpolated. Preserves each group's relative
    order."""
    exact = [v for v in values if v and not has_wildcard(v)]
    globs = [v for v in values if v and has_wildcard(v)]
    return exact, globs


def multi_filter_clauses(filters: dict[str, str | list[str] | None]) -> list[str]:
    """Build ``col = '…' / col IN (…)`` clauses from multi-value filters.

    Empty / ``None`` values contribute nothing. Used by query
    methods that take ``str | list[str] | None`` filters so a
    multi-select widget can drive an ``IN (…)`` clause directly.
    """
    out: list[str] = []
    for column, value in filters.items():
        if value is None or value == "":
            continue
        values = [value] if isinstance(value, str) else [v for v in value if v]
        if not values:
            continue
        if len(values) == 1:
            out.append(f"{column} = '{sql_escape(values[0])}'")
        else:
            quoted = ", ".join(f"'{sql_escape(v)}'" for v in values)
            out.append(f"{column} IN ({quoted})")
    return out
