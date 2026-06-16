"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from litmus.data.models import TestVector
from litmus.execution._state import (
    get_active_facets,
    get_active_limits,
    get_active_profile,
    get_channel_store,
    get_collected_items,
    get_current_step,
    get_event_store,
    get_instrument_records,
    get_session_inputs,
    push_current_context,
    push_current_vector,
    reset_current_context,
    reset_current_vector,
    set_active_instruments,
    set_active_part_context,
    set_active_vector_index,
    set_active_vector_params,
    set_current_run_scope,
    set_instrument_records,
)
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.connections import ConnectionIterator
from litmus.execution.harness import Context
from litmus.execution.instrument_events import emit_instrument_events
from litmus.execution.logger import RunContext, RunScope
from litmus.execution.metadata import build_run_metadata
from litmus.execution.profiles import resolve_test_phase
from litmus.execution.verify import (
    LimitsFn,
    MeasureFn,
    VerifyFn,
    build_measure_callable,
    build_verify_callable,
)
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.part import Part
from litmus.models.station import StationConfig
from litmus.models.test_config import FixtureConfig, PromptConfig
from litmus.parts.context import PartContext
from litmus.prompts import ask as ask_prompt

# Pytest discovers fixtures by attribute lookup on the plugin module —
# importing them here makes them attributes of ``litmus.pytest_plugin``
# without exposing them via ``__all__`` (they are framework internals,
# named with leading ``_``).
from litmus.pytest_plugin.autouse import (
    _litmus_apply_mocks,  # noqa: F401
    _litmus_push_limits,  # noqa: F401
    _litmus_push_params,  # noqa: F401
    _litmus_resolve_connections,  # noqa: F401
    _reseat_current_run_scope,  # noqa: F401
    _route_cleanup,  # noqa: F401
)
from litmus.pytest_plugin.helpers import (
    find_fixture_file as _find_fixture_file,
)
from litmus.pytest_plugin.helpers import (
    find_station_file as _find_station_file,
)
from litmus.pytest_plugin.helpers import (
    find_yaml_in_subdir as _find_yaml_in_subdir,
)
from litmus.pytest_plugin.helpers import (
    mocks_active as _mocks_active,
)
from litmus.pytest_plugin.helpers import (
    prompt_for_serial,
)
from litmus.pytest_plugin.helpers import (
    resolve_station_id as _resolve_station_id,
)
from litmus.pytest_plugin.helpers import (
    safe_get_session_fixture as _safe_get_session_fixture,
)
from litmus.pytest_plugin.hooks import (
    VECTORS_MATRIX_KEY,
    _close_open_class_container,
    _profile_errors_as_usage,
    pytest_addoption,
    pytest_assertion_pass,
    pytest_collection_modifyitems,
    pytest_configure,
    pytest_generate_tests,
    pytest_keyboard_interrupt,
    pytest_load_initial_conftests,
    pytest_report_header,
    pytest_runtest_call,
    pytest_runtest_makereport,
    pytest_runtest_setup,
    pytest_runtestloop,
    pytest_sessionfinish,
    pytest_sessionstart,
)

# Pytest discovers hooks and fixtures by attribute lookup on this
# module, not by ``__all__``. ``__all__`` lists only the pytest hook
# names the plugin contributes — the public surface of "what this
# plugin does." State helpers and autouse fixtures stay attributes of
# the module (so pytest can find them) but are not advertised here:
# the autouse names start with ``_`` and the state helpers live
# canonically in ``litmus.execution._state``.
__all__ = [
    "pytest_addoption",
    "pytest_assertion_pass",
    "pytest_collection_modifyitems",
    "pytest_configure",
    "pytest_generate_tests",
    "pytest_keyboard_interrupt",
    "pytest_load_initial_conftests",
    "pytest_report_header",
    "pytest_runtest_call",
    "pytest_runtest_makereport",
    "pytest_runtest_setup",
    "pytest_runtestloop",
    "pytest_sessionfinish",
    "pytest_sessionstart",
]


def _prompt_for_slot_serials(
    slot_ids: list[str],
    test_phase: str,
) -> dict[str, str]:
    """Prompt for UUT serial for each slot.

    Args:
        slot_ids: Ordered list of slot IDs from fixture config.
        test_phase: Current test phase (for error message).

    Returns:
        Dict mapping slot_id → serial.
    """
    serials: dict[str, str] = {}
    for slot_id in slot_ids:
        serials[slot_id] = prompt_for_serial(test_phase, slot_id)
    return serials


def _require_fixture_and_instruments(
    fixture_config: Any, instruments: dict[str, Any], feature: str
) -> None:
    """Validate that fixture config and instruments are available."""
    if not fixture_config:
        raise pytest.UsageError(
            f"The '{feature}' fixture requires a fixture config. "
            "Provide --fixture <path> or create a fixtures/*.yaml file."
        )
    if not instruments:
        raise pytest.UsageError(
            f"The '{feature}' fixture requires instruments. "
            "Provide --station <path> or create a stations/*.yaml file."
        )


def _build_run_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Pytest adapter — read session fixtures + CLI options, delegate to runner-neutral builder."""
    from litmus.execution.profiles import validate_phase_wiring
    from litmus.store import load_station_type

    requested_phase = request.config.getoption("--test-phase") or os.environ.get(
        "LITMUS_TEST_PHASE"
    )
    station_config = _safe_get_session_fixture(request, "station_config")
    fixture_config = _safe_get_session_fixture(request, "fixture_config")
    profile = get_active_profile()

    # Cross-check phase wiring (Step 5). Loads the StationType template
    # if the active station declares one; runs the four checks; raises
    # pytest.UsageError on mismatch.
    station_type_template = None
    if station_config is not None and station_config.station_type:
        station_type_template = load_station_type(
            station_config.station_type, project_root=request.config.rootpath
        )
    with _profile_errors_as_usage():
        validate_phase_wiring(
            profile=profile,
            station_config=station_config,
            fixture_config=fixture_config,
            station_type_template=station_type_template,
        )

    return build_run_metadata(
        uut_serial=request.config.getoption("--uut-serial"),
        uut_part_number=request.config.getoption("--uut-part-number"),
        uut_revision=request.config.getoption("--uut-revision"),
        uut_lot_number=request.config.getoption("--uut-lot-number"),
        station_id=_resolve_station_id(request.config),
        station_config=station_config,
        fixture_config=fixture_config,
        part=_safe_get_session_fixture(request, "part"),
        operator_id=request.config.getoption("--operator"),
        project_dir=request.config.rootpath,
        data_dir=request.config.getoption("--data-dir"),
        test_phase=resolve_test_phase(requested_phase, mocks_active=_mocks_active(request.config)),
        profile_name=request.config.getoption("--test-profile", default=None),
        profile_facets=dict(get_active_facets()),
        session_inputs=dict(get_session_inputs()),
        instrument_records=_safe_get_session_fixture(request, "instrument_records"),
    )


def _emit_run_start_events(run_scope: RunScope) -> None:
    """Emit RunStarted + per-instrument + StepsDiscovered.

    Session lifecycle (SessionStarted / stores) is opened at
    ``pytest_sessionstart`` (see ``hooks._open_session_for_pytest``); this
    fixture-side helper emits only the RUN-scoped events.
    """
    from litmus.data.events import RunStarted, StepsDiscovered

    event_log = run_scope.event_log
    if event_log is None:
        return

    from litmus.execution._state import get_current_slot_id

    slot_id = get_current_slot_id()
    env_slot_index_str = os.environ.get("_LITMUS_SLOT_INDEX")
    env_slot_index = int(env_slot_index_str) if env_slot_index_str else None

    event_log.emit(
        RunStarted(
            session_id=run_scope._session_id,
            run_id=run_scope.test_run.id,
            slot_id=slot_id,
            slot_index=env_slot_index,
            station_id=run_scope.test_run.station_id,
            station_name=run_scope.test_run.station_name,
            station_type=run_scope.test_run.station_type,
            station_location=run_scope.test_run.station_location,
            station_hostname=run_scope.test_run.station_hostname,
            uut_serial=run_scope.test_run.uut.serial,
            uut_part_number=run_scope.test_run.uut.part_number,
            uut_revision=run_scope.test_run.uut.revision,
            uut_lot_number=run_scope.test_run.uut.lot_number,
            part_id=run_scope.test_run.part_id,
            part_name=run_scope.test_run.part_name,
            part_revision=run_scope.test_run.part_revision,
            operator_id=run_scope.test_run.operator_id,
            operator_name=run_scope.test_run.operator_name,
            fixture_id=run_scope.test_run.fixture_id,
            test_phase=run_scope.test_run.test_phase,
            project_name=run_scope.test_run.project_name,
            git_commit=run_scope.test_run.git_commit,
            git_branch=run_scope.test_run.git_branch,
            git_remote=run_scope.test_run.git_remote,
            environment_json=run_scope.test_run.environment_json,
            custom_metadata=dict(run_scope.test_run.custom_metadata),
            pid=os.getpid(),
        )
    )

    _emit_instrument_events(run_scope, event_log)

    collected = get_collected_items()
    if collected:
        event_log.emit(
            StepsDiscovered(
                session_id=run_scope._session_id,
                run_id=run_scope.test_run.id,
                items=[ci.model_dump() for ci in collected],
            )
        )


def _finalize_run(run_scope: RunScope) -> None:
    """Finalize the run (emit RunEnded). Session close is handled at sessionfinish.

    Close any still-open class container BEFORE finalize() so the
    container's StepEnded carries its rolled-up outcome (the rollup walks
    the run's steps and folds children via the severity ladder).
    ``finalize()`` is idempotent — on a KeyboardInterrupt, ``pytest_sessionfinish``
    finalizes first (it runs before this session-scoped fixture's teardown),
    and this call is then a no-op.
    """
    _close_open_class_container(run_scope)
    # finalize() emits RunEnded; it does not close the event log itself.
    run_scope.finalize()


@pytest.fixture(scope="session", autouse=True)
def _run_scope(request) -> Generator[RunScope, None, None]:
    """Provide the test run scope for the session.

    Autouse so every test (and the ``verify`` / ``context`` fixtures
    that route through ``set_current_run_scope``) sees an active run scope.
    Snapshots config at run start; emits to the always-on parquet +
    channels stores. Post-hoc rendering / format conversion happens
    via ``litmus show -f X`` and ``litmus export <run> -f X``.

    Session lifecycle (SessionStarted / stores open / SessionEnded / stores close)
    is handled by ``pytest_sessionstart`` / ``pytest_sessionfinish``. This fixture
    attaches the run to the already-open session and emits only RunStarted /
    RunEnded (+ instrument events + StepsDiscovered).
    """
    from litmus.data.data_dir import resolve_data_dir
    from litmus.pytest_plugin.hooks import (
        _RUN_SCOPE_KEY,
        _SESSION_ID_KEY,
        _SESSION_SCOPE_KEY,
    )

    meta = _build_run_metadata(request)
    data_dir = meta["data_dir"]
    if not data_dir:
        data_dir = str(resolve_data_dir())
        meta["data_dir"] = data_dir

    # Session_id is minted by _open_session_for_pytest at sessionstart and stashed.
    # Fall back to env/uuid4 for collect-only / headless paths where no session opened.
    stash_session_id = request.session.stash.get(_SESSION_ID_KEY, None)
    if stash_session_id is not None:
        session_id = stash_session_id
    else:
        env_session_id = os.environ.get("_LITMUS_SESSION_ID")
        session_id = UUID(env_session_id) if env_session_id else uuid4()
    meta["session_id"] = session_id

    env_uut_serial = os.environ.get("LITMUS_UUT_SERIAL")
    if env_uut_serial:
        meta["uut_serial"] = env_uut_serial

    run_scope = RunScope(**meta)
    # Store this session's run so pytest_sessionfinish finalizes THIS run on a
    # KeyboardInterrupt — not get_current_run_scope() (a nested pytester run
    # restores that to the outer run, which we must not seal mid-suite).
    request.session.stash[_RUN_SCOPE_KEY] = run_scope

    scope = request.session.stash.get(_SESSION_SCOPE_KEY, None)
    if scope is not None:
        run_scope.event_log = scope.event_log
        _emit_run_start_events(run_scope)

    set_current_run_scope(run_scope)
    try:
        yield run_scope
    finally:
        # Capture not-started steps onto the run manifest before finalize.
        run_scope.test_run.collected_items = get_collected_items()
        _finalize_run(run_scope)
        set_current_run_scope(None)


def _emit_instrument_events(run_scope: RunScope, event_log: Any) -> None:
    """Pytest adapter — read ContextVar records, delegate to runner-neutral emitter."""
    emit_instrument_events(run_scope, event_log, get_instrument_records())


@pytest.fixture(scope="session")
def run_context(_run_scope) -> RunContext:
    """Provide run context for adding custom metadata.

    This is the run-level context that persists across all tests in the session.
    For step or vector-scoped context, use the `context` fixture instead.

    Usage:
        def test_example(run_context):
            run_context.set("operator_badge", "EMP-12345")
            run_context.set("fixture_serial", "FIX-001")
    """
    return _run_scope.run_context


@pytest.fixture(scope="session")
def part(request) -> Part | None:
    """The active :class:`Part` definition for spec-driven testing.

    Resolution chain (first match wins):

    1. ``--part <id-or-path>`` — bare id looks up
       ``parts/<id>.yaml``; a value with ``/`` or ``.yaml``/``.yml``
       is used as an explicit path. Mirrors ``--station``/``--fixture``
       resolution shape.
    2. ``--uut-part-number <pn>`` — content match against
       ``part.part_number:`` across ``parts/*.yaml``.
    3. Single-file fallback when ``parts/`` holds exactly one file.
    4. ``None`` — bringup tier without a part YAML.

    Exposes the part's identity, pins, and characteristics. For derived
    limits use the ``limits`` fixture or ``context.get_limit(name)``; for
    everything else the test can reach, use the ``context`` fixture.

    Usage in tests:
        def test_voltage(part, context, dmm):
            assert part.part_number == "DEMO-BUCK-3V3"
            limit = context.get_limit("output_voltage", temperature=25)

    Returns:
        :class:`Part`, or ``None`` if no part YAML is loaded.
    """
    from litmus.pytest_plugin.helpers import is_yaml_path

    part_value = request.config.getoption("--part")
    guardband = float(request.config.getoption("--guardband"))
    part_number = request.config.getoption("--uut-part-number")

    ctx = None

    if part_value:
        if is_yaml_path(part_value):
            ctx = PartContext.from_file(part_value, guardband_pct=guardband)
        else:
            part_path = _find_yaml_in_subdir(request.config, "parts", f"{part_value}.yaml")
            if part_path is None:
                raise pytest.UsageError(
                    f"--part={part_value!r} did not find "
                    f"parts/{part_value}.yaml. Pass an explicit path "
                    "(e.g. --part=path/to/foo.yaml) for files outside "
                    "the project's ``parts/`` directory."
                )
            ctx = PartContext.from_file(part_path, guardband_pct=guardband)
    else:
        ctx = _autodiscover_part(request.config, guardband, part_number)

    # The PartContext stays the internal derivation engine (limit resolution
    # reaches it via the active-part-context ContextVar); the fixture exposes
    # the Part definition itself.
    set_active_part_context(ctx)
    return ctx.part if ctx else None


def _autodiscover_part(
    config: pytest.Config,
    guardband: float,
    part_number: str | None,
) -> PartContext | None:
    """Pick a part YAML from ``parts/`` in the project or cwd.

    Selection rules:
    1. If ``--uut-part-number`` is set and exactly one part's
       ``part_number:`` matches (case-insensitive), use it.
    2. If ``--uut-part-number`` is set but no file matches, raise
       ``pytest.UsageError`` — a typo in the selector is worse than a
       silent wrong-part pick.
    3. Otherwise, take the first sorted ``parts/*.yaml`` file. If
       the directory holds multiple parts and ``--uut-part-number``
       was not provided, raise ``pytest.UsageError`` — Rev-B flows
       need an explicit selector.
    """
    search_roots = [
        config.rootpath,
        Path(config.invocation_params.dir),
    ]

    part_files: list[Path] = []
    for root in search_roots:
        parts_dir = root / "parts"
        if not parts_dir.exists():
            continue
        part_files = [p for p in sorted(parts_dir.rglob("*.yaml")) if not p.name.startswith("_")]
        if part_files:
            break

    if not part_files:
        return None

    if part_number:
        matches: list[Path] = []
        pn_lower = part_number.lower()
        for yaml_file in part_files:
            try:
                loaded = PartContext.from_file(yaml_file, guardband_pct=guardband)
            except (ValueError, OSError):
                continue
            if (loaded.part.part_number or "").lower() == pn_lower:
                matches.append(yaml_file)
        if len(matches) == 1:
            return PartContext.from_file(matches[0], guardband_pct=guardband)
        if not matches:
            raise pytest.UsageError(
                f"--uut-part-number={part_number!r} did not match any part in "
                f"parts/. Available: " + ", ".join(sorted(p.stem for p in part_files))
            )
        raise pytest.UsageError(
            f"--uut-part-number={part_number!r} matched multiple parts: "
            + ", ".join(sorted(str(m.relative_to(m.parents[1])) for m in matches))
            + ". Use --part=<path> to disambiguate."
        )

    if len(part_files) > 1:
        raise pytest.UsageError(
            f"parts/ has {len(part_files)} YAML files "
            f"({', '.join(p.stem for p in part_files)}); "
            "pass --part <id-or-path> or --uut-part-number <pn> to choose one."
        )

    return PartContext.from_file(part_files[0], guardband_pct=guardband)


@pytest.fixture(scope="session")
def mock_instruments(request) -> bool:
    """Return whether to use mock instruments instead of real hardware.

    Mocks do not block any test_phase; ``resolve_test_phase`` demotes
    the run's data stamp to ``"development"`` when mocks are active, so
    profile-driven limits/markers still apply but dashboards ignore
    the row. See ``resolve_test_phase`` for the demotion rule.
    """
    return _mocks_active(request.config)


@pytest.fixture(scope="session")
def station_config(request) -> StationConfig | None:
    """Load station configuration resolved from ``--station``.

    Also publishes the result to the active-station ContextVar so
    ``context.station`` can read it without taking the fixture as an
    argument.

    Returns:
        StationConfig model, or None if not specified.
    """
    from litmus.execution._state import set_active_station_config

    station_path = _find_station_file(request.config)
    if station_path:
        from litmus.store import load_station

        config = load_station(station_path)
        set_active_station_config(config)
        return config

    # Check if --station was explicitly passed (not auto-resolved)
    station_id = _resolve_station_id(request.config)
    explicit = any(arg.startswith("--station") for arg in request.config.invocation_params.args)
    if explicit:
        warnings.warn(
            f"Station '{station_id}' not found in stations/ directory. "
            f"Instrument fixtures (psu, dmm, etc.) will not be available. "
            f"Fix: create stations/{station_id}.yaml",
            stacklevel=2,
        )
    set_active_station_config(None)
    return None


@pytest.fixture(scope="session")
def fixture_config(request) -> FixtureConfig | None:
    """Load fixture configuration resolved from ``--fixture``.

    In worker mode (``_LITMUS_SLOT_ID`` set), extracts this slot's points
    from a multi-slot fixture config so downstream fixtures (pins,
    FixtureManager) see a flat ``points`` dict.

    Returns:
        FixtureConfig instance, or None if not specified.
    """
    fixture_path = _find_fixture_file(request.config)
    if not fixture_path:
        return None
    config_path = str(fixture_path)

    from litmus.store import load_fixture

    fc = load_fixture(Path(config_path))

    # Worker mode: extract this slot's points from multi-slot fixture
    slot_id = os.environ.get("_LITMUS_SLOT_ID")
    if slot_id and fc.is_multi_slot and fc.slots:
        slot = fc.slots.get(slot_id)
        if slot is not None:
            # Return a flat fixture config with just this slot's connections
            fc = FixtureConfig(
                id=fc.id,
                name=fc.name,
                description=fc.description,
                part_id=fc.part_id,
                part_family=fc.part_family,
                part_revision=fc.part_revision,
                connections=slot.connections,
                uut_resource=slot.uut_resource,
            )

    return fc


class StationError(Exception):
    """Error during station instrument setup."""

    pass


@pytest.fixture(scope="session")
def instrument_records(request, station_config, mock_instruments) -> dict[str, InstrumentRecord]:
    """Load and resolve instrument records from configuration.

    This fixture loads instrument files and station config, resolving
    all references to produce InstrumentRecord objects with full
    identity and calibration info.

    Returns:
        Dict mapping role name to InstrumentRecord
    """
    records: dict[str, InstrumentRecord] = {}
    set_instrument_records(records)

    if not station_config:
        return records

    # Try to find and load instrument files
    from litmus.instruments.loader import find_instruments_dir, resolve_station_instruments
    from litmus.store import load_instrument_files

    # Search from pytest invocation directory
    invocation_dir = Path(request.config.invocation_params.dir)
    instruments_dir = find_instruments_dir(invocation_dir)

    instrument_files = {}
    if instruments_dir:
        instrument_files = load_instrument_files(instruments_dir)

    # Resolve station instruments to records
    records = resolve_station_instruments(station_config, instrument_files)

    # Set mocked flag early so InstrumentConnected events capture it
    inst_configs = station_config.instruments or {}
    for role, rec in records.items():
        inline = inst_configs.get(role)
        rec.mocked = mock_instruments or (inline.mock if inline else False)

    set_instrument_records(records)

    return records


@pytest.fixture(scope="session")
def instruments(
    station_config, mock_instruments, instrument_records, _run_scope
) -> Generator[dict[str, Any], None, None]:
    """Create instrument instances from station configuration.

    Instruments are connected at session start and disconnected at end.
    For real hardware, identity is verified against configuration.
    Calibration status is checked and warnings issued if due/expired.

    When --mock-instruments is passed (or LITMUS_MOCK_INSTRUMENTS=1), uses mock
    instruments instead of real drivers. Mocks are config-driven and instant.

    Station config formats supported:

    Legacy format (inline config):
        instruments:
          dmm:
            driver: pymeasure.instruments.keithley.Keithley2000
            resource: GPIB::16::INSTR
            mock_config:
              measure_voltage: 3.3

    New format (reference to instrument files):
        instruments:
          dmm: keithley_dmm_001
        resources:
          keithley_dmm_001: GPIB::16::INSTR

    Returns:
        Dictionary mapping instrument role names to driver instances.
    """
    if not station_config:
        active: dict[str, Any] = {}
        set_active_instruments(active)
        yield active
        return

    inst_configs = station_config.instruments or {}
    session_id = _run_scope._session_id if _run_scope else None
    run_id = _run_scope.test_run.id if _run_scope else None
    event_log = _run_scope.event_log if _run_scope else None

    pool = InstrumentPool(
        session_id=session_id,
        event_log=event_log,
        channel_store=get_channel_store(),
        mock_all=mock_instruments,
        station_id=station_config.id or "",
        run_id=run_id,
    )

    for role, record in instrument_records.items():
        inline_config = inst_configs.get(role)
        use_mock = mock_instruments or (inline_config.mock if inline_config else False)
        record.mocked = use_mock
        try:
            pool.acquire(role, record, inline_config)
        except ValueError:
            continue

    set_active_instruments(pool.active)
    yield pool.active

    pool.release_all()


@pytest.fixture
def instrument(instruments, instrument_records) -> InstrumentAccessor:
    """Accessor for instruments by role with grouping support.

    Usage:
        def test_voltage(instrument):
            dmm = instrument("dmm")

        def test_all_dmms(instrument):
            dmms = instrument.by_type("pymeasure.instruments.keithley.Keithley2000")
    """
    return InstrumentAccessor(instruments, instrument_records)


@pytest.fixture(scope="session")
def uut(
    part,
    fixture_config,
    mock_instruments,
) -> Generator[Any, None, None]:
    """Instantiate and yield the UUT communication driver.

    Resolves the driver class from ``Part.driver`` (the ``part`` fixture)
    and connects using ``FixtureConfig.uut_resource``. Follows the same pattern
    as instrument fixtures — session-scoped, auto-disconnected at teardown.

    Usage in tests:
        def test_firmware_version(uut):
            assert uut.get_version().startswith("2.")

    Returns:
        Connected UUT driver instance, or None if part has no driver.
    """
    if not part or not part.driver:
        yield None
        return

    from litmus.parts.loader import load_part_driver

    driver_class = load_part_driver(part)
    if driver_class is None:
        warnings.warn(
            f"UUT driver {part.driver!r} could not be imported",
            UserWarning,
            stacklevel=2,
        )
        yield None
        return

    # Resolve connection resource from fixture config
    uut_resource = fixture_config.uut_resource if fixture_config else None

    if mock_instruments:
        from litmus.instruments.mocks import Mock

        inst: Any = Mock(driver_class)
        yield inst
        return

    if uut_resource:
        inst = driver_class(uut_resource)
    else:
        inst = driver_class()

    connect_fn = getattr(inst, "connect", None)
    if callable(connect_fn):
        connect_fn()

    yield inst

    # Teardown: disconnect
    try:
        if hasattr(inst, "disconnect"):
            inst.disconnect()
        elif hasattr(inst, "close"):
            inst.close()
    except (OSError, RuntimeError) as exc:
        warnings.warn(f"Failed to cleanup UUT driver: {exc}", stacklevel=2)


@pytest.fixture(scope="session")
def _route_manager(
    instruments,
    fixture_config,
    _run_scope,
) -> Generator[RouteManager | None, None, None]:
    """Session-scoped route manager for switched signal routing.

    Built from fixture points that have routes. Holds locks for the
    session duration. Yields None if no routes are configured.
    """
    if not fixture_config or not instruments:
        yield None
        return

    session_id = _run_scope._session_id if _run_scope else None
    event_log = _run_scope.event_log if _run_scope else None
    station_id = _run_scope.test_run.station_id or "" if _run_scope else ""

    rm = RouteManager(
        connections=fixture_config.connections,
        instruments=instruments,
        session_id=session_id,
        station_id=station_id,
        event_log=event_log,
    )

    if not rm.has_routes:
        yield None
        return

    yield rm
    rm.deactivate_all()


@pytest.fixture
def routes(request) -> Generator[RouteManager | None, None, None]:
    """Per-test route manager for explicit switch routing.

    Use with the context-manager pattern for direct instrument access::

        def test_vout(dmm, routes):
            with routes.for_pin("VOUT"):
                v = dmm.measure_voltage()
            assert 3.2 < v < 3.4

    Yields None if no routes are configured (tests without switching
    can still request this fixture without error).
    """
    rm = _safe_get_session_fixture(request, "_route_manager")
    yield rm
    if rm is not None:
        rm.deactivate_all()


@pytest.fixture(scope="session")
def pins(instruments, fixture_config, _route_manager) -> PinAccessor:
    """UUT-centric pin accessor for tests.

    Resolves UUT pin names to instrument instances. When fixture points
    have switch routes, instruments are wrapped in RoutedProxy for
    transparent route activation on first use.

        def test_output(pins):
            pins["VIN"].set_voltage(5.0)
            pins["VIN"].enable_output()
            assert pins["VOUT"].measure_voltage() > 3.0

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "pins")

    manager = FixtureManager(fixture_config, instruments, route_manager=_route_manager)
    return PinAccessor(manager)


@pytest.fixture(scope="session")
def fixture_manager(instruments, fixture_config, _route_manager) -> FixtureManager:
    """Fixture manager for advanced pin/net routing.

    Provides direct access to the FixtureManager for tests that need
    advanced routing methods beyond the simple pins[] accessor:

        def test_with_net_lookup(fixture_manager):
            connection = fixture_manager.get_connection_for_net("VOUT_3V3")
            instrument = fixture_manager.get_instrument_for_connection(connection.name)

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "fixture_manager")
    return FixtureManager(fixture_config, instruments, route_manager=_route_manager)


# ---------------------------------------------------------------------------
# Worker-mode fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sync(_run_scope):
    """Provide sync point for multi-UUT test coordination.

    In worker mode (_LITMUS_SLOT_ID set), returns a SyncPoint that
    blocks until all slots arrive. In single-slot mode, returns None.

    Usage:
        def test_measure_hot(dmm, sync):
            if sync:
                sync.wait("thermal_soak", timeout=300)
            v = dmm.measure_voltage()
            assert v > 3.0
    """
    del _run_scope  # dependency-only: forces the session EventStore to exist
    from litmus.execution.slot_runner import is_worker_mode

    if not is_worker_mode():
        yield None
        return

    from litmus.execution.sync import get_sync

    event_store = get_event_store()
    sync_point = get_sync(event_store)
    yield sync_point


# Late import — Vector is used by the ``vectors`` fixture below.
from litmus.execution.vectors import Vector  # noqa: E402


@pytest.fixture
def context(_run_scope: RunScope | None) -> Generator[Context, None, None]:
    """Context exposed to tests for ``context.get_param("...")`` / ``.changed()``.

    Wires the active ChannelStore + session_id from the pytest plugin's
    session setup so the ``observe`` fixture (which routes through this
    context) can dispatch typed-array values (``Waveform``, ndarray,
    list-of-scalars) to ChannelStore and blob values to FileStore.
    Without this wiring, a bare ``Context()`` has no channel store, so
    every Waveform falls through to the FileStore blob path and then
    fails on the session_id guard.

    Also pushes the constructed Context onto the
    ``_current_context_var`` ContextVar so module-level surfaces that
    resolve session via that var (``litmus.files.write``,
    ``litmus.files.stream``, ``litmus.channels.stream`` indirectly)
    find the active session. The push is per-test (no leakage between
    tests); the token-based reset restores the prior context on
    teardown.

    ``_run_scope`` is annotated as ``RunScope | None`` because some
    pytester subtests deliberately override the ``_run_scope`` autouse
    fixture to yield ``None`` (neutralizes the duckdb dependency in
    child processes). Falls back to a bare ``Context()`` when the run
    scope is unwired — matches the pre-wiring behaviour for that case.
    """
    if _run_scope is None:
        ctx = Context()
    else:
        ctx = Context(
            channel_store=get_channel_store(),
            session_id=_run_scope._session_id,
        )
    token = push_current_context(ctx)
    try:
        yield ctx
    finally:
        reset_current_context(token)


@pytest.fixture
def stream(context: Context) -> Callable[..., str]:
    """Callable fixture: ``stream(name, sample[, namespace=...])`` — append a sample.

    The third sibling test-author intent verb (alongside ``observe`` /
    ``verify``). Always routes to ChannelStore. Per §3 of the design
    doc — explicit per-store streaming, never auto-associates with
    the active vector. Use ``observe(name, channel_handle)`` to
    associate a stream with a vector.

    Both shapes are available — this bare callable (pytest-idiomatic)
    and :meth:`Context.stream` (programmatic / non-pytest). Both
    route through the same ``Context.stream`` body so the verb
    behaves identically.

    Example::

        def test_iv_curve(stream, observe, psu, dmm):
            observe("iv_curve.i", "channel://iv_curve.i")   # vector association
            for v in [0.0, 0.5, 1.0, 1.5, 2.0]:
                psu.set_voltage(v)
                stream("iv_curve.i", dmm.read_current())
    """

    def _stream(name: str, sample: Any, *, namespace: str | None = None) -> str:
        return context.stream(name, sample, namespace=namespace)

    return _stream


@pytest.fixture
def observe(context: Context) -> Callable[..., None]:
    """Callable fixture: ``observe(name, value[, namespace=...])`` — stash in vector.

    Per §3 of the design doc, ``observe`` is one of three sibling
    test-author verbs (``observe`` / ``verify`` / ``stream``).
    Exposed both as a method on Context (``context.observe(...)``,
    for programmatic / non-pytest use) and as this bare callable
    fixture (the pytest-idiomatic shape).

    Both shapes route through the same :meth:`Context.observe`
    implementation, so the verb behaves identically regardless of
    which surface the test author reaches for. Symmetric with the
    ``verify`` fixture (which has always been bare).

    Examples:
        ``observe("temperature", 23.5)`` — scalar lands inline
        ``observe("scope.cap", wf)`` — Waveform → ChannelStore
        ``observe("voltage", 3.31, namespace="psu_a")`` — namespaced
    """

    def _observe(name: str, value: Any, *, namespace: str | None = None) -> None:
        context.observe(name, value, namespace=namespace)

    return _observe


@pytest.fixture
def connections(
    _litmus_resolve_connections: None,  # noqa: F811  # pytest fixture-ordering dep
    context: Context,
) -> ConnectionIterator | None:
    """Active fixture connections for the current test.

    Returns the :class:`ConnectionIterator` resolved from
    ``litmus_characteristics`` / ``litmus_connections`` markers, or ``None`` when
    no markers are declared. Symmetric with ``pins``: tests that take
    fixture connections use this fixture instead of reaching through
    ``context.connections``.

    Iterator semantics are unchanged from ``ctx.connections``::

        def test_foo(connections, dmm):
            for conn in connections:        # drives _active_connection_var
                v = dmm.measure_voltage()

    Mapping access is also available::

        def test_named(connections, dmm):
            with connections["vout"]:        # (future: switch lifecycle)
                v = dmm.measure_voltage()
    """
    return getattr(context, "connections", None)


@pytest.fixture
def verify() -> VerifyFn:
    """Callable fixture: ``verify(name, value[, limit=])`` — log + assert.

    Thin pytest wrapper around the runner-neutral
    :func:`litmus.execution.verify.build_verify_callable` — logs a
    measurement, resolves a Limit from the chain, stamps the outcome,
    and raises :class:`LimitFailure` on FAIL.
    """
    return build_verify_callable()


@pytest.fixture
def measure() -> MeasureFn:
    """Callable fixture: ``measure(name, value[, limit=])`` — record-only.

    The record-only sibling of ``verify`` (alongside ``observe`` /
    ``stream``). Stamps one measurement row with ``Outcome.DONE`` and
    never judges or raises on a missing limit — use it when a value
    should be captured but not pass/fail checked (characterization,
    diagnostics, logged context). Same row primitive underneath as
    ``verify``::

        def test_characterize(measure, dmm):
            measure("vout", dmm.measure_dc_voltage())

    Thin pytest wrapper around the runner-neutral
    :func:`litmus.execution.verify.build_measure_callable`. Both shapes
    — this bare callable and :meth:`Context.measure` — route through the
    same body so the verb behaves identically.
    """
    return build_measure_callable()


@pytest.fixture
def limits() -> LimitsFn:
    """Read-only ``name → Limit`` mapping for the active test.

    Resolves from the same chain ``verify`` uses. ``limits[name]``
    raises ``KeyError`` when no limit is configured for ``name`` —
    honest for ad-hoc pythonic assertions::

        assert v in limits["vout"]
    """
    from litmus.execution.verify import _LimitsMapping

    return _LimitsMapping(dict(get_active_limits()))


class _VectorIterator:
    """Iterator for self-loop mode: walks the pre-expanded matrix in a single test case.

    Built by the :func:`vectors` fixture when a test takes ``vectors`` in
    its signature. Each ``__next__`` pushes the current row's params into
    ``_active_vector_params_var`` / ``_active_vector_index_var`` so that:

    * ``logger.measure`` stamps ``in_*`` columns + ``meas_vector_index``
      from active state.
    * A fresh :class:`TestVector` is appended to the current step per
      iteration, so parquet rows land on distinct records.
    * ``context.get_param``, ``.changed``, ``.last``, and ``.params``
      reflect the current row; ``_prev`` chains to the prior iteration.

    On cleanup the ContextVars restore; if the matrix is non-empty and
    the test body iterated zero times, the fixture fails the test.
    """

    def __init__(self, matrix: list[Vector], ctx: Context) -> None:
        self._matrix = matrix
        self._ctx = ctx
        self._consumed = 0

    def __len__(self) -> int:
        return len(self._matrix)

    def __iter__(self) -> Iterator[Vector]:
        # Generator: each yield wraps push_current_vector in its own
        # try/finally so the token's lifetime is exactly the iteration
        # body. No tokens are held across iterations and no out-of-band
        # cleanup is needed — ContextVar push/reset symmetry is preserved
        # naturally by the generator frame.
        prev_snapshot: Context | None = None
        for i, vec in enumerate(self._matrix):
            params = vec.params()

            set_active_vector_params(dict(params))
            set_active_vector_index(i)

            # Chain prev-context for ``context.changed()`` / ``.last()``.
            if prev_snapshot is not None:
                self._ctx._prev = prev_snapshot
            self._ctx._params.clear()
            self._ctx._params.update(params)

            snapshot = Context(channel_store=self._ctx._channel_store)
            snapshot._params = dict(params)
            prev_snapshot = snapshot

            # Fresh TestVector per iteration so vector_index / params
            # stamp distinctly. Only push when a step already exists
            # (logger may still auto-create the first one lazily on
            # measure).
            step = get_current_step()
            if step is not None:
                new_vector = TestVector(index=i, params=dict(params))
                step.vectors.append(new_vector)
                token = push_current_vector(new_vector)
                self._consumed += 1
                try:
                    yield vec
                finally:
                    reset_current_vector(token)
            else:
                self._consumed += 1
                yield vec

    @property
    def consumed(self) -> int:
        return self._consumed


@pytest.fixture
def vectors(request: pytest.FixtureRequest) -> Iterator[_VectorIterator]:
    """Pre-expanded vector matrix for self-loop test mode.

    Taking this fixture in a test signature switches collection to
    **self-loop mode**: every source of vectors (native
    ``@pytest.mark.parametrize``, sidecar ``vectors:``, profile
    overrides) is consolidated into one matrix at collection time, and
    the test runs as a single pytest case. The test body iterates the
    matrix itself::

        def test_sweep(vectors, psu, dmm, logger):
            for v in vectors:
                psu.set_voltage(v["vin"])
                logger.measure("vout", dmm.measure_dc_voltage())

    Each ``for`` iteration pushes the row's params and index into the
    active-vector state, so ``logger.measure`` / ``verify`` / ``spec``
    see the same row-scoped context they would in normal mode.

    Fails the test at teardown if the matrix is non-empty but the body
    iterated zero times — silent skips hide bugs.
    """
    parent = request.node.parent
    matrix_map = parent.stash.get(VECTORS_MATRIX_KEY, {}) if parent is not None else {}
    matrix = matrix_map.get(request.node.originalname, [])
    ctx: Context = request.getfixturevalue("context")
    it = _VectorIterator(matrix=matrix, ctx=ctx)
    try:
        yield it
    finally:
        set_active_vector_params({})
        set_active_vector_index(0)
        if len(matrix) > 0 and it.consumed == 0:
            pytest.fail(
                f"{request.node.nodeid}: ``vectors`` fixture was not iterated "
                f"({len(matrix)} rows available, 0 consumed). Use "
                "``for v in vectors: ...`` in the test body.",
                pytrace=False,
            )


@pytest.fixture
def prompt(request: pytest.FixtureRequest) -> Callable[..., Any]:
    """Operator prompt fixture.

    Resolves prompts declared via ``litmus_prompts`` markers (file-level,
    class-scoped, per-test, or inline ``@pytest.mark.litmus_prompts``).
    Each marker carries one or more entries keyed by name::

        @pytest.mark.litmus_prompts(
            operator_setup={"message": "Insert UUT", "prompt_type": "confirm"},
            pick_fixture={"message": "Pick fixture", "prompt_type": "choice",
                          "choices": ["bench_01", "bench_02"]},
        )
        def test_setup(prompt):
            prompt("operator_setup")          # confirm  -> True
            chosen = prompt("pick_fixture")   # choice   -> selected string

    ``prompt(key)`` looks the entry up by key. ``prompt()`` (no args)
    works when exactly one entry is in scope. Routing of the prompt
    itself goes through :func:`litmus.prompts.ask` — explicit handler
    (UI runner) → ``LITMUS_AUTO_CONFIRM=1`` → tty fallback.
    """
    # Walk listchain root-to-leaf so more-specific markers win on key
    # conflict via ``update``. Within a node, ``own_markers`` preserves
    # insertion order. Cascade markers carry typed PromptConfig
    # instances; inline decorators carry raw dicts which Pydantic
    # validates here.
    merged: dict[str, PromptConfig] = {}
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name != "litmus_prompts":
                continue
            for key, entry in marker.kwargs.items():
                merged[key] = (
                    entry if isinstance(entry, PromptConfig) else PromptConfig.model_validate(entry)
                )

    def _ask(key: str | None = None) -> Any:
        if key is None:
            if not merged:
                raise pytest.UsageError(
                    f"prompt() called with no key but no litmus_prompts "
                    f"markers are in scope for {request.node.nodeid}"
                )
            if len(merged) > 1:
                raise pytest.UsageError(
                    f"prompt() called with no key but {len(merged)} prompts "
                    f"are in scope for {request.node.nodeid}: "
                    f"{sorted(merged)}. Pass an explicit key."
                )
            entry = next(iter(merged.values()))
        else:
            if key not in merged:
                known = sorted(merged) or "none"
                raise pytest.UsageError(
                    f"prompt({key!r}): no such key in litmus_prompts markers "
                    f"for {request.node.nodeid}; known keys: {known}"
                )
            entry = merged[key]
        return ask_prompt(entry)

    return _ask
