"""Demo test that uses operator dialogs."""

import os
import uuid

import pytest

# Generate a unique run ID for this test session (shared across all tests in this process)
_SESSION_RUN_ID = os.environ.get("_TESTERKIT_RUN_ID") or str(uuid.uuid4())


def _get_run_id() -> str:
    """Get the current test run ID."""
    return _SESSION_RUN_ID


@pytest.mark.asyncio
async def test_with_confirm_dialog():
    """Test that prompts operator for confirmation."""
    from testerkit.api.dialogs import get_dialog_manager

    manager = get_dialog_manager()
    run_id = _get_run_id()

    # This will block until operator responds via UI
    response = await manager.confirm(
        message="Is the UUT powered on and connected?",
        title="UUT Check",
        run_id=run_id,
        timeout=120,  # 2 minute timeout
    )

    if response.timed_out:
        pytest.fail("Operator did not respond in time")

    if not response.confirmed:
        pytest.fail("Operator indicated UUT is not ready")

    # Continue with test...
    assert True, "UUT confirmed ready"


@pytest.mark.asyncio
async def test_with_input_dialog():
    """Test that prompts operator for input."""
    from testerkit.api.dialogs import get_dialog_manager

    manager = get_dialog_manager()
    run_id = _get_run_id()

    response = await manager.input(
        message="Enter the UUT serial number from the label:",
        title="Serial Number",
        placeholder="e.g., UUT-001-2024",
        run_id=run_id,
        timeout=60,
    )

    if response.timed_out or response.cancelled:
        pytest.fail("Operator did not provide input")

    serial = response.value
    # Under TESTERKIT_AUTO_CONFIRM the value is "" (matches _auto_confirm); a
    # real operator session would return a typed serial.
    assert serial is not None, "Serial number response missing"
    print(f"Operator entered serial: {serial!r}")


@pytest.mark.asyncio
async def test_with_choice_dialog():
    """Test that prompts operator to select an option."""
    from testerkit.api.dialogs import get_dialog_manager

    manager = get_dialog_manager()
    run_id = _get_run_id()

    response = await manager.choose(
        message="Select the test fixture being used:",
        choices=["Fixture A - Standard", "Fixture B - High Current", "Fixture C - RF"],
        title="Fixture Selection",
        run_id=run_id,
        timeout=60,
    )

    if response.timed_out or response.cancelled:
        pytest.fail("Operator did not make a selection")

    fixture_index = response.choice
    assert fixture_index is not None, "choice is required"
    fixtures = ["Fixture A", "Fixture B", "Fixture C"]
    print(f"Operator selected: {fixtures[fixture_index]}")
    assert fixture_index in [0, 1, 2], "Invalid selection"
