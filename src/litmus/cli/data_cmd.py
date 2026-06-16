"""Data retention and management commands."""

from __future__ import annotations

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

    cols = ["part_id", "station_id", "uut_serial", "fixture_id"]
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
    if row0.get("uut_serial") in _STARTER_UUT_SERIALS:
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
            idx = d / "_index.duckdb"
            if idx.exists():
                idx.unlink()

    click.echo("Index daemons stopped. Index will rebuild on next query.")


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
            idx = d / "_index.duckdb"
            if idx.exists():
                idx.unlink()
    click.echo("Store daemons restarted; warm indexes rebuild on next access.")
