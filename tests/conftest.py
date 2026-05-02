"""Pytest configuration for Litmus tests."""

import os


def pytest_configure(config):
    """Set up test environment."""
    # Auto-confirm dialogs in tests to avoid timeouts
    os.environ.setdefault("LITMUS_AUTO_CONFIRM", "confirm")
    # Short daemon idle timeout to avoid thread exhaustion (each daemon
    # spawns ~100 gRPC threads; 44+ concurrent daemons hits OS limits)
    os.environ.setdefault("LITMUS_DAEMON_IDLE_TIMEOUT", "2")
