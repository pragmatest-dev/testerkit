"""Pytest configuration for Litmus tests.

Storage routing
---------------

The repo-root ``litmus.yaml`` sets ``data_dir: data``.
The outer pytest, run from the repo root, resolves
``resolve_data_dir()`` → ``<repo>/data`` via the
project-config ancestor walk. So **every test's storage stays
project-local** instead of polluting the global
``~/.local/share/litmus/data`` store an operator might also
use for real hardware data.

Pytester / subprocess tests run with CWD = ``pytester.path``
(``/tmp/pytest-of-ryanf/...``) — their ancestor walk doesn't see
the repo's ``litmus.yaml``. We pin ``LITMUS_HOME`` to point at
the same project-local dir so they fall through to the same
storage; one canonical-singleton per project across outer + inner
pytest = no per-test daemon spawning.
"""

import os
import shutil
import signal
from pathlib import Path

import pytest

# Auto-confirm any interactive dialogs so tests don't block.
os.environ.setdefault("LITMUS_AUTO_CONFIRM", "confirm")


def _project_data_dir() -> Path:
    """Resolve the project-local data dir from this conftest's location.

    Walks up from this file to find ``litmus.yaml``, reads its
    ``data_dir`` field. We don't use ``resolve_data_dir`` here
    because it'd require importing litmus, which triggers a chain
    of imports before pytest's collector runs — slow startup.
    """
    repo_root = Path(__file__).resolve().parent.parent
    yaml_path = repo_root / "litmus.yaml"
    if yaml_path.exists():
        # Tiny hand-parser: looks for a line ``data_dir: <value>``.
        # Avoids the YAML import cost during pytest startup; the
        # project ``litmus.yaml`` is hand-written and stable.
        for line in yaml_path.read_text().splitlines():
            if line.strip().startswith("data_dir:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                # Strip trailing comment, if any.
                value = value.split("#", 1)[0].strip()
                return (repo_root / value).resolve()
    return repo_root / ".tmp" / "test-data"


_PROJECT_DATA = _project_data_dir()

# Pin ``LITMUS_HOME`` so pytester subprocesses (whose CWD is a
# tmp_path with no ``litmus.yaml`` ancestor) inherit the same
# project-local store via the env-var fallback in
# ``resolve_data_dir``. Set to the parent of the data dir
# so ``LITMUS_HOME/data`` matches what the outer pytest gets
# from the repo's ``litmus.yaml``.
os.environ.setdefault("LITMUS_HOME", str(_PROJECT_DATA.parent))

# Pre-create the events_dir so the runs daemon's subscription
# wires up on first spawn (see
# ``_runs_duckdb_daemon._start_event_subscriber``).
(_PROJECT_DATA / "events").mkdir(parents=True, exist_ok=True)


def _stop_scratch_daemons(data_dir: Path) -> None:
    """SIGTERM any store daemon whose pid file lives under ``data_dir``.

    All four store managers write a ``*_pid`` file (``_runs_duckdb_pid``,
    ``_duckdb_pid``, ``_flight_pid``, ``_files_catalog_pid``). A stray
    daemon left from a previous session would keep serving the
    about-to-be-wiped dir (and leak its ~95 gRPC threads), so stop it
    first. Best-effort: stale/dead/reused pids and permission errors are
    ignored — tests respawn fresh daemons regardless.
    """
    for pid_file in data_dir.rglob("*_pid"):
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # liveness probe — raises if not alive
            os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass


@pytest.fixture(scope="session", autouse=True)
def _clean_scratch_data_dir():
    """Start each test session from an empty scratch data dir — like CI.

    ``<repo>/data`` is gitignored scratch shared by every test and never
    otherwise reset, so it accumulates run / measurement / file rows
    across local runs indefinitely. Any test that reads GLOBAL state
    (e.g. ``distinct_values`` capped at ``LIMIT 500``) then flakes once
    enough has piled up — passing in isolation, failing only in the full
    suite. Stop stray daemons bound to the old dir, wipe the scratch, and
    recreate the events dir the runs daemon subscribes to; tests then
    spawn fresh daemons against empty data. CI runs from a clean checkout
    so it never saw this — only persistent local dev did.
    """
    _stop_scratch_daemons(_PROJECT_DATA)
    for sub in ("runs", "events", "channels", "files"):
        shutil.rmtree(_PROJECT_DATA / sub, ignore_errors=True)
    (_PROJECT_DATA / "events").mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(autouse=True)
def _reset_filestore_singleton():
    """Clear the process-wide ``get_filestore()`` singleton after each test.

    Tests that monkeypatch ``resolve_data_dir`` to a ``tmp_path`` can cache a
    tmp-bound FileStore in the module singleton (ref resolution in
    ``data/backends/parquet.py`` reaches for ``get_filestore()``). Without a
    reset that instance leaks into the next test, whose ``/api/files`` route
    then resolves against the deleted tmp dir and 404s. Reset on teardown so
    no test inherits a stale singleton.
    """
    yield
    from litmus.data.files import _reset_for_tests

    _reset_for_tests()
