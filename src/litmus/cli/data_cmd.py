"""Data retention and management commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import click

from litmus.cli._common import _get_data_dir
from litmus.cli.root import main


@main.group()
def data():
    """Data retention and management."""
    pass


@data.command("prune")
@click.option("--older-than", required=True, help="Retention period (e.g. 30d, 90d)")
@click.option(
    "--type",
    "data_types",
    multiple=True,
    help="Data types to prune (e.g. channels, files, events)",
)
@click.option("--data-dir", default=None, help="Results directory")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option(
    "--ext",
    "exts",
    multiple=True,
    help="Only prune files with these extensions (tiered retention, e.g. --ext tdms). Files only.",
)
def data_prune(
    older_than: str,
    data_types: tuple[str, ...],
    data_dir: str | None,
    dry_run: bool,
    exts: tuple[str, ...],
) -> None:
    """Delete date-partitioned data older than the specified period.

    Channels and files prune reference-aware (data a run references is kept);
    ``--ext`` further limits file pruning to specific types.
    """
    from litmus.data.retention import prune_all

    data_dir_path = Path(_get_data_dir(data_dir))

    types = data_types or ("channels", "files", "events")
    ext_filter = frozenset(e.lower().lstrip(".") for e in exts) or None
    try:
        result = prune_all(
            data_dir_path, older_than, data_types=types, dry_run=dry_run, exts=ext_filter
        )
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="'--older-than'") from e

    total = 0
    for subdir, paths in result.items():
        for p in paths:
            prefix = "[dry-run] " if dry_run else ""
            click.echo(f"{prefix}Removed {subdir}/{p.name}")
            total += 1
    if total == 0:
        click.echo("Nothing to prune.")
    elif dry_run:
        click.echo(f"\n{total} items would be removed.")
    else:
        click.echo(f"\n{total} items removed.")


# Starter sentinels — runs whose part / station / serial / fixture
# matches any of these are scaffold/example runs, skipped by default.
# The user can opt them in with --include-starter.
_STARTER_PART_IDS = {"example_part"}
_STARTER_STATION_IDS = {"starter_station"}
_STARTER_FIXTURE_IDS = {"example_fixture"}
_STARTER_UUT_SERIALS = {"STARTER001", "SMOKE001"}


def _is_starter_parquet(parquet_path: Path) -> bool:
    """Return True if the parquet's first row matches any starter sentinel.

    Reads only the columns needed; one parquet = one run = small file.
    """
    import pyarrow.parquet as pq

    cols = ["part_id", "station_id", "uut_serial_number", "fixture_id"]
    try:
        t = pq.read_table(parquet_path, columns=cols)
    except (FileNotFoundError, OSError, KeyError):
        return False
    if t.num_rows == 0:
        return False
    row0 = {c: t[c][0].as_py() for c in cols if c in t.column_names}
    if row0.get("part_id") in _STARTER_PART_IDS:
        return True
    if row0.get("station_id") in _STARTER_STATION_IDS:
        return True
    if row0.get("uut_serial_number") in _STARTER_UUT_SERIALS:
        return True
    if row0.get("fixture_id") in _STARTER_FIXTURE_IDS:
        return True
    return False


def _copy_run_references(
    src_parquet: Path, src_data: Path, dst_data: Path, *, with_events: bool
) -> tuple[int, int]:
    """Copy the channel slices + files a run references into the destination store.

    Keeps the run whole in the global store (no dangling refs). ``file://`` keys
    copy by exact path (+ ``.meta.json`` sidecar so the catalog rebuilds); channel
    slices copy every matching segment. ``with_events`` also carries each session's
    event timeline. Returns ``(channel_segments_copied, files_copied)``.
    """
    import shutil

    from litmus.data.backends.parquet import extract_refs

    channels, files = extract_refs(src_parquet)
    sessions = {sid for _, sid in channels}

    def _copy(rel: Path) -> bool:
        s, d = src_data / rel, dst_data / rel
        if not s.exists() or d.exists():
            return False
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        return True

    n_files = 0
    for key in files:
        if _copy(Path("files") / key):
            n_files += 1
        _copy(Path("files") / f"{key}.meta.json")  # sidecar → catalog rebuild
        parts = key.split("/")
        if len(parts) >= 2:
            sessions.add(parts[1])  # file://{date}/{session}/{filename}

    n_chan = 0
    chan_root = src_data / "channels"
    if chan_root.is_dir():
        for cid, sid in channels:
            for seg in chan_root.glob(f"*/{cid}_{sid[:8]}*.arrow"):
                if _copy(seg.relative_to(src_data)):
                    n_chan += 1

    if with_events:
        ev_root = src_data / "events"
        if ev_root.is_dir():
            for sid in sessions:
                for ev in ev_root.glob(f"*/{sid}-*.arrow"):
                    _copy(ev.relative_to(src_data))
    return n_chan, n_files


def _global_data_dir() -> Path:
    """Resolve the platformdirs global data directory.

    Mirrors the fallback in litmus.data.data_dir.resolve_data_dir but
    ignores any project override — promote always targets the global
    store regardless of where the current cwd points.
    """
    import os

    import platformdirs

    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    return home / "data"


@data.command("promote")
@click.option(
    "--include-starter",
    is_flag=True,
    help="Also promote runs that match starter sentinels "
    "(example_part / starter_station / STARTER001 / etc.). "
    "Default skips these as throwaway learning runs.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be promoted; write nothing.")
@click.option(
    "--with-events",
    is_flag=True,
    help="Also carry each run's session event timeline (audit-grade archive).",
)
def data_promote(include_starter: bool, dry_run: bool, with_events: bool) -> None:
    """Move a starter project's local runs + their referenced data to the global store.

    Starter projects ship with ``data_dir: data`` in litmus.yaml so
    learning runs (mock instruments, example_part, STARTER001, etc.)
    don't pollute the platformdirs global store shared across projects
    on this machine. When you're ready to share data across projects,
    `litmus data promote` copies non-starter runs **plus the channel/file
    data they reference** into the global store (the runs stay whole — no
    dangling refs), and removes the ``data_dir`` override from your
    litmus.yaml. ``--with-events`` also carries each run's session events.

    Idempotent. Re-running promote after adding the flag picks up
    anything previously skipped.
    """
    import shutil

    from ruamel.yaml import YAML

    from litmus.connect import _find_project_config

    found = _find_project_config()
    if not found:
        raise click.ClickException(
            "No litmus.yaml found in this directory or any parent. "
            "`litmus data promote` runs inside a project directory."
        )
    project_root, project = found

    if not project.data_dir:
        raise click.ClickException(
            "This project's litmus.yaml has no `data_dir` override, "
            "so it's already using the global store. Nothing to promote."
        )

    src_data = (project_root / project.data_dir).resolve()
    dst_data = _global_data_dir().resolve()
    if src_data == dst_data:
        raise click.ClickException(
            f"Project data_dir resolves to the global store ({src_data}); nothing to promote."
        )

    src_runs_root = src_data / "runs" / "runs"
    if not src_runs_root.exists():
        click.echo(f"No runs found under {src_runs_root}; nothing to promote.")
        return

    parquets = sorted(src_runs_root.glob("*/*.parquet"))
    if not parquets:
        click.echo(f"No parquet files under {src_runs_root}; nothing to promote.")
        return

    to_copy: list[tuple[Path, Path]] = []
    skipped_starter = 0
    skipped_collision = 0

    for src in parquets:
        is_starter = _is_starter_parquet(src)
        if is_starter and not include_starter:
            skipped_starter += 1
            continue
        # dst preserves the YYYY-MM-DD subdir from the source path.
        rel = src.relative_to(src_runs_root)
        dst = dst_data / "runs" / "runs" / rel
        if dst.exists():
            skipped_collision += 1
            continue
        to_copy.append((src, dst))

    click.echo(f"Source:      {src_data}")
    click.echo(f"Destination: {dst_data}")
    click.echo("")
    click.echo(f"Found {len(parquets)} run parquets total.")
    if skipped_starter:
        flag_hint = "" if include_starter else " (use --include-starter to include)"
        click.echo(f"  {skipped_starter} starter / example run(s) — skipped{flag_hint}")
    if skipped_collision:
        click.echo(f"  {skipped_collision} already in global store — skipped (idempotent)")
    click.echo(f"  {len(to_copy)} to promote")

    if dry_run:
        click.echo("\n[dry-run] No files copied. litmus.yaml unchanged.")
        return

    if not to_copy:
        click.echo("\nNothing to promote. litmus.yaml unchanged.")
        return

    # Copy each run parquet + the channel/file data it references, so the run
    # stays whole in the global store (no dangling refs). Each is independent —
    # failures don't roll back, but files are skipped on collision so re-running
    # is safe.
    total_chan = total_files = 0
    for src, dst in to_copy:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        nc, nf = _copy_run_references(src, src_data, dst_data, with_events=with_events)
        total_chan += nc
        total_files += nf
    click.echo(
        f"\nCopied {len(to_copy)} run parquet(s) + {total_chan} channel segment(s) "
        f"+ {total_files} file(s) to {dst_data}/"
    )
    if with_events:
        click.echo("Carried each run's session event timeline (--with-events).")

    # Update litmus.yaml — drop the data_dir override so future runs
    # and queries from this project use the global store. Uses ruamel
    # to preserve formatting + comments.
    litmus_yaml = project_root / "litmus.yaml"
    yaml = YAML()
    yaml.preserve_quotes = True
    with litmus_yaml.open() as f:
        doc = yaml.load(f)
    if "data_dir" in doc:
        del doc["data_dir"]
        with litmus_yaml.open("w") as f:
            yaml.dump(doc, f)
        try:
            display_path = litmus_yaml.relative_to(Path.cwd())
        except ValueError:
            display_path = litmus_yaml
        click.echo(f"Updated {display_path}: removed `data_dir` override.")

    click.echo("")
    click.echo("Future runs and `litmus runs` queries from this project now use the global store.")
    click.echo(
        "The global store now holds the promoted runs + their referenced "
        "channel/file data, so they resolve there."
    )
    click.echo(
        f"The local {src_data} still has unpromoted/starter runs; remove it once "
        f"you've verified the global store:  rm -rf {src_data}"
    )


@data.command("reindex")
@click.option("--data-dir", default=None, help="Results directory")
def data_reindex(data_dir: str | None) -> None:
    """Kill index daemons and rebuild on next access.

    Use this when the index is out of date (e.g. after upgrading litmus).
    """
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    results = Path(_get_data_dir(data_dir))

    for subdir, mgr_cls in [
        ("events", DuckDBDaemonManager),
        ("runs", RunsDuckDBManager),
    ]:
        d = results / subdir
        if d.exists():
            mgr_cls(d).force_restart()
            # Glob covers both the events single-file index (``_index.duckdb``)
            # and the runs content-addressed epoch files (``_index.<fp>.duckdb``,
            # #53 P1) — force-drop every epoch so the daemon rebuilds fresh.
            for idx in d.glob("_index*.duckdb"):
                idx.unlink(missing_ok=True)
                Path(f"{idx}.wal").unlink(missing_ok=True)

    click.echo("Index daemons stopped. Index will rebuild on next query.")


# ── litmus data index — derived-index (DuckDB) epoch lifecycle (#53 P4/P5) ──
#
# See docs/_internal/explorations/derived-index-versioning.md §6 (retention)
# and §7 (this tooling). The runs derived index lives at content-addressed
# files ``<data_dir>/runs/_index.<fp12>.duckdb`` (P1, already shipped);
# multiple epochs can coexist. These commands render/build/prune them by
# human-recognizable identity, sourcing provenance from each file's
# ``_index_meta`` table and last-access from the shared ``_epochs.json``
# ledger — never by re-reading parquet directly.


def _fp12_from_index_path(path: Path) -> str:
    """Extract the 12-char fingerprint prefix from ``_index.<fp12>.duckdb``."""
    parts = path.name.split(".")
    return parts[1] if len(parts) >= 3 else path.stem


def _epoch_size_bytes(path: Path) -> int:
    """Epoch file size in bytes, including its ``.wal`` sidecar if present."""
    total = path.stat().st_size
    wal = Path(f"{path}.wal")
    if wal.exists():
        total += wal.stat().st_size
    return total


def _format_bytes(n: int) -> str:
    """Human-readable byte count (B/KB/MB/GB) for ``litmus data index`` output.

    No repo-wide byte-size helper is reusable here without pulling in
    ``litmus.ui`` (NiceGUI) as a CLI-time import (``format_file_size`` in
    ``litmus.ui.shared.components``) — this is a small local equivalent,
    same thresholds/precision.
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _humanize_ago(iso_ts: str | None) -> str:
    """Render an ISO-8601 UTC timestamp as a short relative string.

    E.g. "2m ago", "6 days ago". No existing relative-"ago" humanizer was
    found in the repo (``event_timeline._relative_time`` renders T+Ns
    run-relative offsets, a different job) — this is a small local one.
    Returns "unknown" for a missing/unparseable timestamp.
    """
    if not iso_ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    seconds = max((datetime.now(UTC) - dt).total_seconds(), 0.0)
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        n = int(days)
        return f"{n} day{'s' if n != 1 else ''} ago"
    months = days / 30
    if months < 12:
        n = int(months)
        return f"{n} month{'s' if n != 1 else ''} ago"
    years = int(days / 365)
    return f"{years} year{'s' if years != 1 else ''} ago"


def _read_epoch_meta_readonly(path: Path) -> tuple[dict[str, str], int | None] | None:
    """Try a direct read-only open of an epoch file for provenance + row count.

    Returns ``None`` when DuckDB can't open it — the only expected case is
    the CURRENT epoch, whose daemon holds an exclusive lock on the file
    while running (see derived-index-versioning.md). The caller falls back
    to the daemon's Flight SQL surface for that case.
    """
    import duckdb

    from litmus.data._runs_duckdb_daemon import _read_index_meta

    try:
        conn = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return None
    try:
        meta = _read_index_meta(conn)
        try:
            row = conn.execute("SELECT count(*) FROM runs_materialized").fetchone()
            n_runs = int(row[0]) if row is not None else None
        except duckdb.Error:
            n_runs = None
        return meta, n_runs
    finally:
        conn.close()


def _read_epoch_meta_via_daemon(runs_dir: Path) -> tuple[dict[str, str], int | None]:
    """Read the CURRENT epoch's provenance + row count via the running daemon's
    Flight SQL surface — the only path available while its exclusive file lock
    is held. ``acquire`` starts the daemon if it isn't already running (idle
    timeout expired), which drops the lock and lets a direct open succeed next
    time; querying through Flight here works either way.
    """
    from litmus.data import runs_duckdb_manager
    from litmus.data._flight_query import FlightQueryClient

    location = runs_duckdb_manager.acquire(runs_dir)
    client = FlightQueryClient(
        location,
        "runs",
        reacquire=lambda: runs_duckdb_manager.acquire(runs_dir),
        label="litmus data index",
    )
    meta_rows = client.query("SELECT key, value FROM _index_meta")
    meta = {str(r["key"]): str(r["value"]) for r in meta_rows}
    count_rows = client.query("SELECT count(*) AS n FROM runs_materialized")
    n_runs = int(count_rows[0]["n"]) if count_rows else None
    return meta, n_runs


@data.group("index")
def data_index() -> None:
    """Runs-index (DuckDB) epoch lifecycle tooling.

    The runs derived index is a content-addressed, always-rebuildable cache
    over durable parquet (see derived-index-versioning.md). These commands
    build/warm it, list its epochs by human identity, drop one, or prune
    stale ones.
    """


@dataclass
class _EpochRow:
    """One rendered row for ``litmus data index list`` — display-only, never persisted."""

    current: bool
    fp12: str
    schema: str
    built_by: str
    seen_by: str
    runs: str
    size_bytes: int
    last_seen_iso: str | None


def _epoch_row_sort_key(row: _EpochRow) -> datetime:
    iso = row.last_seen_iso
    if not iso:
        return datetime.min.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@data_index.command("list")
@click.option("--data-dir", default=None, help="Results directory")
def data_index_list(data_dir: str | None) -> None:
    """List every runs-index epoch by fingerprint, versions, rows, size, last seen."""
    from litmus.data._daemon_lifecycle import _installed_version
    from litmus.data._runs_duckdb_daemon import _projection_fingerprint, _read_epochs_ledger

    data_dir_path = Path(_get_data_dir(data_dir))
    runs_dir = data_dir_path / "runs"
    epoch_files = sorted(runs_dir.glob("_index.*.duckdb")) if runs_dir.is_dir() else []

    if not epoch_files:
        click.echo(
            "No index epochs yet — run `litmus data index build` or any query to create one."
        )
        return

    current_fp12 = _projection_fingerprint()[:12]
    ledger = _read_epochs_ledger(runs_dir)

    rows: list[_EpochRow] = []
    for path in epoch_files:
        fp12 = _fp12_from_index_path(path)
        is_current = fp12 == current_fp12
        result = _read_epoch_meta_readonly(path)
        if result is None:
            result = _read_epoch_meta_via_daemon(runs_dir) if is_current else ({}, None)
        meta, n_runs = result
        ledger_entry = ledger.get(fp12, {})
        seen_by = ledger_entry.get("seen_by") or (
            [meta["litmus_version"]] if meta.get("litmus_version") else []
        )
        rows.append(
            _EpochRow(
                current=is_current,
                fp12=fp12,
                schema=meta.get("schema_version", "?"),
                built_by=meta.get("litmus_version", "?"),
                seen_by=", ".join(seen_by) if seen_by else "?",
                runs=f"{n_runs:,}" if n_runs is not None else "?",
                size_bytes=_epoch_size_bytes(path),
                last_seen_iso=ledger_entry.get("last_seen"),
            )
        )

    rows.sort(key=_epoch_row_sort_key, reverse=True)

    headers = ["", "FINGERPRINT", "SCHEMA", "BUILT BY", "SEEN BY", "RUNS", "SIZE", "LAST SEEN"]
    table_rows = [
        [
            "*" if r.current else " ",
            r.fp12,
            r.schema,
            r.built_by,
            r.seen_by,
            r.runs,
            _format_bytes(r.size_bytes),
            _humanize_ago(r.last_seen_iso),
        ]
        for r in rows
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in table_rows)) for i in range(len(headers))
    ]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip())
    for row in table_rows:
        click.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())

    total_size = sum(r.size_bytes for r in rows)
    click.echo(
        f"\n{len(rows)} index file{'s' if len(rows) != 1 else ''} · "
        f"{_format_bytes(total_size)} total · current = {current_fp12} ({_installed_version()})"
    )


@data_index.command("build")
@click.option("--data-dir", default=None, help="Results directory")
@click.option(
    "--rebuild",
    is_flag=True,
    help="Discard the current epoch first, so it rebuilds fresh from parquet.",
)
@click.option(
    "--background",
    is_flag=True,
    help="Start the daemon and return immediately; don't block until warm.",
)
def data_index_build(data_dir: str | None, rebuild: bool, background: bool) -> None:
    """Eagerly build/warm the CURRENT runs-index epoch (blocks until warm).

    Idempotent: an already-warm index reports near-instant with 0 new files
    ingested. Copy-seed birth (a cheap rescan-free path) is not built yet
    (#53 P2) — this always warms via a full rescan from parquet.
    """
    import time as _time

    from litmus.data import runs_duckdb_manager
    from litmus.data._flight_query import FlightQueryClient
    from litmus.data._runs_duckdb_daemon import (
        _current_provenance,
        _index_file_name,
        _projection_fingerprint,
    )
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    data_dir_path = Path(_get_data_dir(data_dir))
    runs_dir = data_dir_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = _projection_fingerprint()
    fp12 = fingerprint[:12]
    index_path = runs_dir / _index_file_name(fingerprint)

    if rebuild:
        RunsDuckDBManager(runs_dir).force_restart()
        index_path.unlink(missing_ok=True)
        Path(f"{index_path}.wal").unlink(missing_ok=True)
        click.echo(f"Discarded current epoch {fp12} for a full rebuild (--rebuild).")

    disk_count = sum(1 for p in runs_dir.rglob("*.parquet") if not p.name.endswith(".tmp.parquet"))

    started = _time.monotonic()
    location = runs_duckdb_manager.acquire(runs_dir)

    if background:
        click.echo("Index build started in the background (daemon warming).")
        return

    client = FlightQueryClient(
        location,
        "runs",
        reacquire=lambda: runs_duckdb_manager.acquire(runs_dir),
        label="litmus data index build",
    )

    def _ingest_counts() -> tuple[int, int]:
        # (reconciled, ok). ``reconciled`` = every file that reached a TERMINAL
        # ``_ingested`` state — ``ok`` OR ``quarantined`` (an incompatible schema
        # version, which never becomes ``ok``). Warmth MUST wait on *reconciled*,
        # not ``ok``: an ``ok >= disk_count`` gate can never be satisfied when any
        # file is quarantined, so it would spin the poll to its full deadline and
        # print a false "still warming" while the daemon sits idle. ``ok`` is
        # reported separately as the count successfully indexed.
        rows = client.query(
            "SELECT count(*) AS reconciled, "
            "count(*) FILTER (WHERE status = 'ok') AS ok FROM _ingested"
        )
        if not rows:
            return 0, 0
        return int(rows[0]["reconciled"]), int(rows[0]["ok"])

    initial_reconciled, initial_ok = _ingest_counts()
    already_warm = initial_reconciled >= disk_count

    timeout_s = 120.0
    poll_interval_s = 0.5
    deadline = _time.monotonic() + timeout_s
    reconciled, ingested_ok = initial_reconciled, initial_ok
    timed_out = False
    while reconciled < disk_count:
        if _time.monotonic() >= deadline:
            timed_out = True
            break
        _time.sleep(poll_interval_s)
        reconciled, ingested_ok = _ingest_counts()

    elapsed = _time.monotonic() - started
    _litmus_version, schema_version, _fp = _current_provenance()
    quarantined = max(reconciled - ingested_ok, 0)
    verb = "Rebuilt" if rebuild else "Built"
    click.echo(
        f"{verb} runs index from {disk_count} parquet file{'s' if disk_count != 1 else ''} "
        f"in {elapsed:.1f}s (fp {fp12}, schema {schema_version})."
    )
    if already_warm and not rebuild:
        click.echo("Already warm — 0 new files ingested.")
    else:
        new_files = max(ingested_ok - initial_ok, 0)
        note = f" ({quarantined} quarantined — incompatible schema)" if quarantined else ""
        click.echo(f"{new_files} new file(s) indexed this run{note}.")
    if timed_out:
        click.echo(
            f"Warning: did not finish warming within {timeout_s:.0f}s "
            f"({reconciled}/{disk_count} parquet files reconciled so far) — "
            "the daemon continues warming in the background."
        )


@data_index.command("rm")
@click.argument("fingerprint")
@click.option("--data-dir", default=None, help="Results directory")
@click.option(
    "--force",
    is_flag=True,
    help="Also remove the CURRENT epoch (restarts its daemon first to release the lock).",
)
def data_index_rm(fingerprint: str, data_dir: str | None, force: bool) -> None:
    """Delete one runs-index epoch file by fingerprint prefix."""
    from litmus.data._runs_duckdb_daemon import (
        _projection_fingerprint,
        _remove_epochs_ledger_entries,
    )
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    data_dir_path = Path(_get_data_dir(data_dir))
    runs_dir = data_dir_path / "runs"
    epoch_files = sorted(runs_dir.glob("_index.*.duckdb")) if runs_dir.is_dir() else []

    matches = [p for p in epoch_files if _fp12_from_index_path(p).startswith(fingerprint)]
    if not matches:
        raise click.ClickException(
            f"No index epoch matches fingerprint prefix '{fingerprint}'. "
            "Run `litmus data index list` to see what's available."
        )
    if len(matches) > 1:
        names = ", ".join(_fp12_from_index_path(p) for p in matches)
        raise click.ClickException(
            f"Fingerprint prefix '{fingerprint}' is ambiguous — matches "
            f"{len(matches)} epochs: {names}. Use a longer prefix."
        )

    path = matches[0]
    fp12 = _fp12_from_index_path(path)
    current_fp12 = _projection_fingerprint()[:12]

    if fp12 == current_fp12 and not force:
        raise click.ClickException(
            f"Refusing to remove {fp12} — it's the CURRENT epoch (the active "
            "daemon serves it). Pass --force to remove it anyway (its daemon "
            "is restarted first), or use `litmus data index build --rebuild` instead."
        )
    if fp12 == current_fp12 and force:
        RunsDuckDBManager(runs_dir).force_restart()

    path.unlink(missing_ok=True)
    Path(f"{path}.wal").unlink(missing_ok=True)
    _remove_epochs_ledger_entries(runs_dir, {fp12})
    click.echo(f"Removed index epoch {fp12} ({path.name}).")


_DEFAULT_PRUNE_OLDER_THAN = "30d"
_DEFAULT_PRUNE_KEEP_LAST = 3


@data_index.command("prune")
@click.option("--data-dir", default=None, help="Results directory")
@click.option(
    "--keep-last",
    default=_DEFAULT_PRUNE_KEEP_LAST,
    show_default=True,
    help="Always keep at least this many most-recently-seen epochs.",
)
@click.option(
    "--older-than",
    default=_DEFAULT_PRUNE_OLDER_THAN,
    show_default=True,
    help="Never remove an epoch last seen more recently than this (e.g. 30d).",
)
@click.option("--dry-run", is_flag=True, help="Show what would be removed; delete nothing.")
def data_index_prune(data_dir: str | None, keep_last: int, older_than: str, dry_run: bool) -> None:
    """Remove stale runs-index epochs by last-access (never the current epoch).

    Removal is never a data-loss risk: a removed epoch simply rebuilds from
    parquet if that version runs again (the derived index is a pure cache).
    Mirrors ``litmus data prune`` (durable data) — same verb, index layer.
    """
    from litmus.data._runs_duckdb_daemon import (
        _projection_fingerprint,
        _read_epochs_ledger,
        _remove_epochs_ledger_entries,
    )
    from litmus.data.retention import parse_duration

    data_dir_path = Path(_get_data_dir(data_dir))
    runs_dir = data_dir_path / "runs"
    epoch_files = sorted(runs_dir.glob("_index.*.duckdb")) if runs_dir.is_dir() else []
    if not epoch_files:
        click.echo("No index epochs to prune.")
        return

    try:
        cutoff_delta = parse_duration(older_than)
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="'--older-than'") from e

    current_fp12 = _projection_fingerprint()[:12]
    ledger = _read_epochs_ledger(runs_dir)
    cutoff_dt = datetime.now(UTC) - cutoff_delta

    entries: list[tuple[str, Path, datetime | None]] = []
    for path in epoch_files:
        fp12 = _fp12_from_index_path(path)
        last_seen_iso = ledger.get(fp12, {}).get("last_seen")
        last_seen_dt = None
        if last_seen_iso:
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_iso)
                if last_seen_dt.tzinfo is None:
                    last_seen_dt = last_seen_dt.replace(tzinfo=UTC)
            except ValueError:
                last_seen_dt = None
        entries.append((fp12, path, last_seen_dt))

    known_sorted = sorted(
        (e for e in entries if e[2] is not None),
        key=lambda e: e[2] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    keep_reason: dict[str, str] = {current_fp12: "current epoch"}
    for fp12, _path, _ts in known_sorted[:keep_last]:
        keep_reason.setdefault(fp12, f"within --keep-last {keep_last}")
    for fp12, _path, ts in known_sorted:
        if ts is not None and ts >= cutoff_dt:
            keep_reason.setdefault(fp12, f"last seen within --older-than {older_than}")
    for fp12, _path, ts in entries:
        if ts is None:
            keep_reason.setdefault(fp12, "unknown age (no ledger entry) — never remove unknown")

    to_reap = [(fp12, path) for fp12, path, _ts in entries if fp12 not in keep_reason]
    to_keep = [(fp12, path) for fp12, path, _ts in entries if fp12 in keep_reason]

    if to_keep:
        click.echo("Keeping:")
        for fp12, _path in to_keep:
            click.echo(f"  {fp12}  ({keep_reason[fp12]})")
    if not to_reap:
        click.echo("\nNothing to prune.")
        return

    reap_size = sum(_epoch_size_bytes(path) for _fp12, path in to_reap)
    click.echo(
        f"\n{'Would reclaim' if dry_run else 'Reclaiming'} {_format_bytes(reap_size)} "
        f"across {len(to_reap)} epoch(s) — each rebuilds from parquet if that "
        "version runs again:"
    )
    for fp12, path in to_reap:
        click.echo(f"  {fp12}  ({path.name})")

    if dry_run:
        click.echo("\n[dry-run] Nothing deleted.")
        return

    reaped_fp12s = {fp12 for fp12, _path in to_reap}
    for _fp12, path in to_reap:
        path.unlink(missing_ok=True)
        Path(f"{path}.wal").unlink(missing_ok=True)
    _remove_epochs_ledger_entries(runs_dir, reaped_fp12s)
    click.echo(f"\nRemoved {len(to_reap)} epoch(s), reclaiming {_format_bytes(reap_size)}.")


def _merge_data_dir(src: Path, dst: Path) -> int:
    """Copy src's store subdirs into dst, skipping collisions. Returns files copied.

    Identities are unique (uuid4 sessions, ts+serial run files) so a plain union is
    safe; an already-present file is skipped, making re-runs idempotent.
    """
    import shutil

    copied = 0
    for sub in ("runs", "events", "channels", "files"):
        s = src / sub
        if not s.is_dir():
            continue
        for item in s.rglob("*"):
            if not item.is_file():
                continue
            d = dst / item.relative_to(src)
            if d.exists():
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, d)
            copied += 1
    return copied


@data.command("import")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--data-dir", default=None, help="Destination results dir (default: configured).")
def data_import(source: Path, data_dir: str | None) -> None:
    """Merge another ``data_dir`` into this one; the store daemons rebuild from the files.

    Copies SOURCE's runs / events / channels / files into the destination store
    (skipping collisions — identities are unique), then restarts the store daemons
    so they rebuild their warm indexes from the merged files. Use after copying a
    ``data_dir`` from another machine, or merging two stores: the data files are the
    source of truth, so the daemons reconcile by rebuilding — but their state files
    (pids/ports from the other machine) are stale and must be cleared.
    """
    from litmus.data.channels.flight_manager import FlightDaemonManager
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.files.catalog_manager import FilesCatalogManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    src = source.resolve()
    dst = Path(_get_data_dir(data_dir)).resolve()
    if src == dst:
        raise click.ClickException(f"Source and destination are the same: {src}")

    copied = _merge_data_dir(src, dst)
    click.echo(f"Merged {copied} file(s) from {src} into {dst}.")

    # Restart the store daemons: clear their now-stale state files + rebuild from
    # the merged data on next access (events/runs also drop the persisted index).
    for sub, mgr_cls in [
        ("events", DuckDBDaemonManager),
        ("runs", RunsDuckDBManager),
        ("channels", FlightDaemonManager),
        ("files", FilesCatalogManager),
    ]:
        d = dst / sub
        if d.exists():
            mgr_cls(d).force_restart()
            # Glob covers both the events single-file index (``_index.duckdb``)
            # and the runs content-addressed epoch files (``_index.<fp>.duckdb``,
            # #53 P1) — force-drop every epoch so the daemon rebuilds fresh.
            for idx in d.glob("_index*.duckdb"):
                idx.unlink(missing_ok=True)
                Path(f"{idx}.wal").unlink(missing_ok=True)
    click.echo("Store daemons restarted; warm indexes rebuild on next access.")
