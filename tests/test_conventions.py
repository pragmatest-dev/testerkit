"""Repo-wide test conventions enforced as tests.

These exist to keep new tests from quietly bringing back patterns we
took out — specifically the per-test daemon-spawning pattern that
exhausted WSL's pids cgroup. Tests here grep the test tree and fail
when offending patterns reappear, with a pointer to the canonical
replacement.

If you're hitting one of these on a new test you wrote: read the
explanation in the failure message, switch to ``resolve_data_dir()``,
and add the new test file to the allowlist below ONLY if you have a
genuinely good reason (rare).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
_REPO_ROOT = _TESTS_DIR.parent

# Files that legitimately need their own data_dir-shaped path
# (writing parquets to disk, testing the writer itself, etc.) but
# DO NOT spawn daemons. ``ParquetBackend(data_dir=tmp_path)`` is
# fine in conftest because ``LITMUS_SKIP_DAEMON_NOTIFY=1`` blocks
# the daemon-notify hop. Tests that LEGITIMATELY need a daemon
# should use the canonical singleton via ``resolve_data_dir()``.
_DAEMON_SPAWNERS = (
    re.compile(r"\bRunStore\(_data_dir\s*=\s*tmp_path"),
    re.compile(r"\bEventStore\(_data_dir\s*=\s*tmp_path"),
    re.compile(r"\bChannelStore\(\s*tmp_path[^,]*,.*serve\s*=\s*True"),
    re.compile(r"\bStationConnection\([^)]*data_dir\s*=\s*tmp_path"),
    re.compile(r"--data-dir\s*=\s*\{?[^}]*tmp_path"),
    re.compile(r"--data-dir\s*=\s*\{?[^}]*pytester\.path"),
)

_PLATFORMDIRS_HARDCODE = re.compile(r"platformdirs\.user_data_dir\(\s*['\"]litmus['\"]\s*\)")


def _is_doc_line(line: str) -> bool:
    """Heuristic: skip lines that are clearly docstring / comment text.

    Catches RST/markdown-style code references (anything containing
    a double-backtick) and ``#``-prefixed comments. Doesn't try to
    parse multi-line strings perfectly — we just don't want a
    docstring example to fail the lint.
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    # Markdown-style code reference inside a docstring: surrounded by
    # double-backticks. We can't be perfect; this catches the common
    # case of a doc paragraph mentioning the name.
    return "``" in line


def _iter_test_files() -> list[Path]:
    """All ``test_*.py`` files in the test tree, excluding this file."""
    return [p for p in _TESTS_DIR.rglob("test_*.py") if p.resolve() != Path(__file__).resolve()]


def test_no_tmp_path_daemon_spawners():
    """No test passes ``tmp_path`` to a constructor that spawns a daemon.

    Daemons are keyed on ``data_dir``. A fresh ``tmp_path`` per test
    means a fresh daemon per test — each one ~100 gRPC threads. The
    full suite hits WSL's pids cgroup at ~30 such tests.

    Use the canonical singleton instead:

        from litmus.data.data_dir import resolve_data_dir
        canonical = resolve_data_dir()
        store = RunStore()                  # no _data_dir → canonical
        backend = ParquetBackend(data_dir=canonical)

    Per-test isolation by ``run_id`` (uuid4), ``session_id``, or a
    unique ``dut_serial`` / ``product_id`` filter — not by directory.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_test_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _is_doc_line(line):
                continue
            for pattern in _DAEMON_SPAWNERS:
                if pattern.search(line):
                    offenders.append((path.relative_to(_REPO_ROOT), lineno, line.strip()))
                    break
    if offenders:
        msg = "\n".join(f"  {p}:{n}  {line}" for p, n, line in offenders)
        pytest.fail(
            "Tests must not pass ``tmp_path`` to daemon-spawning "
            "constructors. Use ``resolve_data_dir()`` and isolate "
            "by ``run_id`` / ``session_id`` / unique filter values:\n"
            f"{msg}\n\n"
            "Why: every unique ``data_dir`` spawns its own daemon "
            "(~100 gRPC threads). The full suite hits WSL's pids cgroup "
            "and starts SIGKILL'ing daemons mid-write."
        )


def test_no_hardcoded_platformdirs_paths():
    """Test code must not bypass project ``litmus.yaml`` via hardcoded platformdirs.

    Resolution should ALWAYS go through ``resolve_data_dir()`` so
    the repo's project-local ``litmus.yaml`` (``data_dir: data``)
    takes effect. Hardcoding ``platformdirs.user_data_dir("litmus")``
    forces tests onto the operator's global hardware-data store.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_test_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _is_doc_line(line):
                continue
            if _PLATFORMDIRS_HARDCODE.search(line):
                offenders.append((path.relative_to(_REPO_ROOT), lineno, line.strip()))
    if offenders:
        msg = "\n".join(f"  {p}:{n}  {line}" for p, n, line in offenders)
        pytest.fail(
            "Tests must use ``resolve_data_dir()`` instead of "
            'hardcoded ``platformdirs.user_data_dir("litmus")``:\n'
            f"{msg}"
        )


def test_query_clients_read_daemon_not_parquet():
    """Query clients read the daemon index — never re-read parquet.

    A client-side ``read_parquet`` (the old ``StepsQuery._enrich_io``)
    bypasses the daemon's warm index, races the materialize write, and
    breaks the backend swap (a remote backend has no local parquet
    path). Per-vector inputs/outputs live in the index as
    ``dynamic_attrs``; clients read them from the daemon, not the files.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in sorted((_REPO_ROOT / "src" / "litmus" / "analysis").glob("*_query.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _is_doc_line(line):
                continue
            if "read_parquet" in line:
                offenders.append((path.relative_to(_REPO_ROOT), lineno, line.strip()))
    if offenders:
        msg = "\n".join(f"  {p}:{n}  {line}" for p, n, line in offenders)
        pytest.fail(
            "Query clients must read the daemon index, not re-read parquet "
            "(req-2 / req-6, and the #228 projection drift):\n" + msg
        )
