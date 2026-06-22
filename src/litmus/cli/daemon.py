"""Daemon lifecycle commands."""

from __future__ import annotations

from pathlib import Path

import click

from litmus.cli.root import main


def _resolve_daemon_dirs(
    targets: tuple[str, ...] | None,
    *,
    all_flag: bool,
) -> list[tuple[str, Path]]:
    """Resolve user-specified targets to ``[(label, dir), ...]``.

    With ``--all`` (or no targets), return all three canonical
    daemons (events, runs, channels) under the configured
    ``data_dir``. Targets can be the labels themselves
    (``events`` / ``runs`` / ``channels``) or absolute directory
    paths to operate on a non-default project.
    """
    from litmus.data.data_dir import resolve_data_dir

    canonical = {
        "events": Path(resolve_data_dir()) / "events",
        "runs": Path(resolve_data_dir()) / "runs",
        "channels": Path(resolve_data_dir()) / "channels",
    }
    if not targets or all_flag:
        return list(canonical.items())
    out: list[tuple[str, Path]] = []
    for t in targets:
        if t in canonical:
            out.append((t, canonical[t]))
        else:
            p = Path(t).resolve()
            out.append((p.name, p))
    return out


def _manager_for(label: str, daemon_dir: Path):
    """Return the ``DaemonManager`` instance for a daemon-dir label."""
    if label == "channels":
        from litmus.data.channels.flight_manager import FlightDaemonManager

        return FlightDaemonManager(daemon_dir)
    if label == "events":
        from litmus.data.duckdb_manager import DuckDBDaemonManager

        return DuckDBDaemonManager(daemon_dir)
    if label == "runs":
        from litmus.data.runs_duckdb_manager import RunsDuckDBManager

        return RunsDuckDBManager(daemon_dir)
    # Fallback: heuristic by directory name.
    from litmus.data.duckdb_manager import DuckDBDaemonManager

    return DuckDBDaemonManager(daemon_dir)


@main.group("daemon")
def daemon_group():
    """Manage Litmus background daemons (events / runs / channels)."""
    pass


@daemon_group.command("status")
def daemon_status() -> None:
    """Show running daemons, their PIDs, refs, and locations.

    Reads the per-daemon state file directly. No daemon contact
    required — works even if a daemon is unreachable but its state
    file is still on disk (in which case the listed PID may be
    dead; check with ``ps`` if in doubt).
    """
    from litmus.data._daemon_lifecycle import _pid_alive

    rows = _resolve_daemon_dirs((), all_flag=True)
    click.echo(f"{'daemon':<10} {'pid':<8} {'alive':<6} {'refs':<5} location")
    click.echo("-" * 80)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"{label:<10} {'-':<8} {'-':<6} {'-':<5} (no dir)")
            continue
        mgr = _manager_for(label, daemon_dir)
        state = mgr.read_state()
        pid = state.get("pid")
        alive = _pid_alive(pid) if isinstance(pid, int) else False
        refs = len(state.get("refs", []) or [])
        loc = state.get("location", "")
        pid_str = str(pid) if pid is not None else "-"
        alive_str = "yes" if alive else ("no" if pid is not None else "-")
        click.echo(f"{label:<10} {pid_str:<8} {alive_str:<6} {refs:<5} {loc}")


@daemon_group.command("restart")
@click.argument("targets", nargs=-1)
@click.option("--all", "all_flag", is_flag=True, help="Restart every daemon under the project")
def daemon_restart(targets: tuple[str, ...], all_flag: bool) -> None:
    """Restart selected daemons (SIGTERM the running process; respawn on next access).

    Use after editing daemon code while ``litmus serve --reload``
    is running, or after bumping ``_SCHEMA_VERSION`` so the schema
    rebuild path runs at the next acquire.

    Targets can be ``events`` / ``runs`` / ``channels`` (resolved
    against the configured ``data_dir``) or absolute directory
    paths. With ``--all`` or no targets, restarts all three.
    """
    rows = _resolve_daemon_dirs(targets, all_flag=all_flag)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"[{label}] no directory at {daemon_dir} — skipped")
            continue
        mgr = _manager_for(label, daemon_dir)
        try:
            mgr.force_restart()
        except Exception as exc:  # noqa: BLE001 — operator command, surface and keep going
            click.echo(f"[{label}] restart failed: {exc}")
            continue
        click.echo(f"[{label}] restarted (next acquire spawns fresh)")


@daemon_group.command("stop")
@click.argument("targets", nargs=-1)
@click.option("--all", "all_flag", is_flag=True, help="Stop every daemon under the project")
def daemon_stop(targets: tuple[str, ...], all_flag: bool) -> None:
    """Stop selected daemons without respawning.

    Same kill semantics as ``restart`` (SIGTERM the pid in state,
    SIGKILL after grace), but doesn't trigger a respawn. The next
    actual ``acquire()`` from a UI / CLI / test will lazily spawn
    a fresh daemon when needed.
    """
    from litmus.data._daemon_lifecycle import _pid_alive

    rows = _resolve_daemon_dirs(targets, all_flag=all_flag)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"[{label}] no directory at {daemon_dir} — skipped")
            continue
        mgr = _manager_for(label, daemon_dir)
        state = mgr.read_state()
        pid = state.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            click.echo(f"[{label}] not running")
            continue
        try:
            mgr._kill_daemon(pid)  # noqa: SLF001 — operator-side use of internal helper
            mgr.cleanup_state_files()
        except Exception as exc:  # noqa: BLE001
            click.echo(f"[{label}] stop failed: {exc}")
            continue
        click.echo(f"[{label}] stopped (pid {pid})")
