"""``litmus data index list|build|rm|prune`` — runs-index epoch lifecycle tooling
(#53 P4/P5, see docs/_internal/explorations/derived-index-versioning.md §6/§7).

``list``/``rm``/``prune`` operate purely on-disk (glob epoch files + read-only
DuckDB opens + the ``_epochs.json`` ledger) and never spawn a daemon, so they
use fake epoch files built directly with ``duckdb.connect`` in ``tmp_path``
(no daemon-spawning constructor is used — see ``tests/test_conventions.py``).

``build`` is the one command that MUST spawn/warm a real daemon; per
CLAUDE.md's Test Storage Convention, that single integration test uses the
canonical project data dir (``resolve_data_dir()``, this repo's ``litmus.yaml``
points it at ``data/``) instead of a per-test ``tmp_path`` daemon.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
from click.testing import CliRunner

from litmus.cli import main
from litmus.cli.data_cmd import (
    _epoch_size_bytes,
    _format_bytes,
    _fp12_from_index_path,
    _humanize_ago,
    dormant_epoch_hint,
)
from litmus.data import _runs_duckdb_daemon as daemon
from litmus.data.schema_versions import CURRENT_SCHEMA_VERSION, SchemaStore

# A fp12 that is (astronomically) never the real projection fingerprint —
# used to stand in for a "some other version's" non-current epoch.
_OTHER_FP12 = "a1b2c3d4e5f6"
_OTHER_FP12_2 = "112233445566"


def _write_epoch_file(
    path: Path,
    *,
    litmus_version: str,
    schema_version: str,
    fingerprint: str,
    n_runs: int,
) -> None:
    """Build a minimal-but-real epoch file: ``_index_meta`` + ``runs_materialized``,
    exactly the two tables ``litmus data index`` reads. No daemon involved —
    a plain ``duckdb.connect`` write, closed before the CLI reads it back.
    """
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE _index_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        conn.execute(
            "INSERT INTO _index_meta (key, value) VALUES (?, ?), (?, ?), (?, ?), (?, ?)",
            [
                "litmus_version",
                litmus_version,
                "schema_version",
                schema_version,
                "projection_fingerprint",
                fingerprint,
                "built_at",
                datetime.now(UTC).isoformat(),
            ],
        )
        conn.execute("CREATE TABLE runs_materialized (run_id VARCHAR)")
        for i in range(n_runs):
            conn.execute("INSERT INTO runs_materialized VALUES (?)", [f"run-{i}"])
    finally:
        conn.close()


def _write_ledger(runs_dir: Path, entries: dict[str, dict[str, object]]) -> None:
    (runs_dir / "_epochs.json").write_text(json.dumps(entries, indent=2, sort_keys=True))


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) - delta).isoformat()


def _current_fp12() -> str:
    return daemon._projection_fingerprint()[:12]


# ── small helpers (unit-level) ───────────────────────────────────────────


def test_fp12_from_index_path() -> None:
    assert _fp12_from_index_path(Path("_index.a1b2c3d4e5f6.duckdb")) == "a1b2c3d4e5f6"


def test_format_bytes() -> None:
    assert _format_bytes(500) == "500 B"
    assert _format_bytes(2048) == "2.0 KB"
    assert _format_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert _format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"


def test_humanize_ago_buckets() -> None:
    assert _humanize_ago(None) == "unknown"
    assert _humanize_ago("not-a-timestamp") == "unknown"
    assert _humanize_ago(_iso(timedelta(seconds=5))) == "just now"
    assert _humanize_ago(_iso(timedelta(minutes=2))) == "2m ago"
    assert _humanize_ago(_iso(timedelta(hours=3))) == "3h ago"
    assert _humanize_ago(_iso(timedelta(days=6))) == "6 days ago"
    assert _humanize_ago(_iso(timedelta(days=1))) == "1 day ago"


def test_epoch_size_bytes_includes_wal(tmp_path: Path) -> None:
    db = tmp_path / "_index.abc123456789.duckdb"
    db.write_bytes(b"x" * 100)
    without_wal = _epoch_size_bytes(db)
    assert without_wal == 100

    wal = Path(f"{db}.wal")
    wal.write_bytes(b"y" * 50)
    assert _epoch_size_bytes(db) == 150


# ── list ──────────────────────────────────────────────────────────────────


def test_list_empty_state(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No index epochs yet" in result.output
    assert "litmus data index build" in result.output


def test_list_renders_current_marker_seen_by_and_footer(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)

    current_fp12 = _current_fp12()
    schema = CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]

    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version=schema,
        fingerprint=current_fp12 + "f" * 52,
        n_runs=3,
    )
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=5,
    )
    _write_ledger(
        runs_dir,
        {
            current_fp12: {"seen_by": ["0.3.1"], "last_seen": _iso(timedelta(minutes=2))},
            _OTHER_FP12: {
                "seen_by": ["0.2.4", "0.3.0"],
                "last_seen": _iso(timedelta(days=6)),
            },
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    out = result.output

    assert "FINGERPRINT" in out and "SEEN BY" in out and "LAST SEEN" in out
    current_line = next(line for line in out.splitlines() if current_fp12 in line)
    assert current_line.lstrip().startswith("*")  # current-marker on the current row
    other_line = next(line for line in out.splitlines() if _OTHER_FP12 in line)
    assert not other_line.lstrip().startswith("*")  # no marker on the non-current row
    assert "0.2.4, 0.3.0" in out  # SEEN BY set, sorted, comma-joined
    assert "2m ago" in out
    assert "6 days ago" in out
    assert "2 index files" in out
    assert f"current = {current_fp12} (" in out
    # current epoch (last seen 2m ago) sorts before the 6-day-old one
    assert out.index(current_fp12) < out.index(_OTHER_FP12)


def test_dormant_epoch_hint(tmp_path: Path) -> None:
    """The setup-time hint counts only NON-current epochs, and is None when
    there are none (empty dir or current-only)."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    current_fp12 = _current_fp12()

    assert dormant_epoch_hint(str(tmp_path)) is None  # empty → no hint

    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version="0.1",
        fingerprint=current_fp12 + "f" * 52,
        n_runs=1,
    )
    assert dormant_epoch_hint(str(tmp_path)) is None  # current-only → no hint

    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )
    hint = dormant_epoch_hint(str(tmp_path))
    assert hint is not None
    assert "1 older index epoch" in hint
    assert "litmus data index prune" in hint


def test_list_falls_back_to_unknown_seen_by_without_ledger(tmp_path: Path) -> None:
    """No ledger entry at all: SEEN BY falls back to the file's own BUILT BY."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "0.3.0" in result.output  # BUILT BY and the SEEN BY fallback
    assert "unknown" in result.output  # LAST SEEN, no ledger entry


# ── prune ────────────────────────────────────────────────────────────────────


def _prune_fixture(tmp_path: Path) -> tuple[Path, str]:
    """Current epoch (very old last_seen — must survive anyway) + two
    old/stale non-current epochs + ledger. Returns (data_dir, current_fp12)."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    current_fp12 = _current_fp12()

    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version="0.1",
        fingerprint=current_fp12 + "f" * 52,
        n_runs=1,
    )
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12_2}.duckdb",
        litmus_version="0.2.9",
        schema_version="0.1",
        fingerprint=_OTHER_FP12_2 + "0" * 52,
        n_runs=1,
    )
    _write_ledger(
        runs_dir,
        {
            # Current: ancient last_seen — must be kept anyway (never reaped).
            current_fp12: {"seen_by": ["0.3.1"], "last_seen": _iso(timedelta(days=400))},
            _OTHER_FP12: {"seen_by": ["0.3.0"], "last_seen": _iso(timedelta(days=90))},
            _OTHER_FP12_2: {"seen_by": ["0.2.9"], "last_seen": _iso(timedelta(days=91))},
        },
    )
    return tmp_path, current_fp12


def test_prune_no_epochs(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "prune", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "No index epochs to prune" in result.output


def test_prune_dry_run_reaps_old_keeps_current(tmp_path: Path) -> None:
    data_dir, current_fp12 = _prune_fixture(tmp_path)
    runs_dir = data_dir / "runs"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data",
            "index",
            "prune",
            "--data-dir",
            str(data_dir),
            "--keep-last",
            "0",
            "--older-than",
            "30d",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "current epoch" in result.output
    assert "Would reclaim" in result.output
    assert _OTHER_FP12 in result.output
    assert _OTHER_FP12_2 in result.output
    # dry-run deletes nothing
    assert (runs_dir / f"_index.{current_fp12}.duckdb").exists()
    assert (runs_dir / f"_index.{_OTHER_FP12}.duckdb").exists()
    assert (runs_dir / f"_index.{_OTHER_FP12_2}.duckdb").exists()


def test_prune_actually_reaps(tmp_path: Path) -> None:
    data_dir, current_fp12 = _prune_fixture(tmp_path)
    runs_dir = data_dir / "runs"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data",
            "index",
            "prune",
            "--data-dir",
            str(data_dir),
            "--keep-last",
            "0",
            "--older-than",
            "30d",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Removed 2 epoch(s)" in result.output

    assert (runs_dir / f"_index.{current_fp12}.duckdb").exists()  # current survives
    assert not (runs_dir / f"_index.{_OTHER_FP12}.duckdb").exists()
    assert not (runs_dir / f"_index.{_OTHER_FP12_2}.duckdb").exists()

    ledger = json.loads((runs_dir / "_epochs.json").read_text())
    assert set(ledger) == {current_fp12}  # reaped entries removed too


def test_prune_keep_last_overrides_older_than(tmp_path: Path) -> None:
    """--keep-last N keeps the N most-recently-seen epochs even past --older-than."""
    data_dir, current_fp12 = _prune_fixture(tmp_path)
    runs_dir = data_dir / "runs"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data",
            "index",
            "prune",
            "--data-dir",
            str(data_dir),
            "--keep-last",
            "1",
            "--older-than",
            "30d",
        ],
    )
    assert result.exit_code == 0, result.output
    # _OTHER_FP12 (90d, more recent of the two) is kept by --keep-last 1;
    # _OTHER_FP12_2 (91d) is reaped.
    assert (runs_dir / f"_index.{current_fp12}.duckdb").exists()
    assert (runs_dir / f"_index.{_OTHER_FP12}.duckdb").exists()
    assert not (runs_dir / f"_index.{_OTHER_FP12_2}.duckdb").exists()


def test_prune_never_reaps_unknown_age(tmp_path: Path) -> None:
    """An epoch with no ledger entry at all is never reaped (unknowable age)."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    current_fp12 = _current_fp12()
    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version="0.1",
        fingerprint=current_fp12 + "f" * 52,
        n_runs=1,
    )
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )
    # No _epochs.json at all — _OTHER_FP12 has unknowable age.

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "data",
            "index",
            "prune",
            "--data-dir",
            str(tmp_path),
            "--keep-last",
            "0",
            "--older-than",
            "0d",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "unknown age" in result.output
    assert (runs_dir / f"_index.{_OTHER_FP12}.duckdb").exists()


# ── rm ────────────────────────────────────────────────────────────────────


def test_rm_no_match(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["data", "index", "rm", "deadbeef0000", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "No index epoch matches" in result.output


def test_rm_ambiguous_prefix(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    for fp12 in ("aaaa11111111", "aaaa22222222"):
        _write_epoch_file(
            runs_dir / f"_index.{fp12}.duckdb",
            litmus_version="0.3.0",
            schema_version="0.1",
            fingerprint=fp12 + "0" * 52,
            n_runs=1,
        )
    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "rm", "aaaa", "--data-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "ambiguous" in result.output


def test_rm_removes_noncurrent_epoch(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    _write_epoch_file(
        runs_dir / f"_index.{_OTHER_FP12}.duckdb",
        litmus_version="0.3.0",
        schema_version="0.1",
        fingerprint=_OTHER_FP12 + "0" * 52,
        n_runs=1,
    )
    _write_ledger(
        runs_dir,
        {_OTHER_FP12: {"seen_by": ["0.3.0"], "last_seen": _iso(timedelta(days=1))}},
    )

    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "rm", _OTHER_FP12, "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Removed index epoch" in result.output
    assert not (runs_dir / f"_index.{_OTHER_FP12}.duckdb").exists()
    ledger = json.loads((runs_dir / "_epochs.json").read_text())
    assert _OTHER_FP12 not in ledger


def test_rm_refuses_current_without_force(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    current_fp12 = _current_fp12()
    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version="0.1",
        fingerprint=current_fp12 + "f" * 52,
        n_runs=1,
    )
    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "rm", current_fp12, "--data-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "CURRENT epoch" in result.output
    assert (runs_dir / f"_index.{current_fp12}.duckdb").exists()


def test_rm_force_removes_current(tmp_path: Path) -> None:
    """``--force`` on the current epoch: no daemon is running for this
    tmp_path, so ``force_restart()`` finds no state file and no-ops (never
    spawns) — this exercises the force path without a live daemon."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)
    current_fp12 = _current_fp12()
    _write_epoch_file(
        runs_dir / f"_index.{current_fp12}.duckdb",
        litmus_version="0.3.1",
        schema_version="0.1",
        fingerprint=current_fp12 + "f" * 52,
        n_runs=1,
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["data", "index", "rm", current_fp12, "--force", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert not (runs_dir / f"_index.{current_fp12}.duckdb").exists()


# ── build (integration — the one command that must spawn/warm a daemon) ──


def test_build_warmth_counts_quarantined_as_reconciled(tmp_path: Path) -> None:
    """Regression: `build`'s warmth poll must count *terminal* `_ingested` states
    (ok + quarantined), not just `ok`. A quarantined file (incompatible schema)
    never becomes `ok`, so an `ok >= disk_count` gate is unreachable and would
    spin the poll to its full deadline while the daemon sits idle — the exact
    bug that made a 4.5s rebuild masquerade as a 120s one.
    """
    db = tmp_path / "idx.duckdb"
    conn = duckdb.connect(str(db))
    try:
        conn.execute("CREATE TABLE _ingested (path VARCHAR, status VARCHAR)")
        conn.executemany(
            "INSERT INTO _ingested VALUES (?, ?)",
            [(f"f{i}", "ok") for i in range(242)]
            + [("bad1", "quarantined"), ("bad2", "quarantined")],
        )
        row = conn.execute(
            "SELECT count(*) AS reconciled, "
            "count(*) FILTER (WHERE status = 'ok') AS ok FROM _ingested"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    reconciled, ok = row
    disk_count = 244
    assert reconciled == disk_count  # terminal states reach disk_count → warm
    assert ok == 242  # 'ok' alone never reaches 244 → would spin forever (the bug)


def test_build_warms_the_canonical_runs_index() -> None:
    """Uses the canonical project data dir (this repo's litmus.yaml → ``data/``),
    the same shared singleton daemon every other CLI test in the suite uses
    (e.g. tests/test_yield/test_cli.py) — NOT a per-test ``tmp_path`` daemon.

    Doesn't pass ``--rebuild`` (that would discard the shared canonical index
    every other concurrently-run test also depends on); a plain ``build``
    blocks until warm and is idempotent, so this is safe to run any time.
    """
    runner = CliRunner()
    result = runner.invoke(main, ["data", "index", "build"])
    assert result.exit_code == 0, result.output
    assert "runs index from" in result.output
    assert "(fp " in result.output
    assert "schema" in result.output
