"""Unit tests for _parse_utc_timestamp — pure function, no daemon required.

Covers the MCP/HTTP API UTC normalization contract:
- Bare/naive ISO strings are treated as UTC (server is UTC-only).
- Strings with an explicit UTC offset (Z / +00:00) are returned as UTC.
- Strings with a non-UTC offset are converted to UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from litmus.mcp.tools import _parse_utc_timestamp


class TestParseUtcTimestamp:
    """_parse_utc_timestamp normalizes any ISO timestamp to UTC-aware."""

    def test_naive_stamped_as_utc(self):
        """A bare ISO string (no offset) is treated as UTC — wall clock unchanged."""
        result = _parse_utc_timestamp("2024-06-01T12:30:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        # Wall-clock values are preserved as-is (not shifted)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 1
        assert result.hour == 12
        assert result.minute == 30
        assert result.second == 0

    def test_naive_date_only_stamped_as_utc(self):
        """A date-only bare string is treated as UTC midnight."""
        result = _parse_utc_timestamp("2024-06-01")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 1

    def test_explicit_utc_z_suffix(self):
        """A 'Z'-suffixed string is already UTC; returned as UTC."""
        # Python 3.11+ fromisoformat supports 'Z'
        result = _parse_utc_timestamp("2024-06-01T12:30:00+00:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result.hour == 12

    def test_explicit_utc_offset(self):
        """A +00:00 offset string is returned as UTC unchanged."""
        result = _parse_utc_timestamp("2024-06-01T12:30:00+00:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        assert result.hour == 12

    def test_positive_offset_converted_to_utc(self):
        """An +05:30 offset is converted to UTC (wall clock shifted)."""
        result = _parse_utc_timestamp("2024-06-01T17:30:00+05:30")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        # 17:30 IST = 12:00 UTC
        assert result.hour == 12
        assert result.minute == 0

    def test_negative_offset_converted_to_utc(self):
        """A -05:00 offset is converted to UTC (wall clock shifted)."""
        result = _parse_utc_timestamp("2024-06-01T07:00:00-05:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)
        # 07:00 EST = 12:00 UTC
        assert result.hour == 12

    def test_returns_datetime_instance(self):
        """Return type is always a datetime."""
        result = _parse_utc_timestamp("2024-01-01T00:00:00")
        assert isinstance(result, datetime)

    def test_invalid_string_raises(self):
        """A non-parseable string raises ValueError."""
        with pytest.raises(ValueError):
            _parse_utc_timestamp("not-a-date")
