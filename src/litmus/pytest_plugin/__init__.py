"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from litmus.data.models import TestVector
from litmus.execution._state import (
    _active_vector_index_var,
    get_active_facets,
    get_active_limits,
    get_active_profile,
    get_channel_store,
    get_collected_items,
    get_current_step,
    get_event_store,
    get_instrument_records,
    get_session_inputs,
    push_current_vector,
    set_active_instruments,
    set_active_product_context,
    set_active_vector_index,
    set_active_vector_params,
    set_channel_store,
    set_event_store,
    set_instrument_records,
)
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.connections import ConnectionIterator
from litmus.execution.decorators import set_current_logger
from litmus.execution.harness import Context
from litmus.execution.instrument_events import emit_instrument_events
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.execution.metadata import build_run_metadata
from litmus.execution.outputs import (
    create_subscriber,
    find_format_transport_callback,
    run_configured_outputs,
)
from litmus.execution.profiles import resolve_test_phase
from litmus.execution.verify import (
    LimitsFn,
    VerifyFn,
    build_verify_callable,
)
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.station import StationConfig
from litmus.models.test_config import FixtureConfig, PromptConfig
from litmus.products.context import ProductContext
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
    _reseat_current_logger,  # noqa: F401
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
    pytest_addoption,
    pytest_collection_modifyitems,
    pytest_configure,
    pytest_generate_tests,
    pytest_load_initial_conftests,
    pytest_report_header,
    pytest_runtest_call,
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
    "pytest_collection_modifyitems",
    "pytest_configure",
    "pytest_generate_tests",
    "pytest_load_initial_conftests",
    "pytest_report_header",
    "pytest_runtest_call",
    "pytest_runtest_setup",
    "pytest_runtestloop",
    "pytest_sessionfinish",
    "pytest_sessionstart",
]


def _prompt_for_slot_serials(
    slot_ids: list[str],
    test_phase: str,
) -> dict[str, str]:
    """Prompt for DUT serial for each slot.

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
            "Provide --fixture-config <path> or create a fixtures/*.yaml file."
        )
    if not instruments:
        raise pytest.UsageError(
            f"The '{feature}' fixture requires instruments. "
            "Provide --station-config <path> or create a stations/*.yaml file."
        )


def _build_run_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Pytest adapter — read session fixtures + CLI options, delegate to runner-neutral builder."""
    from litmus.execution.profiles import ProfileError, validate_phase_wiring
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
    if station_config is not None:
        st_id = getattr(station_config, "station_type", None)
        if st_id:
            station_type_template = load_station_type(st_id, project_root=request.config.rootpath)
    try:
        validate_phase_wiring(
            profile=profile,
            station_config=station_config,
            fixture_config=fixture_config,
            station_type_template=station_type_template,
        )
    except ProfileError as exc:
        raise pytest.UsageError(str(exc)) from exc

    return build_run_metadata(
        dut_serial=request.config.getoption("--dut-serial"),
        dut_part_number=request.config.getoption("--dut-part-number"),
        dut_revision=request.config.getoption("--dut-revision"),
        dut_lot_number=request.config.getoption("--dut-lot-number"),
        station_id=_resolve_station_id(request.config),
        station_config=station_config,
        fixture_config=fixture_config,
        product_context=_safe_get_session_fixture(request, "product_context"),
        operator_id=request.config.getoption("--operator"),
        project_dir=request.config.rootpath,
        results_dir=request.config.getoption("--results-dir"),
        test_phase=resolve_test_phase(requested_phase, mocks_active=_mocks_active(request.config)),
        profile_name=request.config.getoption("--litmus-profile", default=None),
        profile_facets=dict(get_active_facets()),
        session_inputs=dict(get_session_inputs()),
        instrument_records=_safe_get_session_fixture(request, "instrument_records"),
    )


def _is_multi_slot_worker() -> bool:
    """Return True when this process is one of N>1 workers in a multi-slot run."""
    return (
        os.environ.get("LITMUS_SLOT_ID") is not None
        and int(os.environ.get("LITMUS_SLOT_COUNT", "1")) > 1
    )


def _setup_event_log_and_subscribers(
    logger: TestRunLogger, results_path: Path, session_id: UUID
) -> Any:
    """Wire EventStore + EventLog + default and configured subscribers.

    Returns the EventStore (existing or newly created) so the teardown
    helper can close it after the run.
    """
    from litmus.data.backends.parquet import ParquetSubscriber
    from litmus.data.channels.store import ChannelStore
    from litmus.data.event_store import EventStore
    from litmus.data.subscribers import get_subscriber_class

    event_store = get_event_store()
    if event_store is None:
        event_store = EventStore(_results_dir=results_path)
        set_event_store(event_store)

    event_log = event_store.get_event_log(session_id)
    logger.event_log = event_log

    parquet_on_output = find_format_transport_callback("parquet", results_path)
    event_log.add_subscriber(ParquetSubscriber(results_path, on_output=parquet_on_output))

    channels_on_output = find_format_transport_callback("channels", results_path)
    channel_store = ChannelStore(
        results_path / "channels",
        session_id,
        serve=True,
        on_output=channels_on_output,
    )
    channel_store.open()
    set_channel_store(channel_store)

    # User-configured subscriber formats from litmus.yaml outputs:
    try:
        from litmus.store import load_project_config

        config = load_project_config()
        for output_cfg in config.outputs:
            fmt = output_cfg.format
            if fmt and fmt not in {"parquet", "channels"}:
                cls = get_subscriber_class(fmt)
                if cls is not None:
                    sub = create_subscriber(cls, fmt, output_cfg, results_path, session_id)
                    event_log.add_subscriber(sub)
    except (ValidationError, OSError, KeyError) as exc:
        warnings.warn(
            f"Failed to register configured output subscribers: {exc}",
            stacklevel=2,
        )

    return event_store


def _emit_session_start_events(logger: TestRunLogger) -> None:
    """Emit SessionStarted (orchestrator only) + RunStarted + per-instrument + StepsDiscovered."""
    from litmus.data.events import RunStarted, SessionStarted, StepsDiscovered

    event_log = logger.event_log
    if event_log is None:
        return

    if not _is_multi_slot_worker():
        event_log.emit(
            SessionStarted.from_station(
                session_id=logger._session_id,
                station_id=logger.test_run.station_id,
                station_name=logger.test_run.station_name,
                station_type=logger.test_run.station_type,
                station_location=logger.test_run.station_location,
                station_hostname=logger.test_run.station_hostname,
                operator_id=logger.test_run.operator_id,
                operator_name=logger.test_run.operator_name,
                fixture_id=logger.test_run.fixture_id,
            )
        )

    env_slot_id = os.environ.get("LITMUS_SLOT_ID")
    env_slot_index_str = os.environ.get("LITMUS_SLOT_INDEX")
    env_slot_index = int(env_slot_index_str) if env_slot_index_str else None

    event_log.emit(
        RunStarted(
            session_id=logger._session_id,
            run_id=logger.test_run.id,
            slot_id=env_slot_id,
            slot_index=env_slot_index,
            station_id=logger.test_run.station_id,
            station_name=logger.test_run.station_name,
            station_type=logger.test_run.station_type,
            station_location=logger.test_run.station_location,
            station_hostname=logger.test_run.station_hostname,
            dut_serial=logger.test_run.dut.serial,
            dut_part_number=logger.test_run.dut.part_number,
            dut_revision=logger.test_run.dut.revision,
            dut_lot_number=logger.test_run.dut.lot_number,
            product_id=logger.test_run.product_id,
            product_name=logger.test_run.product_name,
            product_revision=logger.test_run.product_revision,
            operator_id=logger.test_run.operator_id,
            operator_name=logger.test_run.operator_name,
            fixture_id=logger.test_run.fixture_id,
            test_phase=logger.test_run.test_phase,
            project_name=logger.test_run.project_name,
            git_commit=logger.test_run.git_commit,
            git_branch=logger.test_run.git_branch,
            git_remote=logger.test_run.git_remote,
            environment_json=logger.test_run.environment_json,
            custom_metadata=dict(logger.test_run.custom_metadata),
            pid=os.getpid(),
        )
    )

    _emit_instrument_events(logger, event_log)

    collected = get_collected_items()
    if collected:
        event_log.emit(
            StepsDiscovered(
                session_id=logger._session_id,
                run_id=logger.test_run.id,
                items=[ci.model_dump() for ci in collected],
            )
        )


def _teardown_logger(logger: TestRunLogger, event_store: Any, results_dir: str) -> None:
    """Close subscribers, finalize the run, emit SessionEnded, run configured outputs."""
    from litmus.data.events import SessionEnded

    # ChannelStore closes before the event log so its subscribers see the
    # final flush before the event log shuts subscribers down.
    cs = get_channel_store()
    if cs is not None:
        cs.close()
        set_channel_store(None)

    # finalize() emits RunEnded; it does not close the event log itself.
    test_run = logger.finalize()

    if logger.event_log is not None:
        if not _is_multi_slot_worker():
            logger.event_log.emit(
                SessionEnded(
                    session_id=logger._session_id,
                    outcome=test_run.outcome.value,
                )
            )
        logger.event_log.close()

    if event_store is not None:
        event_store.close()
        set_event_store(None)

    run_configured_outputs(test_run, str(test_run.id), results_dir)


@pytest.fixture(scope="session", autouse=True)
def logger(request) -> Generator[TestRunLogger, None, None]:
    """Provide test run logger for the session.

    Autouse so every test (and the ``verify`` / ``context`` fixtures
    that route through ``set_current_logger``) sees an active logger.
    Snapshots config at run start; streams events to subscribers
    declared by ``litmus.yaml: outputs:`` plus the always-on parquet
    + channels defaults.

    The body delegates to focused helpers — :func:`_setup_event_log_and_subscribers`
    wires the event log, :func:`_emit_session_start_events` fires the
    Session/Run/StepsDiscovered triplet, :func:`_teardown_logger` closes
    everything in the right order at session end.
    """
    from litmus.data.results_dir import resolve_results_dir

    meta = _build_run_metadata(request)
    results_dir = meta["results_dir"]
    if not results_dir:
        results_dir = str(resolve_results_dir())
        meta["results_dir"] = results_dir

    env_session_id = os.environ.get("LITMUS_SESSION_ID")
    session_id = UUID(env_session_id) if env_session_id else uuid4()
    meta["session_id"] = session_id

    env_dut_serial = os.environ.get("LITMUS_DUT_SERIAL")
    if env_dut_serial:
        meta["dut_serial"] = env_dut_serial

    logger = TestRunLogger(**meta)

    event_store: Any = None
    if results_dir:
        event_store = _setup_event_log_and_subscribers(logger, Path(results_dir), session_id)
        _emit_session_start_events(logger)

    set_current_logger(logger)
    try:
        yield logger
    finally:
        # Capture not-started steps onto the run manifest before finalize.
        logger.test_run.collected_items = get_collected_items()
        _teardown_logger(logger, event_store, results_dir)
        set_current_logger(None)


def _emit_instrument_events(logger: TestRunLogger, event_log: Any) -> None:
    """Pytest adapter — read ContextVar records, delegate to runner-neutral emitter."""
    emit_instrument_events(logger, event_log, get_instrument_records())


@pytest.fixture(scope="session")
def run_context(logger) -> RunContext:
    """Provide run context for adding custom metadata.

    This is the run-level context that persists across all tests in the session.
    For step or vector-scoped context, use the `context` fixture instead.

    Usage:
        def test_example(run_context):
            run_context.set("operator_badge", "EMP-12345")
            run_context.set("fixture_serial", "FIX-001")
    """
    return logger.run_context


@pytest.fixture(scope="session")
def product_context(request) -> ProductContext | None:
    """Provide product context for spec-driven testing.

    Resolution chain (first match wins):

    1. ``--spec <path>`` — explicit YAML path.
    2. ``--product <id>`` — look up ``products/<id>.yaml`` (mirrors
       ``--station``/``--fixture`` resolution).
    3. ``--dut-part-number <pn>`` — content match against
       ``product.part_number:`` across ``products/*.yaml``.
    4. Single-file fallback when ``products/`` holds exactly one file.
    5. ``None`` — bringup tier without a product YAML.

    Usage in tests:
        def test_voltage(product_context, dmm):
            limit = product_context.get_limit("output_voltage", temperature=25)
            value = dmm.measure_dc_voltage()
            # Use limit for validation...

    Returns:
        :class:`ProductContext`, or ``None`` if no product YAML is loaded.
    """
    spec_path = request.config.getoption("--spec")
    product_id = request.config.getoption("--product")
    guardband = float(request.config.getoption("--guardband"))
    part_number = request.config.getoption("--dut-part-number")

    ctx = None

    if spec_path:
        ctx = ProductContext.from_file(spec_path, guardband_pct=guardband)
    elif product_id:
        product_path = _find_yaml_in_subdir(request.config, "products", f"{product_id}.yaml")
        if product_path is None:
            raise pytest.UsageError(
                f"--product={product_id!r} did not find products/{product_id}.yaml. "
                "Use --spec=<path> for an explicit path."
            )
        ctx = ProductContext.from_file(product_path, guardband_pct=guardband)
    else:
        ctx = _autodiscover_product(request.config, guardband, part_number)

    set_active_product_context(ctx)
    return ctx


def _autodiscover_product(
    config: pytest.Config,
    guardband: float,
    part_number: str | None,
) -> ProductContext | None:
    """Pick a product YAML from ``products/`` in the project or cwd.

    Selection rules:
    1. If ``--dut-part-number`` is set and exactly one product's
       ``part_number:`` matches (case-insensitive), use it.
    2. If ``--dut-part-number`` is set but no file matches, raise
       ``pytest.UsageError`` — a typo in the selector is worse than a
       silent wrong-product pick.
    3. Otherwise, take the first sorted ``products/*.yaml`` file. If
       the directory holds multiple products and ``--dut-part-number``
       was not provided, raise ``pytest.UsageError`` — Rev-B flows
       need an explicit selector.
    """
    search_roots = [
        config.rootpath,
        Path(config.invocation_params.dir),
    ]

    product_files: list[Path] = []
    for root in search_roots:
        products_dir = root / "products"
        if not products_dir.exists():
            continue
        product_files = [
            p for p in sorted(products_dir.rglob("*.yaml")) if not p.name.startswith("_")
        ]
        if product_files:
            break

    if not product_files:
        return None

    if part_number:
        matches: list[Path] = []
        pn_lower = part_number.lower()
        for yaml_file in product_files:
            try:
                loaded = ProductContext.from_file(yaml_file, guardband_pct=guardband)
            except (ValueError, OSError):
                continue
            if (loaded.product.part_number or "").lower() == pn_lower:
                matches.append(yaml_file)
        if len(matches) == 1:
            return ProductContext.from_file(matches[0], guardband_pct=guardband)
        if not matches:
            raise pytest.UsageError(
                f"--dut-part-number={part_number!r} did not match any product in "
                f"products/. Available: " + ", ".join(sorted(p.stem for p in product_files))
            )
        raise pytest.UsageError(
            f"--dut-part-number={part_number!r} matched multiple products: "
            + ", ".join(sorted(str(m.relative_to(m.parents[1])) for m in matches))
            + ". Use --spec <path> to disambiguate."
        )

    if len(product_files) > 1:
        raise pytest.UsageError(
            f"products/ has {len(product_files)} YAML files "
            f"({', '.join(p.stem for p in product_files)}); "
            "pass --product <id>, --dut-part-number <pn>, or --spec <path> to choose one."
        )

    return ProductContext.from_file(product_files[0], guardband_pct=guardband)


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
    """Load station configuration from --station-config option.

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
    """Load fixture configuration from --fixture-config option.

    In worker mode (``LITMUS_SLOT_ID`` set), extracts this slot's points
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
    slot_id = os.environ.get("LITMUS_SLOT_ID")
    if slot_id and fc.is_multi_slot and fc.slots:
        slot = fc.slots.get(slot_id)
        if slot is not None:
            # Return a flat fixture config with just this slot's connections
            fc = FixtureConfig(
                id=fc.id,
                name=fc.name,
                description=fc.description,
                product_id=fc.product_id,
                product_family=fc.product_family,
                product_revision=fc.product_revision,
                connections=slot.connections,
                dut_resource=slot.dut_resource,
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
    station_config, mock_instruments, instrument_records, logger
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
    session_id = logger._session_id if logger else None
    run_id = logger.test_run.id if logger else None
    event_log = logger.event_log if logger else None

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
def dut(
    product_context,
    fixture_config,
    mock_instruments,
) -> Generator[Any, None, None]:
    """Instantiate and yield the DUT communication driver.

    Resolves the driver class from ``Product.driver`` (loaded via product_context)
    and connects using ``FixtureConfig.dut_resource``. Follows the same pattern
    as instrument fixtures — session-scoped, auto-disconnected at teardown.

    Usage in tests:
        def test_firmware_version(dut):
            assert dut.get_version().startswith("2.")

    Returns:
        Connected DUT driver instance, or None if product has no driver.
    """
    if not product_context or not product_context.product.driver:
        yield None
        return

    from litmus.products.loader import load_product_driver

    driver_class = load_product_driver(product_context.product)
    if driver_class is None:
        warnings.warn(
            f"DUT driver {product_context.product.driver!r} could not be imported",
            UserWarning,
            stacklevel=2,
        )
        yield None
        return

    # Resolve connection resource from fixture config
    dut_resource = fixture_config.dut_resource if fixture_config else None

    if mock_instruments:
        from litmus.instruments.mocks import Mock

        inst: Any = Mock(driver_class)
        yield inst
        return

    if dut_resource:
        inst = driver_class(dut_resource)
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
        warnings.warn(f"Failed to cleanup DUT driver: {exc}", stacklevel=2)


@pytest.fixture(scope="session")
def _route_manager(
    instruments,
    fixture_config,
    logger,
) -> Generator[RouteManager | None, None, None]:
    """Session-scoped route manager for switched signal routing.

    Built from fixture points that have routes. Holds locks for the
    session duration. Yields None if no routes are configured.
    """
    if not fixture_config or not instruments:
        yield None
        return

    session_id = logger._session_id if logger else None
    event_log = logger.event_log if logger else None
    station_id = ""
    if logger and hasattr(logger, "_station_id"):
        station_id = getattr(logger, "_station_id", "")

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

    Resolves DUT pin names to instrument instances. When fixture points
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
def sync(logger):
    """Provide sync point for multi-DUT test coordination.

    In worker mode (LITMUS_SLOT_ID set), returns a SyncPoint that
    blocks until all slots arrive. In single-slot mode, returns None.

    Usage:
        def test_measure_hot(dmm, sync):
            if sync:
                sync.wait("thermal_soak", timeout=300)
            v = dmm.measure_voltage()
            assert v > 3.0
    """
    del logger  # dependency-only: forces the session EventStore to exist
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
def context() -> Context:
    """Context exposed to tests for ``context.get_param("...")`` / ``.changed()``."""
    return Context()


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
        self._i = 0
        self._consumed = 0
        self._prev_snapshot: Context | None = None

    def __iter__(self) -> _VectorIterator:
        return self

    def __len__(self) -> int:
        return len(self._matrix)

    def __next__(self) -> Vector:
        if self._i >= len(self._matrix):
            raise StopIteration

        vec = self._matrix[self._i]
        params = vec.params()

        set_active_vector_params(dict(params))
        set_active_vector_index(self._i)

        # Chain prev-context for ``context.changed()`` / ``.last()``.
        if self._prev_snapshot is not None:
            self._ctx._prev = self._prev_snapshot
        self._ctx._params.clear()
        self._ctx._params.update(params)

        snapshot = Context(channel_store=self._ctx._channel_store)
        snapshot._params = dict(params)
        self._prev_snapshot = snapshot

        # Fresh TestVector per iteration so vector_index / params stamp
        # distinctly. Only do this if a step already exists (logger may
        # still auto-create the first one lazily on measure).
        step = get_current_step()
        if step is not None:
            new_vector = TestVector(index=self._i, params=dict(params))
            step.vectors.append(new_vector)
            push_current_vector(new_vector)

        self._i += 1
        self._consumed += 1
        return vec

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
        try:
            _active_vector_index_var.set(0)
        except LookupError:
            pass
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
            operator_setup={"message": "Insert DUT", "prompt_type": "confirm"},
            pick_fixture={"message": "Pick fixture", "prompt_type": "choice",
                          "choices": ["bench_01", "bench_02"]},
        )
        def test_setup(prompt):
            prompt("operator_setup")          # confirm  -> True
            chosen = prompt("pick_fixture")   # choice   -> selected string

    ``prompt(key)`` looks the entry up by key. ``prompt()`` (no args)
    works when exactly one entry is in scope. Routing of the prompt
    itself goes through :func:`litmus.prompts.ask` — explicit handler
    (UI runner) → ``LITMUS_PROMPT_MODE=auto-confirm`` → tty fallback.
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
