"""Unit tests for litmus.cli._time — pure functions, no daemons, no I/O."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone

import pytest

from litmus.cli._time import format_ts, parse_cli_duration, resolve_since_until

# ---------------------------------------------------------------------------
# parse_cli_duration
# ---------------------------------------------------------------------------


class TestParseCliDuration:
    def test_days(self):
        assert parse_cli_duration("7d") == timedelta(days=7)

    def test_hours(self):
        assert parse_cli_duration("4h") == timedelta(hours=4)

    def test_minutes(self):
        assert parse_cli_duration("30m") == timedelta(minutes=30)

    def test_seconds(self):
        assert parse_cli_duration("90s") == timedelta(seconds=90)

    def test_single_unit(self):
        assert parse_cli_duration("1d") == timedelta(days=1)
        assert parse_cli_duration("1h") == timedelta(hours=1)
        assert parse_cli_duration("1m") == timedelta(minutes=1)
        assert parse_cli_duration("1s") == timedelta(seconds=1)

    def test_large_value(self):
        assert parse_cli_duration("365d") == timedelta(days=365)
        assert parse_cli_duration("1000h") == timedelta(hours=1000)

    def test_leading_trailing_whitespace_ok(self):
        assert parse_cli_duration("  7d  ") == timedelta(days=7)

    def test_invalid_unit_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_cli_duration("7w")  # weeks not supported

    def test_bare_number_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_cli_duration("7")

    def test_compound_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_cli_duration("1h30m")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_cli_duration("")

    def test_old_retention_format_still_works(self):
        """Verify the d-only form used by retention.parse_duration works here too."""
        assert parse_cli_duration("30d") == timedelta(days=30)
        assert parse_cli_duration("90d") == timedelta(days=90)


# ---------------------------------------------------------------------------
# resolve_since_until
# ---------------------------------------------------------------------------


class TestResolveSinceUntil:
    """Relative durations, absolute values with explicit offsets, and bare values."""

    # -- relative duration --------------------------------------------------

    def test_relative_days_returns_utc(self):
        before = datetime.now(UTC)
        result = resolve_since_until("7d", utc=False)
        after = datetime.now(UTC)

        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None, "must be timezone-aware"
        # Should be approximately now - 7 days
        expected_lo = before - timedelta(days=7)
        expected_hi = after - timedelta(days=7)
        assert expected_lo <= dt <= expected_hi

    def test_relative_hours(self):
        before = datetime.now(UTC)
        result = resolve_since_until("4h", utc=False)
        after = datetime.now(UTC)

        dt = datetime.fromisoformat(result)
        expected_lo = before - timedelta(hours=4)
        expected_hi = after - timedelta(hours=4)
        assert expected_lo <= dt <= expected_hi

    def test_relative_minutes(self):
        before = datetime.now(UTC)
        result = resolve_since_until("30m", utc=False)
        after = datetime.now(UTC)

        dt = datetime.fromisoformat(result)
        expected_lo = before - timedelta(minutes=30)
        expected_hi = after - timedelta(minutes=30)
        assert expected_lo <= dt <= expected_hi

    def test_relative_utc_flag_no_effect(self):
        """--utc flag doesn't change relative-duration output (always UTC)."""
        r_local = resolve_since_until("1h", utc=False)
        r_utc = resolve_since_until("1h", utc=True)
        dt_local = datetime.fromisoformat(r_local)
        dt_utc = datetime.fromisoformat(r_utc)
        # Both should be close to now-1h in UTC; within 1s of each other.
        assert abs((dt_local - dt_utc).total_seconds()) < 1

    # -- explicit offset ----------------------------------------------------

    def test_explicit_utc_z_suffix(self):
        result = resolve_since_until("2024-03-15T10:00:00Z", utc=False)
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC)

    def test_explicit_positive_offset(self):
        result = resolve_since_until("2024-03-15T12:00:00+02:00", utc=False)
        dt = datetime.fromisoformat(result)
        assert dt.astimezone(UTC) == datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC)

    def test_explicit_negative_offset(self):
        result = resolve_since_until("2024-03-15T05:00:00-05:00", utc=False)
        dt = datetime.fromisoformat(result)
        assert dt.astimezone(UTC) == datetime(2024, 3, 15, 10, 0, 0, tzinfo=UTC)

    def test_explicit_offset_utc_flag_ignored(self):
        """utc flag does not override an explicit user-supplied offset."""
        result_true = resolve_since_until("2024-03-15T12:00:00+02:00", utc=True)
        result_false = resolve_since_until("2024-03-15T12:00:00+02:00", utc=False)
        dt_true = datetime.fromisoformat(result_true)
        dt_false = datetime.fromisoformat(result_false)
        assert dt_true.astimezone(UTC) == dt_false.astimezone(UTC)

    # -- bare date / datetime -----------------------------------------------

    def test_bare_date_utc_mode(self):
        result = resolve_since_until("2024-03-15", utc=True)
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC)

    def test_bare_datetime_utc_mode(self):
        result = resolve_since_until("2024-03-15T08:00:00", utc=True)
        dt = datetime.fromisoformat(result)
        assert dt == datetime(2024, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_bare_datetime_local_mode_via_tz_env(self, monkeypatch):
        """Bare value with utc=False yields a timezone-aware result.

        The exact UTC offset depends on the machine's local timezone, but the
        result must always carry an explicit offset (never naive).
        """
        import time

        original_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "UTC")
        time.tzset()  # required on Linux for TZ env var to take effect
        try:
            result = resolve_since_until("2024-03-15T08:00:00", utc=False)
        finally:
            # Restore: monkeypatch restores os.environ, but we must also call tzset.
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

        dt = datetime.fromisoformat(result)
        # Must be timezone-aware.
        assert dt.tzinfo is not None, "result must carry an explicit offset"
        # With TZ=UTC, local == UTC, so the stored value should be 08:00:00Z.
        assert dt.astimezone(UTC) == datetime(2024, 3, 15, 8, 0, 0, tzinfo=UTC)

    # -- invalid input ------------------------------------------------------

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            resolve_since_until("not-a-date", utc=False)

    def test_invalid_unit_raises(self):
        # '7w' is not a valid duration and not parseable as ISO — so we get
        # "Cannot parse" (the fallback after the ISO parse also fails).
        with pytest.raises(ValueError, match="Cannot parse"):
            resolve_since_until("7w", utc=False)


# ---------------------------------------------------------------------------
# format_ts
# ---------------------------------------------------------------------------


class TestFormatTs:
    """format_ts always returns an offset-bearing string, never naive."""

    _UTC_DT = datetime(2024, 6, 15, 14, 30, 0, tzinfo=UTC)
    _PLUS2 = datetime(2024, 6, 15, 16, 30, 0, tzinfo=timezone(timedelta(hours=2)))

    def test_none_returns_empty(self):
        assert format_ts(None, utc=False) == ""
        assert format_ts(None, utc=True) == ""

    def test_empty_string_returns_empty(self):
        assert format_ts("", utc=False) == ""
        assert format_ts("", utc=True) == ""

    # -- utc=True -----------------------------------------------------------

    def test_utc_mode_datetime_ends_with_z(self):
        result = format_ts(self._UTC_DT, utc=True)
        assert result.endswith("Z"), f"Expected trailing Z, got: {result!r}"
        assert result == "2024-06-15T14:30:00Z"

    def test_utc_mode_offset_aware_input(self):
        """Input in +02:00 should convert to UTC Z output."""
        result = format_ts(self._PLUS2, utc=True)
        assert result == "2024-06-15T14:30:00Z"

    def test_utc_mode_string_input_naive_treated_as_utc(self):
        result = format_ts("2024-06-15T14:30:00", utc=True)
        assert result == "2024-06-15T14:30:00Z"

    def test_utc_mode_string_with_z(self):
        result = format_ts("2024-06-15T14:30:00Z", utc=True)
        assert result == "2024-06-15T14:30:00Z"

    def test_utc_mode_string_with_offset(self):
        result = format_ts("2024-06-15T16:30:00+02:00", utc=True)
        assert result == "2024-06-15T14:30:00Z"

    # -- utc=False (local) --------------------------------------------------

    def test_local_mode_has_explicit_offset(self, monkeypatch):
        """In UTC local timezone, output should have +0000 offset, not be naive."""
        monkeypatch.setenv("TZ", "UTC")
        result = format_ts(self._UTC_DT, utc=False)
        # Offset present — the strftime %z produces '+0000' for UTC.
        assert "+0000" in result or result.endswith("Z") is False
        # The value must not be naive (no offset at all).
        # A proper %z appends e.g. +0000, +0530, -0500; check at least one digit after +/-
        import re

        assert re.search(r"[+-]\d{4}$", result), f"No offset found in: {result!r}"

    def test_local_mode_string_naive_treated_as_utc_then_localised(self, monkeypatch):
        """Naive stored string is treated as UTC, then localised; output has offset."""
        import re
        import time

        original_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "UTC")
        time.tzset()  # required on Linux for TZ env var to take effect
        try:
            result = format_ts("2024-06-15T14:30:00", utc=False)
        finally:
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

        assert re.search(r"[+-]\d{4}$", result), f"No offset found in: {result!r}"
        # With TZ=UTC, local == UTC, so the naive string (treated as UTC) stays at 14:30:00.
        assert "14:30:00" in result

    def test_utc_flag_false_does_not_end_with_bare_z(self, monkeypatch):
        """In non-UTC local mode, result should NOT end with Z."""
        monkeypatch.setenv("TZ", "America/New_York")
        result = format_ts(self._UTC_DT, utc=False)
        # Z suffix should not appear when utc=False; offset digits will differ.
        assert not result.endswith("Z"), f"Unexpected Z suffix: {result!r}"

    # -- unrecognised string pass-through -----------------------------------

    def test_unrecognised_string_returned_raw(self):
        """Strings that can't be parsed are returned unchanged."""
        result = format_ts("not-a-date", utc=True)
        assert result == "not-a-date"
