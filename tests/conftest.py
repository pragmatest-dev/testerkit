"""Pytest configuration for Litmus tests.

Storage routing
---------------

The repo-root ``litmus.yaml`` sets ``results_dir: .tmp/test-results``.
The outer pytest, run from the repo root, resolves
``resolve_results_dir()`` → ``<repo>/.tmp/test-results`` via the
project-config ancestor walk. So **every test's storage stays
project-local** instead of polluting the global
``~/.local/share/litmus/results`` store an operator might also
use for real hardware data.

Pytester / subprocess tests run with CWD = ``pytester.path``
(``/tmp/pytest-of-ryanf/...``) — their ancestor walk doesn't see
the repo's ``litmus.yaml``. We pin ``LITMUS_HOME`` to point at
the same project-local dir so they fall through to the same
storage; one canonical-singleton per project across outer + inner
pytest = no per-test daemon spawning.
"""

import os
from pathlib import Path

# Auto-confirm any interactive dialogs so tests don't block.
os.environ.setdefault("LITMUS_AUTO_CONFIRM", "confirm")

# Skip ``ParquetBackend.notify_new_run`` in tests. Storage-layer
# tests use ``ParquetBackend(results_dir=tmp_path)`` to verify
# file-write behaviour. In production the notify hop spawns the
# canonical runs daemon (already alive); in tests it spawns a
# fresh daemon for that tmp_path. Skipping the notify keeps tests
# pure-filesystem; the subscriber tests that genuinely exercise
# the daemon connect via the canonical singleton instead.
os.environ.setdefault("LITMUS_SKIP_DAEMON_NOTIFY", "1")


def _project_results_dir() -> Path:
    """Resolve the project-local results dir from this conftest's location.

    Walks up from this file to find ``litmus.yaml``, reads its
    ``results_dir`` field. We don't use ``resolve_results_dir`` here
    because it'd require importing litmus, which triggers a chain
    of imports before pytest's collector runs — slow startup.
    """
    repo_root = Path(__file__).resolve().parent.parent
    yaml_path = repo_root / "litmus.yaml"
    if yaml_path.exists():
        # Tiny hand-parser: looks for a line ``results_dir: <value>``.
        # Avoids the YAML import cost during pytest startup; the
        # project ``litmus.yaml`` is hand-written and stable.
        for line in yaml_path.read_text().splitlines():
            if line.strip().startswith("results_dir:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                # Strip trailing comment, if any.
                value = value.split("#", 1)[0].strip()
                return (repo_root / value).resolve()
    return repo_root / ".tmp" / "test-results"


_PROJECT_RESULTS = _project_results_dir()

# Pin ``LITMUS_HOME`` so pytester subprocesses (whose CWD is a
# tmp_path with no ``litmus.yaml`` ancestor) inherit the same
# project-local store via the env-var fallback in
# ``resolve_results_dir``. Set to the parent of the results dir
# so ``LITMUS_HOME/results`` matches what the outer pytest gets
# from the repo's ``litmus.yaml``.
os.environ.setdefault("LITMUS_HOME", str(_PROJECT_RESULTS.parent))

# Pre-create the events_dir so the runs daemon's subscription
# wires up on first spawn (see
# ``_runs_duckdb_daemon._start_event_subscriber``).
(_PROJECT_RESULTS / "events").mkdir(parents=True, exist_ok=True)
