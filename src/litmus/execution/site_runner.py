"""Platform-level parallel site execution for multi-UUT testing.

SiteRunner manages per-site **subprocesses** with environment-based isolation.
Each site gets its own OS process with site-specific env vars. The parent
process coordinates sync points via EventStore.

Usage:
    runner = SiteRunner(
        sites=resolved_sites,
        uuts={0: uut0, 1: uut1},
        session_id=session_id,
    )
    results = runner.run(["pytest", "tests/", "-v"])
    # results = {0: SiteResult(outcome="passed"), 1: SiteResult(...)}
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from litmus.data.models import UUT
from litmus.execution.sites import ResolvedSite

if TYPE_CHECKING:
    from litmus.data.event_store import EventStore
    from litmus.execution.sync import SyncCoordinator

logger = logging.getLogger(__name__)


def _build_site_env(
    site_index: int,
    uut: UUT,
    site: ResolvedSite,
    base_env: dict[str, str],
) -> dict[str, str]:
    """Build environment variables for a site subprocess.

    Environment variables set:
    - ``_LITMUS_SITE_INDEX`` — which site index this process handles
    - ``LITMUS_UUT_SERIAL`` — UUT serial for this site
    - ``LITMUS_UUT_PART_NUMBER`` — optional UUT part number
    - ``LITMUS_UUT_REVISION`` — optional UUT revision
    - ``LITMUS_UUT_LOT_NUMBER`` — optional UUT lot/batch number
    - ``LITMUS_FIXTURE_SITE`` — JSON-serialized site config
    """
    env = base_env.copy()
    env["_LITMUS_SITE_INDEX"] = str(site_index)
    env["LITMUS_UUT_SERIAL"] = uut.serial
    if uut.part_number:
        env["LITMUS_UUT_PART_NUMBER"] = uut.part_number
    if uut.revision:
        env["LITMUS_UUT_REVISION"] = uut.revision
    if uut.lot_number:
        env["LITMUS_UUT_LOT_NUMBER"] = uut.lot_number
    if site.uut_resource:
        env["LITMUS_UUT_RESOURCE"] = site.uut_resource
    env["LITMUS_FIXTURE_SITE"] = site.model_dump_json()
    return env


@dataclass
class SiteResult:
    """Outcome of a single site's subprocess execution."""

    site_index: int
    outcome: str  # "passed", "failed", "errored"
    returncode: int | None = None
    output_lines: list[str] = field(default_factory=list, repr=False)


class SiteRunner:
    """Runs a command for each UUT site in parallel subprocesses.

    Each subprocess gets:
    - ``_LITMUS_SESSION_ID`` — shared across all sites
    - ``_LITMUS_SITE_INDEX`` — which site index this process handles
    - ``_LITMUS_SITE_COUNT`` — total site count (for sync)
    - ``LITMUS_UUT_SERIAL`` — UUT serial for this site
    - ``LITMUS_UUT_PART_NUMBER`` — optional UUT metadata
    - ``LITMUS_UUT_RESOURCE`` — UUT driver connection string
    - ``LITMUS_FIXTURE_SITE`` — JSON-serialized site config
    - ``_LITMUS_INSTRUMENT_SERVER`` — instrument server address (if shared instruments)
    - ``_LITMUS_SHARED_ROLES`` — comma-separated roles served remotely

    Sync points use EventStore events.
    """

    def __init__(
        self,
        sites: list[ResolvedSite],
        uuts: dict[int, UUT],
        *,
        session_id: UUID | None = None,
        instrument_server_address: str | None = None,
        shared_roles: set[str] | None = None,
        child_grace_seconds: float = 5.0,
    ) -> None:
        if not sites:
            raise ValueError("At least one site is required")

        site_indices = {s.site_index for s in sites}
        missing = site_indices - set(uuts)
        if missing:
            missing_str = ", ".join(str(i) for i in sorted(missing))
            raise ValueError(f"Missing UUT identity for site indices: {missing_str}")

        self._sites = sites
        self._uuts = uuts
        self._session_id = session_id or uuid4()
        self._instrument_server_address = instrument_server_address
        self._shared_roles = shared_roles or set()
        self._child_grace_seconds = child_grace_seconds
        # Live child processes, keyed by site_index. Populated as each child
        # is spawned in :meth:`run`; consulted by
        # :meth:`_propagate_termination` (called from
        # ``pytest_keyboard_interrupt``) to forward SIGTERM and by the
        # ``finally`` cleanup path to kill survivors past the grace
        # budget. Empty outside of a ``run()`` call.
        self._processes: dict[int, subprocess.Popen] = {}

    def _propagate_termination(self) -> None:
        """Forward SIGTERM to every live child.

        Called from the orchestrator's ``pytest_keyboard_interrupt``
        path so each child gets a chance to run its own cleanup chain
        (``pytest_keyboard_interrupt`` → fixture teardown →
        ``Terminated``) *before* the orchestrator's existing
        ``finally`` block falls through to ``proc.kill()``.

        Idempotent — already-exited children are skipped, and a second
        call after the first SIGTERM has been delivered is a no-op.
        Errors raised by ``proc.terminate`` (e.g. process gone between
        ``poll`` and the syscall) are swallowed; the goal is best-effort
        propagation, not a strict delivery guarantee.
        """
        for site_index, proc in self._processes.items():
            if proc.poll() is None:
                logger.info("Forwarding SIGTERM to site %d", site_index)
                try:
                    proc.terminate()
                except (ProcessLookupError, OSError):
                    pass

    @property
    def session_id(self) -> UUID:
        return self._session_id

    def run(  # noqa: PLR0912
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        sync: bool = True,
        on_output: Callable[[int, str], None] | None = None,
        event_store: EventStore | None = None,
    ) -> dict[int, SiteResult]:
        """Spawn one subprocess per site, optionally coordinate sync.

        Args:
            cmd: Command to run in each site (e.g., ["pytest", "tests/", "-v"]).
            env: Extra environment variables to pass to all children.
            sync: If True, coordinate sync points via EventStore.
            on_output: Callback ``(site_index, line)`` for each stdout line.
                If None, output is collected silently in SiteResult.
            event_store: Pre-existing EventStore for sync coordination.
                If None, a new one is created when ``sync=True``.

        Returns:
            Dict mapping site_index -> SiteResult.
        """
        results: dict[int, SiteResult] = {}
        self._processes = {}
        threads: list[threading.Thread] = []

        base_env = os.environ.copy()
        if env:
            base_env.update(env)

        # Set shared env vars
        base_env["_LITMUS_SESSION_ID"] = str(self._session_id)
        base_env["_LITMUS_SITE_COUNT"] = str(len(self._sites))

        # Instrument server env vars for shared instruments
        if self._instrument_server_address and self._shared_roles:
            base_env["_LITMUS_INSTRUMENT_SERVER"] = self._instrument_server_address
            base_env["_LITMUS_SHARED_ROLES"] = ",".join(
                sorted(self._shared_roles),
            )

        # Start sync coordinator if needed
        coordinator: SyncCoordinator | None = None
        owns_event_store = False
        if sync and len(self._sites) > 1:
            try:
                from litmus.data.event_store import EventStore
                from litmus.execution.sync import SyncCoordinator

                if event_store is None:
                    event_store = EventStore()
                    owns_event_store = True
                coordinator = SyncCoordinator(
                    site_count=len(self._sites),
                    session_id=self._session_id,
                    event_store=event_store,
                )
                coordinator.start()
            except (ImportError, ValueError, OSError) as exc:
                logger.warning("Sync coordinator unavailable: %s", exc)

        # Get event log for emitting site lifecycle events
        event_log = None
        if event_store is not None:
            event_log = event_store.get_event_log(self._session_id)

        try:
            # Spawn one subprocess per site
            for site in self._sites:
                uut = self._uuts[site.site_index]
                site_env = _build_site_env(site.site_index, uut, site, base_env)

                result = SiteResult(site_index=site.site_index, outcome="errored")
                results[site.site_index] = result

                logger.info(
                    "Spawning site %d (UUT %s): %s",
                    site.site_index,
                    uut.serial,
                    " ".join(cmd),
                )

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=site_env,
                    text=True,
                )
                self._processes[site.site_index] = proc

                # Emit SiteStarted event
                if event_log is not None:
                    from litmus.data.events import SiteStarted

                    event_log.emit(
                        SiteStarted(
                            session_id=self._session_id,
                            site_index=site.site_index,
                            site_name=site.site_name,
                            uut_serial_number=uut.serial,
                        )
                    )

                # Monitor thread: reads stdout, waits for exit, notifies
                # coordinator *immediately* when a child dies so sync points
                # don't deadlock.
                t = threading.Thread(
                    target=self._monitor_site,
                    args=(proc, result, coordinator, on_output),
                    name=f"litmus-site-{site.site_index}",
                    daemon=True,
                )
                t.start()
                threads.append(t)

            # Wait for all monitor threads to finish
            for t in threads:
                t.join()

            # Emit SiteCompleted events after all workers finish
            if event_log is not None:
                from litmus.data.events import SiteCompleted

                for site_index, result in results.items():
                    event_log.emit(
                        SiteCompleted(
                            session_id=self._session_id,
                            site_index=site_index,
                            site_name=next(
                                (s.site_name for s in self._sites if s.site_index == site_index),
                                None,
                            ),
                            outcome=result.outcome,
                            error_message=(
                                f"exit code {result.returncode}"
                                if result.outcome != "passed"
                                else None
                            ),
                        )
                    )

        finally:
            # Terminate any still-running worker processes. If
            # ``_propagate_termination`` already fired (KeyboardInterrupt
            # path), most children are already exiting and ``terminate``
            # below is a no-op; the wait/kill cascade then enforces the
            # grace budget for any laggard.
            for site_index, proc in self._processes.items():
                if proc.poll() is None:
                    logger.warning("Terminating orphaned site %d", site_index)
                    proc.terminate()
                    try:
                        proc.wait(timeout=self._child_grace_seconds)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self._processes = {}
            if coordinator:
                coordinator.stop()
            if owns_event_store and event_store is not None:
                event_store.close()

        return results

    @staticmethod
    def _monitor_site(
        proc: subprocess.Popen,
        result: SiteResult,
        coordinator: SyncCoordinator | None,
        on_output: Callable[[int, str], None] | None = None,
    ) -> None:
        """Read stdout and wait for exit. Notifies coordinator immediately
        when a child dies so blocked sync points don't deadlock."""
        # Read stdout line by line
        if proc.stdout is not None:
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                result.output_lines.append(stripped)
                if on_output is not None:
                    on_output(result.site_index, line)

        # Process has exited (stdout EOF implies exit)
        proc.wait()
        returncode = proc.returncode
        result.returncode = returncode
        result.outcome = "passed" if returncode == 0 else "failed"

        # Immediately notify coordinator so blocked sync points unblock
        if returncode != 0 and coordinator is not None:
            coordinator.mark_site_dead(result.site_index)


# ---------------------------------------------------------------------------
# Pytest-plugin orchestrator layer
#
# ``pytest_runtestloop`` in plugin.py detects orchestrator mode and delegates
# to :func:`run_multi_site_session`, which spawns per-site subprocesses via
# :class:`SiteRunner`, optionally stands up an :class:`InstrumentServer` for
# shared instruments, coordinates session-level events, and aggregates results.
# ---------------------------------------------------------------------------


def is_orchestrator_mode(config) -> bool:
    """Detect if this process should orchestrate multi-site execution.

    Orchestrator mode activates when:

    1. ``_LITMUS_SITE_INDEX`` is NOT set (we're not a worker child)
    2. ``--site=N`` is NOT set (operator is targeting one specific
       site in single-process mode — bypass orchestrator dispatch)
    3. A multi-site fixture config is detected

    The ``--site`` opt-out is what makes operator targeting work
    against a multi-site fixture: the operator passes ``--site=1``
    (and a single ``--uut-serial``) and gets a single-process run that
    records as ``site_index=1`` instead of N parallel children.
    """
    if os.environ.get("_LITMUS_SITE_INDEX") is not None:
        return False  # Already a worker

    if config.getoption("--site"):
        return False  # Operator targeting a single site in single-process mode

    from litmus.pytest_plugin.helpers import find_fixture_file

    fixture_path = find_fixture_file(config)
    if fixture_path is None:
        return False

    try:
        from litmus.store import load_fixture

        fc = load_fixture(fixture_path)
        return fc.is_multi_site
    except Exception:  # noqa: BLE001 — fall back to single-site on any load error
        # Missing or invalid fixture file — fall back to single-site mode
        # and let the normal config-loading path surface the real error.
        return False


def is_worker_mode() -> bool:
    """Detect if this process is a multi-site worker child."""
    return os.environ.get("_LITMUS_SITE_INDEX") is not None


def _build_child_cmd(config) -> list[str]:
    """Build the pytest command for child processes.

    Reconstructs the original pytest invocation, stripping ``--uut-serial(s)``
    (each child gets its own ``--uut-serial`` via env var).
    """
    import sys

    args = list(config.invocation_params.args)

    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("--uut-serials="):
            continue
        if arg == "--uut-serials":
            skip_next = True
            continue
        if arg.startswith("--uut-serial="):
            continue
        if arg == "--uut-serial":
            skip_next = True
            continue
        filtered.append(arg)

    return [sys.executable, "-m", "pytest"] + filtered


def _extract_pytest_summary(output_lines: list[str]) -> str:
    """Extract the pytest summary line from worker output.

    Scans from the end looking for lines matching pytest's summary format
    (e.g., ``1 passed``, ``2 failed, 1 passed``).
    """
    import re

    pattern = re.compile(r"\d+ (passed|failed|error|warning|skipped|deselected)")
    for line in reversed(output_lines):
        if pattern.search(line):
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            clean = clean.strip("= ").strip()
            return clean
    return "(no summary)"


def _report_site_results(session, results: dict[int, SiteResult]) -> None:
    """Report per-site results from subprocess mode."""
    import sys

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write("Multi-UUT Results\n")
    sys.stdout.write("=" * 60 + "\n")
    for site_index in sorted(results):
        r = results[site_index]
        status = "PASS" if r.outcome == "passed" else "FAIL"
        summary = _extract_pytest_summary(r.output_lines)
        sys.stdout.write(f"  site[{site_index}]: {status}  {summary}\n")
    sys.stdout.write("=" * 60 + "\n\n")
    sys.stdout.flush()

    failed_sites = [idx for idx, r in results.items() if r.outcome != "passed"]
    session.testsfailed = len(failed_sites)


def _run_subprocess_mode(
    session,
    sites: list[ResolvedSite],
    uuts: dict[int, UUT],
    session_id: UUID,
    shared_roles: set[str] | None = None,
    station_instruments: dict[str, Any] | None = None,
    mock_all: bool = False,
    child_grace_seconds: float = 5.0,
) -> None:
    """Run multi-site tests using subprocess-per-site.

    If shared instruments are detected, starts an :class:`InstrumentServer` in
    the orchestrator process and passes the address to workers via env vars.
    Workers get ``RemoteInstrumentProxy`` objects for those roles.
    """
    import sys

    from litmus.data.events import SessionStarted
    from litmus.execution._state import get_current_run_scope
    from litmus.execution.session_scope import open_session

    server = None
    shared_drivers: dict[str, Any] = {}
    shared_roles = shared_roles or set()
    station_instruments = station_instruments or {}

    # Only serve non-mocked shared instruments through the server.
    # Mocked instruments get independent instances per worker so each
    # worker has its own mock state (per-test mock values don't leak).
    served_roles: set[str] = set()
    if shared_roles:
        from litmus.instruments.lifecycle import disconnect, load_and_connect
        from litmus.instruments.server import InstrumentServer
        from litmus.models.instrument import InstrumentRecord

        concurrent_roles: set[str] = set()
        resources: dict[str, str] = {}

        for role in shared_roles:
            inst_cfg = station_instruments.get(role)
            if inst_cfg is None:
                continue
            if mock_all or inst_cfg.mock:
                continue  # Workers get independent mocks

            try:
                record = InstrumentRecord(
                    role=role,
                    instrument_id=role,
                    driver=inst_cfg.driver,
                    resource=inst_cfg.resource or "",
                    protocol="visa",
                    mocked=False,
                )
                driver = load_and_connect(
                    record,
                    mock=False,
                    mock_config=inst_cfg.mock_config,
                )
                shared_drivers[role] = driver
                served_roles.add(role)
                if inst_cfg.resource:
                    resources[role] = inst_cfg.resource
                if inst_cfg.type == "switch":
                    concurrent_roles.add(role)
            except Exception as exc:
                for cleanup_role, cleanup_driver in shared_drivers.items():
                    disconnect(cleanup_driver, cleanup_role)
                raise RuntimeError(f"Failed to connect shared instrument {role!r}: {exc}") from exc

        if shared_drivers:
            server = InstrumentServer(
                shared_drivers,
                resources=resources,
                concurrent_roles=concurrent_roles,
            )
            server.start()

    current_run_scope = get_current_run_scope()

    station_id = ""
    station_name = None
    station_type = None
    station_location = None
    operator_id = None
    operator_name = None
    fixture_id = None
    if current_run_scope:
        tr = current_run_scope.test_run
        station_id = tr.station_id
        station_name = tr.station_name
        station_type = tr.station_type
        station_location = tr.station_location
        operator_id = tr.operator_id
        operator_name = tr.operator_name
        fixture_id = tr.fixture_id

    # Open the session via the shared primitive — the orchestrator owns it
    # (reuse the store if the logger fixture set one, else create + own). Emits
    # SessionStarted(site_count); workers attach to this injected session id.
    scope = open_session(
        SessionStarted.from_station(
            session_id=session_id,
            station_id=station_id,
            station_name=station_name,
            station_type=station_type,
            station_location=station_location,
            operator_id=operator_id,
            operator_name=operator_name,
            fixture_id=fixture_id,
            site_count=len(sites),
        ),
        session_id=session_id,
        reuse_existing=True,
        emit_lifecycle=True,
    )

    try:

        def _stream_output(site_index: int, line: str) -> None:
            sys.stdout.write(f"[site:{site_index}] {line}")
            sys.stdout.flush()

        from litmus.execution._state import set_active_site_runner

        runner = SiteRunner(
            sites,
            uuts,
            session_id=session_id,
            instrument_server_address=server.address_str if server else None,
            shared_roles=served_roles if server else None,
            child_grace_seconds=child_grace_seconds,
        )
        child_cmd = _build_child_cmd(session.config)
        # Expose the runner via ContextVar so
        # ``pytest_keyboard_interrupt`` can forward SIGTERM to live
        # children before the orchestrator's own teardown unwinds.
        set_active_site_runner(runner)
        try:
            results = runner.run(
                child_cmd,
                on_output=_stream_output,
                event_store=scope.event_store,
            )
        finally:
            set_active_site_runner(None)

        _report_site_results(session, results)

        scope.emit_ended()
    finally:
        scope.close_stores()  # closes log + (owned) EventStore client connection; daemon untouched

        if server is not None:
            server.stop(force=True)

        from litmus.instruments.lifecycle import disconnect

        for role, driver in shared_drivers.items():
            disconnect(driver, role)


def run_multi_site_session(
    session,
    station_config=None,
) -> bool:
    """Orchestrate a multi-site pytest session.

    Called from ``pytest_runtestloop`` when :func:`is_orchestrator_mode` is
    true. Loads the fixture config, resolves per-site UUT identities, stands
    up an :class:`InstrumentServer` for any shared roles, then drives
    :class:`SiteRunner` and reports per-site summaries.

    Returns ``True`` to signal the caller should suppress pytest's default
    test-execution loop.
    """
    import warnings

    from litmus.execution._state import get_current_run_scope
    from litmus.execution.sites import detect_shared_instruments, resolve_fixture_sites
    from litmus.execution.uut_provider import CLIUUTProvider
    from litmus.pytest_plugin import _mocks_active
    from litmus.pytest_plugin.helpers import find_fixture_file
    from litmus.store import load_fixture, load_project_config

    fixture_path = find_fixture_file(session.config)
    if fixture_path is None:
        return False
    fixture_config = load_fixture(fixture_path)

    sites = resolve_fixture_sites(fixture_config)

    shared_roles = detect_shared_instruments(sites)
    station_instruments = station_config.instruments if station_config else {}

    uut_serial = session.config.getoption("--uut-serial")
    uut_serials_raw = session.config.getoption("--uut-serials")
    provider = CLIUUTProvider.from_cli_args(
        uut_serial=uut_serial,
        uut_serials=uut_serials_raw,
        sites=sites,
    )
    uuts = {site.site_index: provider.get_uut(site.site_index) for site in sites}

    if uut_serial and not uut_serials_raw and len(sites) > 1:
        warnings.warn(
            f"Single --uut-serial '{uut_serial}' applied to all {len(sites)} sites. "
            f"Use --uut-serials for per-site assignment.",
            stacklevel=1,
        )

    current_run_scope = get_current_run_scope()
    session_id = current_run_scope.test_run.session_id if current_run_scope else uuid4()

    project_config = load_project_config()

    _run_subprocess_mode(
        session,
        sites,
        uuts,
        session_id,
        shared_roles=shared_roles,
        station_instruments=station_instruments,
        mock_all=_mocks_active(session.config),
        child_grace_seconds=project_config.multi_site.child_grace_seconds,
    )

    return True
