"""Cross-process Flight server lifecycle management.

Ref-counted singleton: first ``acquire()`` spawns a detached daemon process
running the Flight server.  Subsequent calls increment the ref count.
``release()`` decrements; when refs hit 0, the daemon starts an idle
countdown and exits.

The daemon process is fully independent — it outlives any client process.
Clients only touch a lock file and a JSON state file; the daemon polls
the state file to detect when it should shut down.

Crash safety:
- ``atexit`` / signal handlers call ``release()`` on normal exit.
- If a client crashes without releasing, the ref count is too high.
  The daemon detects stale refs by checking PIDs periodically and
  self-corrects.
- If the daemon crashes, the next ``acquire()`` detects a dead PID in
  the state file, cleans up, and spawns a new daemon.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from filelock import FileLock

_IDLE_TIMEOUT = 10  # seconds daemon waits after refs=0 before exiting
_POLL_INTERVAL = 2  # seconds between daemon ref-count checks

# Track acquired channels_dirs so atexit/signal can release them all
_acquired: set[str] = set()


def _state_path(channels_dir: Path) -> Path:
    return channels_dir / "_flight.json"


def _lock_path(channels_dir: Path) -> Path:
    return channels_dir / "_flight.lock"


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive (cross-platform)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


def acquire(channels_dir: Path, host: str = "127.0.0.1", port: int = 0) -> str:
    """Acquire a reference to the Flight server, starting it if needed.

    Returns the gRPC location string (e.g. ``grpc://127.0.0.1:12345``).
    """
    channels_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(_lock_path(channels_dir), timeout=10)
    state = _state_path(channels_dir)

    with lock:
        # Check for existing server
        if state.exists():
            try:
                data = json.loads(state.read_text())
                daemon_pid = data["pid"]
                location = data["location"]
                if _pid_alive(daemon_pid):
                    # Server alive — add our PID to refs
                    refs: list[int] = data.get("refs", [])
                    if os.getpid() not in refs:
                        refs.append(os.getpid())
                    data["refs"] = refs
                    state.write_text(json.dumps(data))
                    return location
            except Exception:
                pass
            # Stale — clean up
            state.unlink(missing_ok=True)

        # Spawn daemon
        location = _spawn_daemon(channels_dir, host, port)

        # Write initial state
        state.write_text(json.dumps({
            "location": location,
            "pid": _read_daemon_pid(channels_dir),
            "refs": [os.getpid()],
        }))

    _register_cleanup(channels_dir)
    return location


def release(channels_dir: Path) -> None:
    """Release our reference to the Flight server.

    When the last ref is removed, the daemon will idle-timeout and exit.
    """
    lock = FileLock(_lock_path(channels_dir), timeout=10)
    state = _state_path(channels_dir)

    with lock:
        if not state.exists():
            return
        try:
            data = json.loads(state.read_text())
            refs: list[int] = data.get("refs", [])
            my_pid = os.getpid()
            refs = [p for p in refs if p != my_pid]
            data["refs"] = refs
            state.write_text(json.dumps(data))
        except Exception:
            pass


def _register_cleanup(channels_dir: Path) -> None:
    """Register atexit + signal handlers to release on exit.

    Best-effort: atexit doesn't fire on SIGKILL, but the daemon's
    PID scrubbing handles that case.
    """
    key = str(channels_dir)
    if key in _acquired:
        return  # already registered
    _acquired.add(key)

    def _cleanup() -> None:
        if key in _acquired:
            _acquired.discard(key)
            try:
                release(channels_dir)
            except Exception:
                pass

    atexit.register(_cleanup)

    # Re-raise after cleanup so the process still terminates normally
    def _signal_handler(signum: int, frame: object) -> None:
        _cleanup()
        # Restore default handler and re-raise
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass  # Can't set signal handlers outside main thread


def _spawn_daemon(channels_dir: Path, host: str, port: int) -> str:
    """Spawn the Flight server as a detached process.

    Blocks until the daemon writes its port file, then returns the location.
    """
    port_file = channels_dir / "_flight_port"
    port_file.unlink(missing_ok=True)

    cmd = [
        sys.executable, "-m", "litmus.data.channels._flight_daemon",
        str(channels_dir), host, str(port),
    ]

    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)

    # Wait for daemon to write its port
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if port_file.exists():
            location = port_file.read_text().strip()
            if location:
                return location
        time.sleep(0.05)

    # Timeout — kill and raise
    proc.kill()
    raise RuntimeError(
        f"Flight daemon failed to start within 10s. "
        f"channels_dir={channels_dir}"
    )


def _read_daemon_pid(channels_dir: Path) -> int:
    """Read the daemon PID from the port file (written by daemon on startup)."""
    pid_file = channels_dir / "_flight_pid"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                return int(pid_file.read_text().strip())
            except ValueError:
                pass
        time.sleep(0.05)
    raise RuntimeError("Daemon did not write PID file")


# --- Daemon-side functions (called from _flight_daemon.py) ---


def daemon_run(channels_dir: Path, host: str, port: int) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    from uuid import UUID

    from litmus.data.channels.server import ChannelFlightServer
    from litmus.data.channels.store import ChannelStore

    store = ChannelStore(channels_dir, UUID(int=0))
    store.open()

    location = f"grpc://{host}:{port}"
    server = ChannelFlightServer(store, location)
    actual_port = server.port
    actual_location = f"grpc://{host}:{actual_port}"

    # Write port file so the spawner knows where to connect
    (channels_dir / "_flight_port").write_text(actual_location)
    (channels_dir / "_flight_pid").write_text(str(os.getpid()))

    # Update state file with actual location
    lock = FileLock(_lock_path(channels_dir), timeout=10)
    state = _state_path(channels_dir)
    with lock:
        if state.exists():
            try:
                data = json.loads(state.read_text())
                data["location"] = actual_location
                data["pid"] = os.getpid()
                state.write_text(json.dumps(data))
            except Exception:
                pass

    # Serve in background thread, monitor refs in main thread
    import threading

    serve_thread = threading.Thread(target=server.serve, daemon=True)
    serve_thread.start()

    _monitor_refs(channels_dir, server, store)


def _monitor_refs(
    channels_dir: Path,
    server: object,
    store: object,
) -> None:
    """Poll the ref count. Exit after idle timeout with zero live refs."""
    state = _state_path(channels_dir)
    lock = FileLock(_lock_path(channels_dir), timeout=10)
    idle_since: float | None = None

    while True:
        time.sleep(_POLL_INTERVAL)

        with lock:
            if not state.exists():
                break

            try:
                data = json.loads(state.read_text())
                refs: list[int] = data.get("refs", [])

                # Scrub dead PIDs
                live = [p for p in refs if _pid_alive(p)]
                if live != refs:
                    data["refs"] = live
                    state.write_text(json.dumps(data))
                    refs = live
            except Exception:
                break

        if not refs:
            if idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= _IDLE_TIMEOUT:
                break
        else:
            idle_since = None

    # Shut down
    try:
        server.shutdown()  # type: ignore[union-attr]
    except Exception:
        pass
    try:
        store.close()  # type: ignore[union-attr]
    except Exception:
        pass

    # Clean up files
    with FileLock(_lock_path(channels_dir), timeout=5):
        state.unlink(missing_ok=True)
        (channels_dir / "_flight_port").unlink(missing_ok=True)
        (channels_dir / "_flight_pid").unlink(missing_ok=True)
