"""Tests for dialog event emission."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from litmus.api.dialogs.manager import DialogManager
from litmus.api.dialogs.models import ConfirmDialog, DialogResponse
from litmus.data.events import DialogOpened, DialogResponded


def _make_mock_logger() -> MagicMock:
    """Create a mock logger with event_log and session_id."""
    from uuid import uuid4

    logger = MagicMock()
    logger._session_id = uuid4()
    logger.test_run.id = uuid4()
    logger.event_log = MagicMock()
    logger.event_log.emit = MagicMock()
    return logger


@pytest.mark.asyncio()
async def test_auto_respond_emits_both_events() -> None:
    """Auto-responded dialogs emit DialogOpened and DialogResponded."""
    manager = DialogManager(auto_respond="confirm")
    mock_logger = _make_mock_logger()

    with patch("litmus.api.dialogs.manager.get_current_run_scope", return_value=mock_logger):
        dialog = ConfirmDialog(title="Test", message="Ready?")
        response = await manager.show(dialog)

    assert response.confirmed
    calls = mock_logger.event_log.emit.call_args_list
    assert len(calls) == 2

    opened = calls[0].args[0]
    assert isinstance(opened, DialogOpened)
    assert opened.dialog_id == dialog.id
    assert opened.dialog_type == "confirm"
    assert opened.title == "Test"

    responded = calls[1].args[0]
    assert isinstance(responded, DialogResponded)
    assert responded.dialog_id == dialog.id
    assert responded.response_type == "answered"
    assert responded.duration_seconds >= 0.0


@pytest.mark.asyncio()
async def test_preset_response_emits_events() -> None:
    """Preset responses also emit dialog events."""
    manager = DialogManager()
    manager.preset_response(confirmed=False, cancelled=True)
    mock_logger = _make_mock_logger()

    with patch("litmus.api.dialogs.manager.get_current_run_scope", return_value=mock_logger):
        dialog = ConfirmDialog(title="Cancel Test", message="Abort?")
        response = await manager.show(dialog)

    assert response.cancelled
    calls = mock_logger.event_log.emit.call_args_list
    responded = calls[1].args[0]
    assert isinstance(responded, DialogResponded)
    assert responded.response_type == "cancelled"


@pytest.mark.asyncio()
async def test_no_logger_no_events() -> None:
    """When no logger is in context, events are silently skipped."""
    manager = DialogManager(auto_respond="confirm")

    with patch("litmus.execution._state.get_current_run_scope", return_value=None):
        response = await manager.show(ConfirmDialog(title="Test", message="No logger"))

    assert response.confirmed  # Works fine, just no events


@pytest.mark.asyncio()
async def test_local_dialog_emits_events() -> None:
    """In-process local dialogs emit events with correct duration."""
    manager = DialogManager()
    mock_logger = _make_mock_logger()

    dialog = ConfirmDialog(title="Wait", message="Check UUT")

    async def respond_after_delay() -> None:
        await asyncio.sleep(0.05)
        manager.respond(dialog.id, DialogResponse(dialog_id=dialog.id, confirmed=True))

    # Clear auto-respond env var so dialog actually waits
    old_auto = os.environ.pop("LITMUS_AUTO_CONFIRM", None)
    try:
        with patch("litmus.api.dialogs.manager.get_current_run_scope", return_value=mock_logger):
            task = asyncio.create_task(respond_after_delay())
            response = await manager.show(dialog)
            await task
    finally:
        if old_auto is not None:
            os.environ["LITMUS_AUTO_CONFIRM"] = old_auto

    assert response.confirmed
    calls = mock_logger.event_log.emit.call_args_list
    opened_events = [c.args[0] for c in calls if isinstance(c.args[0], DialogOpened)]
    responded_events = [c.args[0] for c in calls if isinstance(c.args[0], DialogResponded)]

    assert len(opened_events) == 1
    assert len(responded_events) == 1
    assert responded_events[0].duration_seconds >= 0.04


@pytest.mark.asyncio()
async def test_register_and_respond_emits_events() -> None:
    """Externally registered dialogs emit events via register + respond."""
    manager = DialogManager()
    mock_logger = _make_mock_logger()

    dialog = ConfirmDialog(title="API", message="From API")

    with patch("litmus.api.dialogs.manager.get_current_run_scope", return_value=mock_logger):
        manager.register_dialog(dialog)

        # Verify opened event
        opened_calls = [
            c.args[0]
            for c in mock_logger.event_log.emit.call_args_list
            if isinstance(c.args[0], DialogOpened)
        ]
        assert len(opened_calls) == 1

        manager.respond(dialog.id, DialogResponse(dialog_id=dialog.id, confirmed=True))

        responded_calls = [
            c.args[0]
            for c in mock_logger.event_log.emit.call_args_list
            if isinstance(c.args[0], DialogResponded)
        ]
        assert len(responded_calls) == 1
        assert responded_calls[0].response_type == "answered"
