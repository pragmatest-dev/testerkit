"""Unit tests for the ``DaemonManager`` reuse-decision seam.

These exercise ``_can_reuse`` / ``_daemon_identity`` as pure functions —
no daemon is spawned. Constructing a ``DaemonManager`` subclass against a
``tmp_path`` directory is fine (it only sets ``self._dir``); only
``acquire()`` spawns a process, and these tests never call it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from litmus.data._daemon_lifecycle import DaemonManager, _installed_version


class _ToyManager(DaemonManager):
    """Minimal concrete subclass — just enough class attrs to construct."""

    _state_name = "_toy.json"
    _lock_name = "_toy.lock"
    _ready_name = "_toy_ready"
    _pid_name = "_toy_pid"


class _FingerprintManager(DaemonManager):
    """Toy subclass keying reuse on a fingerprint instead of version.

    Proves the seam: a subclass can override both hooks to change the
    reuse policy without touching ``acquire()``.
    """

    _state_name = "_fp.json"
    _lock_name = "_fp.lock"
    _ready_name = "_fp_ready"
    _pid_name = "_fp_pid"

    def _daemon_identity(self) -> dict[str, Any]:
        return {"fingerprint": "abc"}

    def _can_reuse(self, running_state: dict[str, Any]) -> bool:
        return running_state.get("fingerprint") == "abc"


def test_default_daemon_identity_is_litmus_version(tmp_path: Path) -> None:
    mgr = _ToyManager(tmp_path)
    assert mgr._daemon_identity() == {"litmus_version": _installed_version()}


def test_default_can_reuse_older_running_version_is_false(tmp_path: Path) -> None:
    mgr = _ToyManager(tmp_path)
    # An old running daemon (0.0.1) is older than whatever is installed
    # (always >= 0.0.1 in practice) so the client should NOT reuse it.
    assert mgr._can_reuse({"litmus_version": "0.0.1"}) is False


def test_default_can_reuse_equal_running_version_is_true(tmp_path: Path) -> None:
    mgr = _ToyManager(tmp_path)
    assert mgr._can_reuse({"litmus_version": _installed_version()}) is True


def test_default_can_reuse_newer_running_version_is_true(tmp_path: Path) -> None:
    mgr = _ToyManager(tmp_path)
    # A running daemon "from the future" (higher version than installed)
    # is still reusable under the ratchet: only strictly-older is rejected.
    assert mgr._can_reuse({"litmus_version": "999.0.0"}) is True


def test_default_can_reuse_missing_version_defaults_to_0_0_0(tmp_path: Path) -> None:
    mgr = _ToyManager(tmp_path)
    # No litmus_version key at all -> treated as "0.0.0", i.e. older ->
    # not reusable (matches current `.get("litmus_version", "0.0.0")` default).
    assert mgr._can_reuse({}) is False


def test_fingerprint_seam_matching_fingerprint_reuses(tmp_path: Path) -> None:
    mgr = _FingerprintManager(tmp_path)
    assert mgr._daemon_identity() == {"fingerprint": "abc"}
    assert mgr._can_reuse({"fingerprint": "abc"}) is True


def test_fingerprint_seam_mismatched_fingerprint_respawns(tmp_path: Path) -> None:
    mgr = _FingerprintManager(tmp_path)
    assert mgr._can_reuse({"fingerprint": "xyz"}) is False
    assert mgr._can_reuse({}) is False


def test_runs_manager_keys_reuse_on_projection_fingerprint(tmp_path: Path) -> None:
    """RunsDuckDBManager (the first real store activated on the seam) keys reuse
    on the projection fingerprint, not the litmus version. Pure — constructs the
    manager and calls the hooks; no daemon spawned."""
    from litmus.data._runs_duckdb_daemon import _projection_fingerprint
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    mgr = RunsDuckDBManager(tmp_path)
    fp = _projection_fingerprint()

    identity = mgr._daemon_identity()
    assert identity["fingerprint"] == fp
    assert "litmus_version" in identity  # kept for provenance

    assert mgr._can_reuse({"fingerprint": fp}) is True  # same projection → reuse
    assert mgr._can_reuse({"fingerprint": "deadbeef0000"}) is False  # different → respawn
    assert mgr._can_reuse({"litmus_version": "0.3.0"}) is False  # pre-fingerprint daemon → respawn
    assert mgr._can_reuse({}) is False
