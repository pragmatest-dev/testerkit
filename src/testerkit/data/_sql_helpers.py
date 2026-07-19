"""Shared SQL helpers for DuckDB query construction."""

from __future__ import annotations


def sql_escape(value: str) -> str:
    """Escape single quotes in SQL string literals."""
    return value.replace("'", "''")


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
