"""Test harness for vector-based test execution.

The TestHarness owns vectors, handles loop iteration, retry logic, prompting,
and measurement logging. It can be used directly (without pytest) or via
the pytest plugin fixtures.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from litmus.data.models import Measurement, Outcome, TestStep, TestVector, _utcnow, escalate_outcome
from litmus.data.ref import classify_value
from litmus.execution._state import (
    get_active_characteristic,
    get_active_limits,
    get_active_product_context,
    get_active_station_config,
    get_active_test_characteristics,
    get_current_code_identity,
    get_current_logger,
    get_current_step,
    get_current_vector,
    push_current_step,
    push_current_vector,
    reset_current_step,
    reset_current_vector,
)
from litmus.execution.vectors import Vector, expand_vectors
from litmus.models.test_config import Limit, MeasurementLimitConfig, PromptConfig, RetryConfig
from litmus.prompts import ask

if TYPE_CHECKING:
    from litmus.data.models import TestRun
    from litmus.execution.logger import TestRunLogger
    from litmus.models.station import StationConfig
    from litmus.products.context import ProductContext


class LimitsView(Mapping[str, MeasurementLimitConfig]):
    """Dict-like view over the merged ``litmus_limits`` for the active test.

    Wraps the ``_active_limits_var`` dict and exposes the standard
    :class:`Mapping` surface (``__getitem__`` / ``__iter__`` /
    ``__len__`` / ``__contains__`` / ``keys`` / ``values`` / ``items``)
    plus a :meth:`for_characteristic` filter for per-char scoping.

    The view is a snapshot: accessors return live references into the
    underlying dict. Tests should treat it as read-only.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, MeasurementLimitConfig]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> MeasurementLimitConfig:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def for_characteristic(self, char_id: str) -> dict[str, MeasurementLimitConfig]:
        """Return entries whose ``characteristic:`` field equals ``char_id``.

        **Result depends on iteration state.** Two cases:

        * Entries with an explicit ``characteristic: <id>`` field are
          always included when ``id == char_id``.
        * Entries with **no** ``characteristic:`` field are included
          only when the active-char ContextVar matches ``char_id``.
          That ContextVar is pushed by ``ConnectionIterator`` during
          ``for connection in ctx.connections.for_characteristic(...)``
          (and during plain iteration over a single-char default).
          Outside any iteration block, the ContextVar is ``None`` and
          field-less entries are omitted from the result.

        Practical consequence: calling
        ``ctx.limits.for_characteristic("rail_3v3")`` from inside a
        ``ctx.connections.for_characteristic("rail_3v3")`` loop returns
        every limit applicable to that char (explicit + inherited);
        calling it from the test body's top level returns only
        explicit matches. Test code that wants the unconditional
        explicit-only view should filter ``ctx.limits`` directly:
        ``{k: v for k, v in ctx.limits.items() if v.characteristic == "rail_3v3"}``.
        """
        active = get_active_characteristic()
        return {
            label: cfg
            for label, cfg in self._data.items()
            if cfg.characteristic == char_id or (cfg.characteristic is None and active == char_id)
        }


class Context:
    """Hierarchical context with scoped inheritance.

    Data set at parent level is inherited by children. Children can override
    parent values locally. This enables run → step → vector scoping:

    - **Run level**: Data visible to all steps and vectors
    - **Step level**: Data visible to all vectors in that step
    - **Vector level**: Data visible only to that vector

    The context provides semantic methods for data capture:
    - configure(): Record configuration/stimulus values (→ in_*)
    - observe(): Record measured context/observations (→ out_*)

    Example usage:
        def test_output_voltage(psu, dmm, temp_probe, context):
            # Log environmental observation
            context.observe("temp_probe.temperature", temp_probe.read())
            context.observe("temp_probe.humidity", temp_probe.read_humidity())

            # Configure stimulus (if not already from vector params)
            context.configure("psu.actual_voltage", psu.read_voltage())

            # THE measurement
            return dmm.measure_dc_voltage()

    Naming convention:
    - Spec conditions use bare names: temperature, load (match spec condition keys)
    - Implementation details use fixture prefix: psu.voltage, dmm.sample_count
    """

    def __init__(
        self,
        parent: Context | None = None,
        prev: Context | None = None,
        harness: TestHarness | None = None,
        channel_store: Any | None = None,
    ):
        """Initialize context with optional parent for inheritance.

        Args:
            parent: Parent context to inherit values from. If None, this is a root context.
            prev: Previous sibling context (for change detection across vectors).
            harness: TestHarness reference for accessing limits and other harness features.
            channel_store: Optional ChannelStore for direct writes of numeric data.
        """
        self._parent = parent
        self._prev = prev
        self._harness = harness
        self._channel_store = channel_store
        self._params: dict[str, Any] = {}
        self._observations: dict[str, Any] = {}
        # ``connections`` iterates :class:`FixtureConnection` objects the
        # test declares via ``litmus_characteristics`` / ``litmus_connections``
        # markers. Populated by the pytest-native plugin's
        # ``_litmus_resolve_connections`` autouse fixture; ``None`` when
        # the test declares no spec/connections markers. Test body:
        # ``for _ in ctx.connections: ...`` — iterating pushes the
        # active :class:`FixtureConnection` into ``_active_connection_var``
        # so driver fixtures route and the logger stamps traceability.
        self.connections: Any = None

    def child(self) -> Context:
        """Create a child context that inherits from this one.

        Returns:
            New Context with this context as parent.
        """
        return Context(parent=self, harness=self._harness, channel_store=self._channel_store)

    # -------------------------------------------------------------------------
    # Semantic API (preferred)
    # -------------------------------------------------------------------------

    def configure(self, key: str, value: Any) -> None:
        """Record a configuration/stimulus value (→ in_* column).

        Use for commanded values, setpoints, and settings.

        Args:
            key: Parameter name (e.g., "psu.voltage", "temperature").
            value: The commanded value.
        """
        self._params[key] = value

    def observe(self, key: str, value: Any) -> None:
        """Record an observation/measurement context (→ out_* column).

        Use for measured environmental data, raw readings, and context.
        Numeric arrays are written to ChannelStore directly and a URI is stored.

        Args:
            key: Observation name (e.g., "temp_probe.temperature", "scope.waveform").
            value: The observed value. Large arrays go to ChannelStore, blobs to file store.
        """
        if self._channel_store is not None and value is not None:
            vtype = classify_value(value)
            if vtype in ("numeric_array", "channel"):
                uri = self._channel_store.write(key, value, source="observe")
                self._observations[key] = uri
                return

        self._observations[key] = value

    def changed(self, key: str) -> bool:
        """Check if an input parameter changed from the previous vector.

        Args:
            key: Parameter name to check.

        Returns:
            True if the value changed or this is the first vector, False otherwise.
        """
        if self._prev is None:
            return True  # First vector - everything is "changed"

        current_value = self.get_param(key)
        prev_value = self._prev.get_param(key)
        return current_value != prev_value

    def last(self, key: str, default: Any = None) -> Any:
        """Return the value of ``key`` recorded on the previous vector.

        Reads from the immediately-preceding ``Context`` (same
        ``(parent_node, method)`` in pytest-native; same parametrize case
        stream in the legacy harness). Looks up the key as a param first,
        then as an observation — matches how callers want to read back
        "what did I set / measure last time?" without caring about which
        store holds it.

        Returns ``default`` when no previous context exists (first vector)
        or the key was never recorded.
        """
        if self._prev is None:
            return default
        if key in self._prev._params:
            return self._prev._params[key]
        if key in self._prev._observations:
            return self._prev._observations[key]
        return default

    # -------------------------------------------------------------------------
    # Bulk operations
    # -------------------------------------------------------------------------

    def configure_all(self, values: dict[str, Any]) -> None:
        """Record multiple configuration values at once.

        Args:
            values: Dict of key-value pairs for in_* columns.
        """
        for key, value in values.items():
            self.configure(key, value)

    def observe_all(self, values: dict[str, Any]) -> None:
        """Record multiple observations at once.

        Args:
            values: Dict of key-value pairs for out_* columns.
        """
        for key, value in values.items():
            self.observe(key, value)

    def set_params(self, values: dict[str, Any]) -> None:
        """Set multiple param values at once (typically from vector params).

        Args:
            values: Dict of parameter values.
        """
        self._params.update(values)

    def set_observations(self, values: dict[str, Any]) -> None:
        """Set multiple observation values at once.

        Args:
            values: Dict of observation values.
        """
        self._observations.update(values)

    # -------------------------------------------------------------------------
    # Read access (with parent chain lookup)
    # -------------------------------------------------------------------------

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a parameter value, checking parent chain.

        Args:
            key: Parameter name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._params:
            return self._params[key]
        if self._parent is not None:
            return self._parent.get_param(key, default)
        return default

    def get_observation(self, key: str, default: Any = None) -> Any:
        """Get an observation value, checking parent chain.

        Args:
            key: Observation name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._observations:
            return self._observations[key]
        if self._parent is not None:
            return self._parent.get_observation(key, default)
        return default

    @property
    def params(self) -> dict[str, Any]:
        """All parameter values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.params)
        result.update(self._params)
        return result

    @property
    def observations(self) -> dict[str, Any]:
        """All observation values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.observations)
        result.update(self._observations)
        return result

    @property
    def characteristics(self) -> tuple[str, ...]:
        """Characteristic IDs in scope for the active test, in declaration order.

        Sourced from the ``litmus_characteristics`` marker (or the union of
        per-limit ``characteristic:`` values when the marker is absent).
        Read-only view; mirrors how :attr:`params` exposes the active
        vector params.
        """
        return tuple(get_active_test_characteristics())

    @property
    def limits(self) -> LimitsView:
        """Dict-like view over the merged ``litmus_limits`` for this test.

        Use ``ctx.limits[label]`` for the raw config (no resolution),
        ``ctx.limits.for_characteristic(char_id)`` for the subset bound
        to one char. Iteration follows insertion order. Resolution to
        a concrete :class:`Limit` still goes through
        :meth:`Context.get_limit` or the ``verify``/``logger`` paths.
        """
        return LimitsView(get_active_limits())

    # -------------------------------------------------------------------------
    # Session-level traceability (read-only ambient roll-up)
    # -------------------------------------------------------------------------

    @property
    def run(self) -> TestRun | None:
        """Active :class:`TestRun` record, or ``None`` outside a run.

        Carries run identity (id, started_at) plus DUT, station, fixture,
        product, profile, operator, and git fields. ``ctx.run.dut.serial``
        is the canonical path to DUT identity — there is intentionally no
        ``ctx.dut`` attribute (the bare ``dut`` fixture is the live DUT
        driver, a different concept).
        """
        logger = get_current_logger()
        return logger.test_run if logger is not None else None

    @property
    def station(self) -> StationConfig | None:
        """Active :class:`StationConfig`, or ``None`` for bringup tier.

        Sourced from the ``station_config`` session fixture, which seeds
        the ContextVar at session start. Note the asymmetry with the
        bare ``instruments`` fixture: ``instruments`` is left as a
        fixture-only entry point because ``ctx.instruments`` would
        collide with that fixture's name — take it as a test argument
        instead.
        """
        return get_active_station_config()

    @property
    def product(self) -> ProductContext | None:
        """Active :class:`ProductContext`, or ``None`` when no product is loaded.

        Mirrors the ``product_context`` session fixture but lets tests
        reach for it via ``ctx.product`` without taking the fixture as
        an argument. Useful inside helpers / verify wrappers that
        already have a ``Context`` reference.
        """
        return get_active_product_context()

    # -------------------------------------------------------------------------
    # Limit access
    # -------------------------------------------------------------------------

    def get_limit(self, name: str) -> Limit | None:
        """Get resolved limit for a measurement.

        Resolves limit using the same logic as harness.measure():
        1. Check harness._limits for direct/config limits
        2. Try MeasurementLimitConfig.to_limit() for direct values
        3. Try spec reference via ProductContext
        4. Try callable limit evaluation
        5. Fall back to ProductContext characteristic lookup

        Args:
            name: Measurement name to get limit for.

        Returns:
            Resolved Limit object, or None if no limit defined.

        Example:
            def test_adaptive(context, dmm, verify):
                limit = context.get_limit("output_voltage")
                if limit:
                    # Take more samples if nominal is tight
                    samples = 10 if limit.tolerance < 0.05 else 5
        """
        if self._harness is None:
            return None
        return self._harness._resolve_limit(name)

    def measure(
        self,
        name: str,
        value: float | None,
        units: str | None = None,
        limit: Limit | None = None,
        dut_pin: str | None = None,
        instrument_channel: str | None = None,
        fixture_connection: str | None = None,
    ) -> Measurement:
        """Record an explicit measurement by name.

        Use when the measurement name differs from the step name,
        or when producing multiple measurements from one test.

        Args:
            name: Measurement name (e.g., "output_voltage").
            value: Measured value.
            units: Units (optional, uses limit.units if available).
            limit: Explicit limit (optional, overrides config lookup).
            dut_pin: DUT pin being measured (optional).
            instrument_channel: Instrument channel used (optional).
            fixture_connection: Named fixture connection used (optional).

        Returns:
            Measurement object with outcome set.

        Example:
            def test_power_supply(context, dmm, psu):
                context.measure("output_voltage", dmm.measure_dc_voltage())
                context.measure("quiescent_current", psu.measure_current())
        """
        if self._harness is None:
            raise RuntimeError("No harness attached to context")
        return self._harness.measure(
            name,
            value,
            units=units,
            limit=limit,
            dut_pin=dut_pin,
            instrument_channel=instrument_channel,
            fixture_connection=fixture_connection,
        )


class TestHarness:
    """Harness for executing tests across expanded vectors.

    The harness manages:
    - Vector expansion from config
    - Iteration over vectors with .changed() tracking
    - Retry logic at the vector level
    - Measurement logging with limit resolution
    - Operator prompts
    - Mock configuration per vector (when using mocks)

    Usage (explicit loop):
        harness = TestHarness(config, logger=logger)
        for vector in harness.vectors:
            if vector.changed("temperature"):
                harness.prompt(f"Set chamber to {vector['temperature']}C")
            with harness.run_vector(vector):
                harness.measure("voltage", dmm.measure_dc_voltage())

    Usage (pytest-native):
        def test_sweep(context, psu, dmm, verify):
            psu.set_voltage(context.get_param("voltage"))
            verify("voltage", float(dmm.measure_dc_voltage()))
    """

    __test__ = False  # Prevent pytest collection (name starts with "Test")

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        logger: TestRunLogger | None = None,
        step_name: str = "test",
        retry: RetryConfig | None = None,
        limits: dict[str, MeasurementLimitConfig | Limit] | None = None,
        product_context: ProductContext | None = None,
        instruments: dict[str, Any] | None = None,
        mock_instruments: bool = False,
        channel_store: Any | None = None,
    ):
        """Initialize harness.

        Args:
            config: Test configuration dict with 'vectors', 'retry', 'limits' keys.
            logger: TestRunLogger for accumulating results.
            step_name: Name for the test step.
            retry: Retry configuration (overrides config if provided).
            limits: Limit configurations by measurement name (overrides config).
            product_context: ProductContext for spec-driven limit derivation and
                         channel traceability.
            instruments: Dictionary of instrument instances for mock configuration.
            mock_instruments: Whether using mock instruments.
            channel_store: Optional ChannelStore for direct writes of numeric data.
        """
        self._config = config or {}
        self._logger = logger
        self._step_name = step_name
        self._product_context = product_context
        self._instruments = instruments or {}
        self._mock_instruments = mock_instruments
        self._channel_store = channel_store
        self._test_level_mock = self._config.get("mocks", {})

        # Parse retry config
        if retry is not None:
            self._retry = retry
        elif self._config.get("retry"):
            retry_data = self._config["retry"]
            if isinstance(retry_data, RetryConfig):
                self._retry = retry_data
            else:
                self._retry = RetryConfig.model_validate(retry_data)
        else:
            self._retry = RetryConfig()

        # Parse limits
        self._limits: dict[str, MeasurementLimitConfig | Limit] = {}
        if limits is not None:
            self._limits = limits
        elif "limits" in self._config:
            for name, limit_config in self._config["limits"].items():
                if isinstance(limit_config, (Limit, MeasurementLimitConfig)):
                    self._limits[name] = limit_config
                else:
                    self._limits[name] = MeasurementLimitConfig.model_validate(limit_config)

        # Expand vectors from config
        vectors_config = self._config.get("vectors", {})
        if isinstance(vectors_config, list):
            # Explicit list of vectors
            self._vectors = expand_vectors(vectors_config)
        elif vectors_config:
            self._vectors = expand_vectors(vectors_config)
        else:
            # No vectors config = single empty vector
            self._vectors = [Vector(_index=0)]

        # Current execution state
        self._current_vector: Vector | None = None
        self._current_step_index: int = -1
        # 0-based retry counter. Set to 0 for the original execution and
        # incremented per retry inside the harness retry loop. Stamped onto
        # the per-vector TestVector + each MeasurementRecorded event.
        self._retry_index: int = 0

        # Hierarchical context: run → step → vector
        self._run_context: Context = Context(harness=self, channel_store=self._channel_store)
        self._step_context: Context | None = None
        self._vector_context: Context | None = None
        self._prev_vector_context: Context | None = None

    @property
    def vectors(self) -> list[Vector]:
        """Expanded vectors for iteration."""
        return self._vectors

    @property
    def current_vector(self) -> Vector | None:
        """Currently executing vector."""
        return self._current_vector

    @property
    def retry_config(self) -> RetryConfig:
        """Retry configuration."""
        return self._retry

    @property
    def context(self) -> Context:
        """Current active context (vector > step > run).

        Returns the most specific active context:
        - During vector execution: vector context (inherits from step and run)
        - During step but outside vector: step context (inherits from run)
        - Outside step: run context

        This is the primary API for setting and reading context values.
        """
        if self._vector_context is not None:
            return self._vector_context
        if self._step_context is not None:
            return self._step_context
        return self._run_context

    @property
    def run_context(self) -> Context:
        """Run-level context, persists across all steps and vectors."""
        return self._run_context

    def prompt(self, message: str, prompt_type: str = "confirm", **kwargs: Any) -> Any:
        """Show an operator prompt.

        Args:
            message: Prompt message (supports {param} formatting from current vector).
            prompt_type: Type of prompt ("confirm", "choice", "input").
            **kwargs: Additional prompt config (choices, timeout_seconds, etc.)

        Returns:
            Prompt result (True for confirm, selected choice, or input value).
        """
        if self._current_vector:
            message = message.format(**self._current_vector.params())

        config = PromptConfig(
            message=message,
            prompt_type=prompt_type,  # type: ignore
            choices=kwargs.get("choices"),
            timeout_seconds=kwargs.get("timeout_seconds"),
        )
        return ask(config)

    def _resolve_limit(self, name: str) -> Limit | None:
        """Resolve limit for a measurement name.

        Resolution order:
        1. Per-vector _limits (if current vector has _limits.{name})
        2. Direct Limit object in self._limits
        3. MeasurementLimitConfig with direct values
        4. MeasurementLimitConfig with spec ref (uses ProductContext)
        5. ProductContext characteristic lookup (name matches char_id)

        Args:
            name: Measurement name.

        Returns:
            Resolved Limit or None if no limit configured.
        """
        # Check per-vector _limits first
        if self._current_vector:
            vector_limits = self._current_vector.get("_limits", {})
            if name in vector_limits:
                vl = vector_limits[name]
                if isinstance(vl, Limit):
                    return vl
                if isinstance(vl, dict):
                    # Try as MeasurementLimitConfig first, then direct Limit
                    config = MeasurementLimitConfig.model_validate(vl)
                    return self._resolve_limit_config(config)
                if isinstance(vl, MeasurementLimitConfig):
                    return self._resolve_limit_config(vl)

        # Check test-level explicit limits
        if name in self._limits:
            limit_config = self._limits[name]

            # Direct Limit object
            if isinstance(limit_config, Limit):
                return limit_config

            # MeasurementLimitConfig - resolve based on type
            if isinstance(limit_config, MeasurementLimitConfig):
                result = self._resolve_limit_config(limit_config)
                if result is not None:
                    return result

        # Try ProductContext direct lookup (measurement name = characteristic ID)
        if self._product_context:
            try:
                conditions = {}
                if self._current_vector:
                    conditions = self._current_vector.params()

                return self._product_context.get_limit(name, **conditions)
            except (KeyError, ValueError):
                pass  # No matching characteristic

        return None

    def _resolve_limit_config(self, config: MeasurementLimitConfig) -> Limit | None:
        """Resolve a MeasurementLimitConfig to a Limit.

        Args:
            config: The limit configuration to resolve.

        Returns:
            Resolved Limit or None.
        """
        # Direct limit values
        direct = config.to_limit()
        if direct is not None:
            # Apply comparator from config if set
            if config.comparator and direct.comparator != config.comparator:
                direct = direct.model_copy(update={"comparator": config.comparator})
            return direct

        # Characteristic-only resolution (no tolerance — fetch the
        # characteristic's spec band straight off the active context).
        if config.characteristic and self._product_context:
            try:
                conditions = {}
                if self._current_vector:
                    conditions = self._current_vector.params()

                guardband = config.guardband_pct or 0.0
                return self._product_context.get_limit(
                    config.characteristic,
                    guardband_pct=guardband,
                    comparator=config.comparator,
                    limit_low=config.low,
                    limit_high=config.high,
                    **conditions,
                )
            except (KeyError, ValueError):
                pass  # Fall through

        # Callable resolution
        if config.callable:
            return self._resolve_callable_limit(config.callable)

        return None

    def _resolve_callable_limit(self, callable_str: str) -> Limit:
        """Resolve a callable limit from a dotted module path or inline Python.

        Args:
            callable_str: Either a dotted module path
                (e.g., "myproject.limits.output_voltage") or inline
                Python code (e.g., "Limit(high=ctx.get_param('vin') * 0.01,
                units='V')")

        Returns:
            Resolved Limit object.
        """
        # Detect inline Python vs module path
        # Inline Python contains: newlines, Limit(, return, if, =, operators
        is_inline = any(
            marker in callable_str
            for marker in ("\n", "Limit(", "return ", "if ", " = ", " + ", " - ", " * ", " / ")
        )

        if not is_inline and "." in callable_str:
            # Module path → import and call
            return self._call_module_function(callable_str)
        else:
            # Inline Python → eval with context
            return self._eval_inline_limit(callable_str)

    def _call_module_function(self, dotted_path: str) -> Limit:
        """Import and call a function by its dotted module path.

        Args:
            dotted_path: e.g., "myproject.limits.output_voltage"

        Returns:
            Limit returned by the function.
        """
        # Split into module and function
        module_path, func_name = dotted_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        # Call with context
        return func(self.context)

    def _eval_inline_limit(self, code: str) -> Limit:
        """Evaluate inline Python code to produce a Limit.

        .. warning::
            Executes arbitrary Python via ``eval``/``exec``.  Only use with
            trusted config files written by the test engineer — never with
            user-supplied or network-sourced strings.

        Args:
            code: Python expression or statements that return a Limit.

        Returns:
            Limit object from evaluation.
        """
        # Build namespace with useful values
        namespace = {
            "Limit": Limit,
            "ctx": self.context,
            **self.context.params,
        }

        # Handle multi-line code (need exec + return capture)
        if "\n" in code or code.strip().startswith("if "):
            # Wrap in function to capture return
            wrapped = "def __limit_fn__():\n"
            for line in code.strip().split("\n"):
                wrapped += f"    {line}\n"
            wrapped += "__result__ = __limit_fn__()"
            exec(wrapped, namespace)
            return namespace["__result__"]
        else:
            # Simple expression
            return eval(code, namespace)

    def measure(
        self,
        name: str,
        value: float | None,
        units: str | None = None,
        limit: Limit | None = None,
        dut_pin: str | None = None,
        instrument_channel: str | None = None,
        fixture_connection: str | None = None,
    ) -> Measurement:
        """Record a measurement for the current vector.

        Args:
            name: Measurement name.
            value: Measured value.
            units: Units (optional, uses limit.units if available).
            limit: Explicit limit (optional, overrides config lookup).
            dut_pin: DUT pin being measured (optional, auto-resolved from spec).
            instrument_channel: Instrument channel used (optional).
            fixture_connection: Named fixture connection used (optional).

        Returns:
            Measurement object with outcome set.
        """
        # Ensure value is float
        if value is not None and not isinstance(value, float):
            value = float(value)

        # Resolve limit
        resolved_limit = limit or self._resolve_limit(name)

        # Resolve channel traceability from ProductContext if not provided
        resolved_dut_pin = dut_pin
        resolved_instrument_channel = instrument_channel
        resolved_fixture_connection = fixture_connection

        if self._product_context and not all([dut_pin, instrument_channel, fixture_connection]):
            pin_info = self._product_context.get_pin_info(name)
            if pin_info:
                resolved_dut_pin = resolved_dut_pin or pin_info.get("dut_pin")
                resolved_instrument_channel = resolved_instrument_channel or pin_info.get(
                    "instrument_channel"
                )
                resolved_fixture_connection = resolved_fixture_connection or pin_info.get(
                    "fixture_connection"
                )

        # Create measurement
        measurement = Measurement(
            name=name,
            value=value,
            units=units or (resolved_limit.units if resolved_limit else None),
            limit_low=resolved_limit.low if resolved_limit else None,
            limit_high=resolved_limit.high if resolved_limit else None,
            limit_nominal=resolved_limit.nominal if resolved_limit else None,
            limit_comparator=resolved_limit.comparator if resolved_limit else None,
            characteristic_id=resolved_limit.characteristic_id if resolved_limit else None,
            spec_ref=resolved_limit.spec_ref if resolved_limit else None,
            dut_pin=resolved_dut_pin,
            instrument_channel=resolved_instrument_channel,
            fixture_connection=resolved_fixture_connection,
        )

        # Check limits
        measurement.check_limit()

        # Add to current vector (resolved from contextvar)
        current_tv = get_current_vector()
        if current_tv is not None:
            current_tv.measurements.append(measurement)

        # Stream to event log via logger (logger handles outcome updates).
        # The no-logger branch is for unit-testing the harness lifecycle
        # without an event-log fixture — vector outcome cascades but
        # step/run cascade is skipped (no logger context to read them
        # from). Production harness usage always has a logger.
        if self._logger is not None:
            self._logger.log_measurement(measurement)
        elif current_tv is not None and measurement.outcome is not None:
            current_tv.outcome = escalate_outcome(current_tv.outcome, measurement.outcome)

        return measurement

    def _record_result(
        self, result: float | dict[str, float] | tuple[str, float] | list[float] | None
    ) -> None:
        """Record a result from test function (return or yield).

        Handles:
        - dict: Multiple named measurements
        - tuple (name, value): Single named measurement
        - list/tuple of values: Use limit keys in order
        - single value: Measurement name inferred from spec ref, limit key, or step name
        - None: No measurement

        Name resolution order for single values:
        1. spec ref from limit config (if MeasurementLimitConfig with ref:)
        2. limit key (if exactly one limit)
        3. step name (fallback)
        """
        if result is None:
            return
        elif isinstance(result, dict):
            for name, value in result.items():
                self.measure(name, value)
        elif isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
            # Named tuple: (name, value)
            name, value = result
            self.measure(name, value)
        elif isinstance(result, (list, tuple)):
            # Multiple values without names: use limit keys in order
            limit_keys = list(self._limits.keys())
            for i, val in enumerate(result):
                meas_value = float(val) if val is not None else None
                if i < len(limit_keys):
                    self.measure(limit_keys[i], meas_value)
                else:
                    self.measure(f"{self._step_name}_{i}", meas_value)
        else:
            # Single value: infer name from spec ref → limit key → step name
            name = self._infer_measurement_name()
            self.measure(name, result)

    def record(self, key: str, value: Any) -> None:
        """Emit a key/value record event via the logger.

        Args:
            key: Record key (e.g., "firmware_version").
            value: Record value (must be JSON-serializable).
        """
        if self._logger:
            self._logger.record(key, value)

    def _infer_measurement_name(self) -> str:
        """Infer measurement name from limits config.

        Resolution order:
        1. spec ref from limit config (if MeasurementLimitConfig with ref:)
        2. limit key (if exactly one limit)
        3. step name (fallback)
        """
        if len(self._limits) == 1:
            limit_key = next(iter(self._limits.keys()))
            limit_config = self._limits[limit_key]
            # Prefer the characteristic ID when the config is a typed
            # MeasurementLimitConfig — that's the structured FK the
            # measurement should stamp.
            if isinstance(limit_config, MeasurementLimitConfig) and limit_config.characteristic:
                return limit_config.characteristic
            # Otherwise use the limit key itself
            return limit_key
        return self._step_name

    def _reset_mock_state(self) -> None:
        """Reset mock state flags on all instruments.

        This ensures that mocks behave normally when no explicit
        mock values are configured for a vector.
        """
        for inst in self._instruments.values():
            if hasattr(inst, "reset_mock_state"):
                inst.reset_mock_state()

    def _configure_mocks(self, mock_config: dict[str, Any]) -> None:
        """Configure mock instruments with values from config.

        Args:
            mock_config: Dict mapping "instrument.method" to values.
                        Example: {"dmm.measure_voltage": 3.3, "psu.measure_current": 0.5}

        If a value is callable, it will be wrapped to receive the current
        vector context as a keyword argument:

            def dynamic_voltage(cmd, *, context=None):
                load = context.get_param("load_current", 0) if context else 0
                return str(3.3 - load * 0.1)

            _mocks:
              inst.query: !callable dynamic_voltage
        """
        for key, value in mock_config.items():
            if "." not in key:
                continue
            inst_name, measurement = key.split(".", 1)
            if inst_name in self._instruments:
                inst = self._instruments[inst_name]
                if hasattr(inst, "set_mock_value"):
                    # Wrap callables to inject context
                    if callable(value):
                        value = self._wrap_mock_callable(value)
                    inst.set_mock_value(measurement, value)

    def _wrap_mock_callable(self, fn: Callable) -> Callable:
        """Wrap a mock callable to inject the current context.

        Args:
            fn: Original callable that may accept context kwarg

        Returns:
            Wrapped callable that passes current vector context
        """
        harness = self  # Capture reference for closure

        def wrapper(*args, **kwargs):
            # Inject context if not already provided
            if "context" not in kwargs:
                kwargs["context"] = harness._vector_context
            return fn(*args, **kwargs)

        return wrapper

    def _get_mock_config_for_vector(self, vector: Vector) -> dict[str, Any]:
        """Get mock configuration for a vector.

        Resolution order:
        1. Vector-level _mocks (per-vector config)
        2. Test-level mocks (constant for all vectors)
        3. Limit nominal values (fallback)
        """
        vector_mock = vector.get("_mocks", {})
        if vector_mock:
            return vector_mock

        # Fall back to test-level mocks
        if self._test_level_mock:
            return self._test_level_mock

        # Fall back to limit nominal values
        mock_from_limits: dict[str, Any] = {}
        for name, limit_config in self._limits.items():
            if isinstance(limit_config, Limit) and limit_config.nominal is not None:
                # Infer instrument.measurement from limit name
                # Convention: limit name matches measurement, dmm is default for voltage
                if "voltage" in name.lower():
                    mock_from_limits["dmm.measure_voltage"] = float(limit_config.nominal)
                elif "current" in name.lower():
                    mock_from_limits["psu.measure_current"] = float(limit_config.nominal)

        return mock_from_limits

    @contextmanager
    def run_vector(self, vector: Vector) -> Iterator[TestVector]:
        """Context manager for executing a single vector with retry support.

        Handles:
        - Creating TestVector record
        - Setting up vector context as child of step/run context
        - Configuring mocks for this vector (when mock=True)
        - Retry logic on failure
        - Finalizing vector timing

        Args:
            vector: Vector to execute.

        Yields:
            TestVector object for the execution.

        Example:
            for vector in harness.vectors:
                with harness.run_vector(vector) as tv:
                    harness.context.observe("temp_probe.temp", 24.8)
                    harness.measure("voltage", dmm.measure())
        """
        self._current_vector = vector
        self._retry_index = 0

        # Configure mocks for this vector if using mocks
        if self._mock_instruments and self._instruments:
            # Reset mock state from previous vector
            self._reset_mock_state()
            # Apply mock config for this vector
            mock_config = self._get_mock_config_for_vector(vector)
            if mock_config:
                self._configure_mocks(mock_config)

        # Create vector context as child of step (or run if no step)
        parent_context = self._step_context or self._run_context
        self._vector_context = Context(
            parent=parent_context,
            prev=self._prev_vector_context,
            harness=self,
            channel_store=self._channel_store,
        )

        # Seed the per-vector context with this vector's params; the
        # ``.params`` property below then merges them with parent-chain
        # values, which is why set() and read() look redundant but aren't.
        self._vector_context.set_params(vector.params())

        # Create TestVector record
        test_vector = TestVector(
            index=vector.get("_index", 0),
            params=self._vector_context.params,  # merged with parent chain
            retry=self._retry_index,
            max_retries=self._retry.max_retries,
            started_at=_utcnow(),
        )
        # Add to current step if logging
        current_step = get_current_step()
        if current_step is not None:
            current_step.vectors.append(test_vector)

        # Set contextvar for concurrency-safe resolution
        vector_token = push_current_vector(test_vector)
        try:
            yield test_vector
        except AssertionError as e:
            test_vector.outcome = Outcome.FAILED
            msg = str(e) or "assertion failed"
            test_vector.error_message = msg
            m = Measurement(name="assert", value=None, outcome=Outcome.FAILED)
            test_vector.measurements.append(m)
            if self._logger is not None:
                self._logger.log_measurement(m)
            raise
        except Exception as e:
            test_vector.outcome = Outcome.ERRORED
            test_vector.error_message = str(e)
            raise
        finally:
            # Snapshot context into TestVector before clearing
            test_vector.params = self._vector_context.params
            test_vector.observations = self._vector_context.observations
            test_vector.ended_at = _utcnow()
            reset_current_vector(vector_token)
            # Save current context for next vector's change detection
            self._prev_vector_context = self._vector_context
            self._vector_context = None
            self._current_vector = None

    def run_with_retry(
        self,
        vector: Vector,
        test_fn: Callable[[Vector], Any],
    ) -> TestVector:
        """Run a test function for a vector with retry support.

        Args:
            vector: Vector to test.
            test_fn: Test function that takes vector and returns value or yields
                    (name, value) tuples.

        Returns:
            Final TestVector after all attempts.
        """
        last_vector: TestVector | None = None

        # 0-based: retry=0 is the original attempt; retry=N is the Nth retry.
        # Loop runs ``max_retries + 1`` times total (1 original + max_retries retries).
        for retry in range(self._retry.max_retries + 1):
            self._retry_index = retry

            try:
                with self.run_vector(vector) as test_vector:
                    test_vector.retry = retry
                    last_vector = test_vector

                    result = test_fn(vector)

                    # Handle generator (streaming measurements via yield)
                    if hasattr(result, "__iter__") and hasattr(result, "__next__"):
                        for item in result:
                            self._record_result(item)
                    else:
                        self._record_result(result)
            except Exception:
                pass  # run_vector already recorded outcome + error

            assert last_vector is not None
            # Check if passed
            if last_vector.outcome == Outcome.PASSED:
                break

            # Retry delay — only sleep if there's another retry left
            if retry < self._retry.max_retries and self._retry.delay > 0:
                time.sleep(self._retry.delay)

        assert last_vector is not None
        return last_vector

    @contextmanager
    def step(self, name: str | None = None, description: str | None = None) -> Iterator[TestStep]:
        """Context manager for a test step.

        Creates a TestStep and adds it to the logger if available.
        Also creates a step-level context that inherits from the run context.

        Args:
            name: Step name (defaults to harness step_name).
            description: Step description.

        Yields:
            TestStep object.
        """
        identity = get_current_code_identity()

        step = TestStep(
            name=name or self._step_name,
            description=description,
            started_at=_utcnow(),
            node_id=identity.get("node_id"),
            file=identity.get("file"),
            module=identity.get("module"),
            class_name=identity.get("class_name"),
            function=identity.get("function"),
            markers=identity.get("markers"),
        )
        # Create step context as child of run context
        self._step_context = self._run_context.child()

        # Register with logger and emit event via public API
        if self._logger is not None:
            self._current_step_index = self._logger.register_step(step)
            step.instrument_arrays = self._logger.step_instrument_arrays
            self._logger.emit_step_started(step, self._current_step_index)

        # Set contextvar for concurrency-safe resolution
        step_token = push_current_step(step)
        try:
            yield step
        finally:
            step.ended_at = _utcnow()

            # Compute step outcome from vectors
            for tv in step.vectors:
                step.outcome = escalate_outcome(step.outcome, tv.outcome)

            # Emit StepEnded event via public API
            if self._logger is not None:
                self._logger.emit_step_ended(step, self._current_step_index)

            reset_current_step(step_token)
            self._step_context = None

    def run_all(
        self,
        test_fn: Callable[[Vector], Any],
        step_name: str | None = None,
    ) -> TestStep:
        """Run test function across all vectors.

        Convenience method that creates a step, iterates vectors, handles
        retries, and returns the completed step.

        Args:
            test_fn: Test function that takes vector and returns/yields measurements.
            step_name: Name for the test step.

        Returns:
            Completed TestStep with all vectors.
        """
        with self.step(name=step_name) as test_step:
            for vector in self._vectors:
                self.run_with_retry(vector, test_fn)

        return test_step
