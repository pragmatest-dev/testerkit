"""Regression tests for the UTC date-input primitive helpers.

These guard the two pieces of ``utc_date_input`` / ``utc_datetime_input`` that
are unit-testable without a browser:

1. ``_parse_emitted_utc`` — turning whatever the browser ``js_handler``
   ``emit()``'d (bare string or 1-element list) into the UTC string the handle
   stores.
2. ``_invoke_change_callback`` — that an async ``on_change`` is **awaited**, not
   fire-and-forget. The fire-and-forget form (``asyncio.ensure_future``) lost the
   NiceGUI client/slot context and silently broke ``push_url_state`` / the tab
   refresh, so a date change updated the display but never reached the server.
   The full JS-conversion + client-context path is covered by in-app
   (Playwright) verification; this guards the Python-side contract.
"""

from __future__ import annotations

from litmus.ui.shared.components import _invoke_change_callback, _parse_emitted_utc


class TestParseEmittedUtc:
    def test_bare_string(self) -> None:
        assert _parse_emitted_utc("2026-06-20") == "2026-06-20"

    def test_single_element_list(self) -> None:
        # NiceGUI may deliver the emitted value wrapped in a list.
        assert _parse_emitted_utc(["2026-06-20"]) == "2026-06-20"

    def test_empty_string_is_none(self) -> None:
        assert _parse_emitted_utc("") is None

    def test_empty_list_is_none(self) -> None:
        assert _parse_emitted_utc([]) is None

    def test_none_is_none(self) -> None:
        assert _parse_emitted_utc(None) is None

    def test_datetime_string_passthrough(self) -> None:
        assert _parse_emitted_utc("2026-06-20T08:30:00+00:00") == "2026-06-20T08:30:00+00:00"


class TestInvokeChangeCallback:
    async def test_async_callback_is_awaited(self) -> None:
        # The regression guard: an async on_change must COMPLETE within the call
        # (awaited), not be left scheduled. If reverted to ensure_future, ``seen``
        # would still be empty when the await returns.
        seen: list[str] = []

        async def cb(event: object) -> None:
            seen.append("ran")

        await _invoke_change_callback(cb, "event")
        assert seen == ["ran"]

    async def test_sync_callback_is_called(self) -> None:
        seen: list[object] = []
        await _invoke_change_callback(lambda e: seen.append(e), "event")
        assert seen == ["event"]

    async def test_none_callback_is_noop(self) -> None:
        # Must not raise.
        await _invoke_change_callback(None, "event")
