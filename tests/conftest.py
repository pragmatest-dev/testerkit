"""Pytest configuration for Litmus tests."""

import os


def pytest_configure(config):
    """Set up test environment."""
    # Auto-confirm dialogs in tests to avoid timeouts
    os.environ.setdefault("LITMUS_DIALOG_AUTO", "confirm")
