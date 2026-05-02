"""Shared SQL helpers for DuckDB query construction."""

from __future__ import annotations


def sql_escape(value: str) -> str:
    """Escape single quotes in SQL string literals."""
    return value.replace("'", "''")
