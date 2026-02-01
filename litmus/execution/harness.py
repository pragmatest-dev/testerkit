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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from litmus.config.models import Limit, MeasurementLimitConfig, PromptConfig, RetryConfig
from litmus.data.models import Measurement, Outcome, TestStep, TestVector
from litmus.execution.vectors import Vector, expand_vectors

if TYPE_CHECKING:
    from litmus.execution.logger import TestRunLogger
    from litmus.products.context import SpecContext

# Forward reference for type hints
_TestHarness = "TestHarness"


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


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
        harness: _TestHarness | None = None,
    ):
        """Initialize context with optional parent for inheritance.

        Args:
            parent: Parent context to inherit values from. If None, this is a root context.
            prev: Previous sibling context (for change detection across vectors).
            harness: TestHarness reference for accessing limits and other harness features.
        """
        self._parent = parent
        self._prev = prev
        self._harness = harness
        self._inputs: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}

    def child(self) -> Context:
        """Create a child context that inherits from this one.

        Returns:
            New Context with this context as parent.
        """
        return Context(parent=self, harness=self._harness)

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
        self._inputs[key] = value

    def observe(self, key: str, value: Any) -> None:
        """Record an observation/measurement context (→ out_* column).

        Use for measured environmental data, raw readings, and context.

        Args:
            key: Observation name (e.g., "temp_probe.temperature", "scope.waveform").
            value: The observed value. Large arrays may be stored in _raw directory.
        """
        self._outputs[key] = value

    def changed(self, key: str) -> bool:
        """Check if an input parameter changed from the previous vector.

        Args:
            key: Parameter name to check.

        Returns:
            True if the value changed or this is the first vector, False otherwise.
        """
        if self._prev is None:
            return True  # First vector - everything is "changed"

        current_value = self.get_in(key)
        prev_value = self._prev.get_in(key)
        return current_value != prev_value

    # -------------------------------------------------------------------------
    # Explicit API (aliases)
    # -------------------------------------------------------------------------

    def set_in(self, key: str, value: Any) -> None:
        """Alias for configure() - explicitly set an in_* value."""
        self.configure(key, value)

    def set_out(self, key: str, value: Any) -> None:
        """Alias for observe() - explicitly set an out_* value."""
        self.observe(key, value)

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

    def set_inputs(self, values: dict[str, Any]) -> None:
        """Set multiple input values at once (typically from vector params).

        Args:
            values: Dict of input parameter values.
        """
        self._inputs.update(values)

    def set_outputs(self, values: dict[str, Any]) -> None:
        """Set multiple output observation values at once.

        Args:
            values: Dict of observation values.
        """
        self._outputs.update(values)

    # -------------------------------------------------------------------------
    # Read access (with parent chain lookup)
    # -------------------------------------------------------------------------

    def get_in(self, key: str, default: Any = None) -> Any:
        """Get an input configuration value, checking parent chain.

        Args:
            key: Parameter name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._inputs:
            return self._inputs[key]
        if self._parent is not None:
            return self._parent.get_in(key, default)
        return default

    def get_out(self, key: str, default: Any = None) -> Any:
        """Get an observation value, checking parent chain.

        Args:
            key: Observation name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._outputs:
            return self._outputs[key]
        if self._parent is not None:
            return self._parent.get_out(key, default)
        return default

    @property
    def inputs(self) -> dict[str, Any]:
        """All input configuration values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.inputs)
        result.update(self._inputs)
        return result

    @property
    def outputs(self) -> dict[str, Any]:
        """All observation values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.outputs)
        result.update(self._outputs)
        return result

    # -------------------------------------------------------------------------
    # Limit access
    # -------------------------------------------------------------------------

    def get_limit(self, name: str) -> Limit | None:
        """Get resolved limit for a measurement.

        Resolves limit using the same logic as harness.measure():
        1. Check harness._limits for direct/config limits
        2. Try MeasurementLimitConfig.to_limit() for direct values
        3. Try spec reference via SpecContext
        4. Try callable limit evaluation
        5. Fall back to SpecContext characteristic lookup

        Args:
            name: Measurement name to get limit for.

        Returns:
            Resolved Limit object, or None if no limit defined.

        Example:
            @litmus_test
            def test_adaptive(context, dmm):
                limit = context.get_limit("output_voltage")
                if limit:
                    # Take more samples if nominal is tight
                    samples = 10 if limit.tolerance < 0.05 else 5
        """
        if self._harness is None:
            return None
        return self._harness._resolve_limit(name)

    # -------------------------------------------------------------------------
    # RunContext compatibility (for metadata at run level)
    # -------------------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Set a custom metadata field (RunContext compatibility).

        For run-level context, this is the primary way to add custom fields
        that become columns in Parquet. For vector-level context, use
        configure() for inputs or observe() for outputs.

        Args:
            key: Field name.
            value: Field value (must be JSON-serializable for Parquet).
        """
        # Store as input for now - custom metadata flows through inputs
        self._inputs[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a custom metadata field (RunContext compatibility).

        Args:
            key: Field name.
            default: Value to return if key not found.

        Returns:
            The stored value or default.
        """
        return self.get_in(key, default)

    def update(self, **kwargs: Any) -> None:
        """Set multiple custom metadata fields at once (RunContext compatibility).

        Args:
            **kwargs: Key-value pairs to set.
        """
        for key, value in kwargs.items():
            self.set(key, value)

    @property
    def metadata(self) -> dict[str, Any]:
        """Access the underlying metadata dict (RunContext compatibility)."""
        return self.inputs


# Backwards compatibility alias
VectorContext = Context


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

    Usage (via @litmus_test decorator):
        @litmus_test
        def test_sweep(vector, psu, dmm):
            psu.set_voltage(vector["voltage"])
            return dmm.measure_dc_voltage()
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        logger: TestRunLogger | None = None,
        step_name: str = "test",
        retry: RetryConfig | None = None,
        limits: dict[str, MeasurementLimitConfig | Limit] | None = None,
        prompt_handler: Callable[[PromptConfig], Any] | None = None,
        spec_context: SpecContext | None = None,
        instruments: dict[str, Any] | None = None,
        mock_instruments: bool = False,
    ):
        """Initialize harness.

        Args:
            config: Test configuration dict with 'vectors', 'retry', 'limits' keys.
            logger: TestRunLogger for accumulating results.
            step_name: Name for the test step.
            retry: Retry configuration (overrides config if provided).
            limits: Limit configurations by measurement name (overrides config).
            prompt_handler: Callback for operator prompts. If None, prompts
                           are printed to stdout.
            spec_context: SpecContext for spec-driven limit derivation and
                         channel traceability.
            instruments: Dictionary of instrument instances for mock configuration.
            mock_instruments: Whether using mock instruments.
        """
        self._config = config or {}
        self._logger = logger
        self._step_name = step_name
        self._prompt_handler = prompt_handler or self._default_prompt_handler
        self._spec_context = spec_context
        self._instruments = instruments or {}
        self._mock_instruments = mock_instruments
        self._test_level_mock = self._config.get("_mock", {})

        # Parse retry config
        if retry is not None:
            self._retry = retry
        elif "retry" in self._config:
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
            self._vectors = expand_vectors({"expand": None})
            # Re-expand with the list
            from litmus.execution.vectors import expand_list

            self._vectors = expand_list(vectors_config)
        elif vectors_config:
            self._vectors = expand_vectors(vectors_config)
        else:
            # No vectors config = single empty vector
            self._vectors = [Vector(_index=0)]

        # Current execution state
        self._current_vector: Vector | None = None
        self._current_test_vector: TestVector | None = None
        self._current_step: TestStep | None = None
        self._attempt: int = 1

        # Hierarchical context: run → step → vector
        self._run_context: Context = Context(harness=self)
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
    def vector_context(self) -> Context:
        """Current vector context for logging in_*/out_* data.

        Deprecated: Use `context` property instead for the active context
        at any scope (vector > step > run).
        """
        return self.context

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

    def _default_prompt_handler(self, prompt: PromptConfig) -> Any:
        """Default prompt handler - prints to stdout and waits for input."""
        print(f"\n[Prompt] {prompt.message}")
        if prompt.prompt_type == "confirm":
            input("Press Enter to continue...")
            return True
        elif prompt.prompt_type == "choice" and prompt.choices:
            for i, choice in enumerate(prompt.choices, 1):
                print(f"  {i}. {choice}")
            while True:
                try:
                    selection = int(input("Select option: "))
                    if 1 <= selection <= len(prompt.choices):
                        return prompt.choices[selection - 1]
                except ValueError:
                    pass
                print("Invalid selection, try again.")
        elif prompt.prompt_type == "input":
            return input("Enter value: ")
        return None

    def prompt(self, message: str, prompt_type: str = "confirm", **kwargs: Any) -> Any:
        """Show an operator prompt.

        Args:
            message: Prompt message (supports {param} formatting from current vector).
            prompt_type: Type of prompt ("confirm", "choice", "input").
            **kwargs: Additional prompt config (choices, timeout_seconds, etc.)

        Returns:
            Prompt result (True for confirm, selected choice, or input value).
        """
        # Format message with current vector params
        if self._current_vector:
            message = message.format(**self._current_vector.params())

        config = PromptConfig(
            message=message,
            prompt_type=prompt_type,  # type: ignore
            choices=kwargs.get("choices"),
            timeout_seconds=kwargs.get("timeout_seconds"),
        )
        return self._prompt_handler(config)

    def _resolve_limit(self, name: str) -> Limit | None:
        """Resolve limit for a measurement name.

        Resolution order:
        1. Direct Limit object in self._limits
        2. MeasurementLimitConfig with direct values
        3. MeasurementLimitConfig with spec ref (uses SpecContext)
        4. SpecContext characteristic lookup (name matches char_id)

        Args:
            name: Measurement name.

        Returns:
            Resolved Limit or None if no limit configured.
        """
        # First check explicit limits
        if name in self._limits:
            limit_config = self._limits[name]

            # Direct Limit object
            if isinstance(limit_config, Limit):
                return limit_config

            # MeasurementLimitConfig - resolve based on type
            if isinstance(limit_config, MeasurementLimitConfig):
                # Direct limit values
                direct = limit_config.to_limit()
                if direct is not None:
                    return direct

                # Spec ref resolution
                if limit_config.ref and self._spec_context:
                    try:
                        # Get current vector params for conditions
                        conditions = {}
                        if self._current_vector:
                            conditions = self._current_vector.params()

                        guardband = limit_config.guardband_pct or 0.0
                        return self._spec_context.get_limit(
                            limit_config.ref,
                            guardband_pct=guardband,
                            **conditions,
                        )
                    except (KeyError, ValueError):
                        pass  # Fall through

                # Callable resolution
                if limit_config.callable:
                    return self._resolve_callable_limit(limit_config.callable)

        # Try SpecContext direct lookup (measurement name = characteristic ID)
        if self._spec_context:
            try:
                conditions = {}
                if self._current_vector:
                    conditions = self._current_vector.params()

                return self._spec_context.get_limit(name, **conditions)
            except (KeyError, ValueError):
                pass  # No matching characteristic

        return None

    def _resolve_callable_limit(self, callable_str: str) -> Limit:
        """Resolve a callable limit from a dotted module path or inline Python.

        Args:
            callable_str: Either a dotted module path (e.g., "myproject.limits.output_voltage")
                         or inline Python code (e.g., "Limit(high=ctx.get_in('vin') * 0.01, units='V')")

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

        Args:
            code: Python expression or statements that return a Limit.

        Returns:
            Limit object from evaluation.
        """
        # Build namespace with useful values
        namespace = {
            "Limit": Limit,
            "ctx": self.context,
            **self.context.inputs,
        }

        # Handle multi-line code (need exec + return capture)
        if "\n" in code or code.strip().startswith("if "):
            # Wrap in function to capture return
            wrapped = f"def __limit_fn__():\n"
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
        fixture_point: str | None = None,
    ) -> Measurement:
        """Record a measurement for the current vector.

        Args:
            name: Measurement name.
            value: Measured value.
            units: Units (optional, uses limit.units if available).
            limit: Explicit limit (optional, overrides config lookup).
            dut_pin: DUT pin being measured (optional, auto-resolved from spec).
            instrument_channel: Instrument channel used (optional).
            fixture_point: Fixture channel used (optional).

        Returns:
            Measurement object with outcome set.
        """
        # Ensure value is float
        if value is not None and not isinstance(value, float):
            value = float(value)

        # Resolve limit
        resolved_limit = limit or self._resolve_limit(name)

        # Resolve channel traceability from SpecContext if not provided
        resolved_dut_pin = dut_pin
        resolved_instrument_channel = instrument_channel
        resolved_fixture_point = fixture_point

        if self._spec_context and not all([dut_pin, instrument_channel, fixture_point]):
            pin_info = self._spec_context.get_pin_info(name)
            if pin_info:
                resolved_dut_pin = resolved_dut_pin or pin_info.get("dut_pin")
                resolved_instrument_channel = resolved_instrument_channel or pin_info.get(
                    "instrument_channel"
                )
                resolved_fixture_point = resolved_fixture_point or pin_info.get(
                    "fixture_point"
                )

        # Create measurement
        measurement = Measurement(
            name=name,
            value=value,
            units=units or (resolved_limit.units if resolved_limit else None),
            low_limit=resolved_limit.low if resolved_limit else None,
            high_limit=resolved_limit.high if resolved_limit else None,
            nominal=resolved_limit.nominal if resolved_limit else None,
            spec_id=resolved_limit.spec_id if resolved_limit else None,
            spec_ref=resolved_limit.spec_ref if resolved_limit else None,
            dut_pin=resolved_dut_pin,
            instrument_channel=resolved_instrument_channel,
            fixture_point=resolved_fixture_point,
        )

        # Check limits
        measurement.check_limit()

        # Add to current vector
        if self._current_test_vector is not None:
            self._current_test_vector.measurements.append(measurement)
            # Update vector outcome
            if measurement.outcome == Outcome.FAIL:
                self._current_test_vector.outcome = Outcome.FAIL
            elif measurement.outcome == Outcome.ERROR:
                if self._current_test_vector.outcome != Outcome.FAIL:
                    self._current_test_vector.outcome = Outcome.ERROR

        return measurement

    def _record_result(self, result: Any) -> None:
        """Record a result from test function (return or yield).

        Handles:
        - dict: Multiple named measurements
        - tuple (name, value): Single named measurement
        - single value: Measurement with step name
        - None: No measurement
        """
        if result is None:
            return
        elif isinstance(result, dict):
            for name, value in result.items():
                self.measure(name, value)
        elif isinstance(result, tuple) and len(result) == 2:
            name, value = result
            self.measure(name, value)
        else:
            self.measure(self._step_name, result)

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
        """
        for key, value in mock_config.items():
            if "." not in key:
                continue
            inst_name, measurement = key.split(".", 1)
            if inst_name in self._instruments:
                inst = self._instruments[inst_name]
                if hasattr(inst, "set_mock_value"):
                    inst.set_mock_value(measurement, value)

    def _get_mock_config_for_vector(self, vector: Vector) -> dict[str, Any]:
        """Get mock configuration for a vector.

        Resolution order:
        1. Vector-level _mock (per-vector config)
        2. Test-level _mock (constant for all vectors)
        3. Limit nominal values (fallback)
        """
        # Check for vector-level _mock
        vector_mock = vector.get("_mock", {})
        if vector_mock:
            return vector_mock

        # Fall back to test-level _mock
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
        - Configuring mocks for this vector (when simulate=True)
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
        self._attempt = 1

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
            parent=parent_context, prev=self._prev_vector_context, harness=self
        )

        # Pre-populate with vector params (they become in_* columns)
        self._vector_context.set_inputs(vector.params())

        # Create TestVector record
        test_vector = TestVector(
            index=vector.get("_index", 0),
            params=self._vector_context.inputs,  # Includes inherited values
            attempt=self._attempt,
            max_attempts=self._retry.max_attempts,
            started_at=_utcnow(),
        )
        self._current_test_vector = test_vector

        # Add to current step if logging
        if self._current_step is not None:
            self._current_step.vectors.append(test_vector)

        try:
            yield test_vector
        except Exception as e:
            test_vector.outcome = Outcome.ERROR
            test_vector.error_message = str(e)
            raise
        finally:
            # Snapshot context into TestVector before clearing
            test_vector.params = self._vector_context.inputs
            test_vector.observations = self._vector_context.outputs
            test_vector.ended_at = _utcnow()
            # Save current context for next vector's change detection
            self._prev_vector_context = self._vector_context
            self._vector_context = None
            self._current_test_vector = None
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

        for attempt in range(1, self._retry.max_attempts + 1):
            self._attempt = attempt

            with self.run_vector(vector) as test_vector:
                test_vector.attempt = attempt
                last_vector = test_vector

                try:
                    result = test_fn(vector)

                    # Handle generator (streaming measurements via yield)
                    if hasattr(result, "__iter__") and hasattr(result, "__next__"):
                        for item in result:
                            self._record_result(item)
                    else:
                        self._record_result(result)

                except Exception as e:
                    test_vector.outcome = Outcome.ERROR
                    test_vector.error_message = str(e)

            # Check if passed
            if last_vector.outcome == Outcome.PASS:
                break

            # Retry delay
            if attempt < self._retry.max_attempts and self._retry.delay_seconds > 0:
                time.sleep(self._retry.delay_seconds)

        return last_vector  # type: ignore

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
        step = TestStep(
            name=name or self._step_name,
            description=description,
            started_at=_utcnow(),
        )
        self._current_step = step

        # Create step context as child of run context
        self._step_context = self._run_context.child()

        # Add to logger
        if self._logger is not None:
            self._logger.test_run.steps.append(step)

        try:
            yield step
        finally:
            step.ended_at = _utcnow()

            # Compute step outcome from vectors
            for tv in step.vectors:
                if tv.outcome == Outcome.FAIL:
                    step.outcome = Outcome.FAIL
                    break
                elif tv.outcome == Outcome.ERROR and step.outcome != Outcome.FAIL:
                    step.outcome = Outcome.ERROR

            self._step_context = None
            self._current_step = None

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
