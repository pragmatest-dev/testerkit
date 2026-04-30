"""Data models for test results."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from litmus.data.backends._row_helpers import MeasurementRow


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


class StimulusRecord(BaseModel):
    """Record of a stimulus applied during test execution.

    Captures the full signal path when an instrument sets an input condition:
    - param: The parameter name (e.g., "vin", "load", "temp")
    - value: The commanded value
    - units: Units of the value
    - instrument: Station config name (e.g., "psu_main")
    - resource: VISA address or connection string
    - channel: Channel on instrument (e.g., "CH1")
    - dut_pin: DUT pin being driven
    - fixture_connection: Named fixture connection
    """

    param: str
    value: float | None = None
    units: str | None = None
    instrument: str | None = None  # Station config name (e.g., "psu_main")
    resource: str | None = None  # VISA address or connection string
    channel: str | None = None  # Channel on instrument (e.g., "CH1")
    dut_pin: str | None = None  # DUT pin being driven
    fixture_connection: str | None = None  # Named fixture connection


class Outcome(StrEnum):
    """Canonical terminal outcome of a measurement / step / run.

    All values are past participles — outcomes are retrospective by
    construction (the row reflects what happened). Each runner adapter
    translates its native signals into these. Pytest's own
    ``passed`` / ``failed`` / ``skipped`` / ``error`` mostly map by
    casing; bare-assert failures and ``pytest.skip()`` flow through
    ``pytest_runtest_makereport``.

    Producer story:

    * ``PASSED`` — measurement value met its limit. Producer:
      ``execution.verify._apply_outcome`` when ``value in limit``;
      pytest ``passed`` propagates when no measurement-level outcome
      contradicts.
    * ``FAILED`` — measurement violated its limit OR the test failed an
      assertion. Producer: ``_apply_outcome`` when ``value not in limit``;
      ``pytest_runtest_makereport`` escalates ``step.outcome`` to
      ``FAILED`` when pytest reports ``failed`` and no measurement
      already failed.
    * ``ERRORED`` — exception during execution (not an assertion).
      Producer: pytest ``error`` (setup/teardown failures); uncaught
      non-AssertionError during call.
    * ``SKIPPED`` — explicit skip by operator/marker/condition.
      Producer: pytest ``skipped`` (``pytest.skip()`` /
      ``@pytest.mark.skip`` / skipif); ``VectorBuilder.skip()`` on the
      catch-all client API.
    * ``DONE`` — recorded value, no judgment evaluated. Producer:
      ``_apply_outcome`` when no limit is configured; setup/action /
      characterization-mode measurements that explicitly aren't being
      judged. Test engineers reject ``PASSED`` for un-judged actions
      ("don't call my setup a pass") — this is the answer.
    * ``ABORTED`` — interrupted mid-execution by user or system.
      Producer: ``RunBuilder.abort()`` on the catch-all client;
      ``pytest_keyboard_interrupt`` stamps the in-flight step + the run
      as ``ABORTED``.
    * ``PLANNED`` — scheduled, never reached. Producer: the
      reconciliation in ``_append_not_started`` for collected pytest
      items whose ``node_id`` never appears in the executed set; the
      run ended before this step's turn.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"
    ABORTED = "aborted"
    DONE = "done"
    PLANNED = "planned"


_OUTCOME_SEVERITY: dict[Outcome, int] = {
    Outcome.ABORTED: 6,
    Outcome.ERRORED: 5,
    Outcome.FAILED: 4,
    Outcome.PASSED: 3,
    Outcome.DONE: 2,
    Outcome.SKIPPED: 1,
    Outcome.PLANNED: 0,
}


def escalate_outcome(current: Outcome, incoming: Outcome) -> Outcome:
    """Return the worse (higher-severity) of two outcomes.

    Severity, worst first:
    ``ABORTED > ERRORED > FAILED > PASSED > DONE > SKIPPED > PLANNED``.

    Use this everywhere outcome cascading is needed (vector → step → run)
    to keep severity logic in one place. Reading the ladder:

    * ``ABORTED`` preempts everything — a mid-flight kill is the loudest
      signal we have.
    * ``ERRORED`` beats ``FAILED`` — an unexpected blow-up is worse than
      a judged-bad value.
    * ``PASSED`` beats ``DONE`` — an actual verdict outranks a recorded-
      but-unjudged value.
    * ``SKIPPED`` beats ``PLANNED`` — declining to run is more committed
      than never reaching the step at all.
    """
    return current if _OUTCOME_SEVERITY[current] >= _OUTCOME_SEVERITY[incoming] else incoming


class Measurement(BaseModel):
    """A single measurement with optional limit checking."""

    name: str
    step_path: str = ""
    value: float | None
    units: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    outcome: Outcome | None = None
    characteristic_id: str | None = None  # Characteristic ID for structured traceability
    spec_ref: str | None = None  # Human-readable spec reference with conditions
    limit_comparator: str | None = None  # ATML comparator: EQ, NE, GE, LE, GELE, etc.
    timestamp: datetime = Field(default_factory=_utcnow)

    # Traceability (ATML: signal routing)
    dut_pin: str | None = None  # Which DUT pin was measured
    instrument_name: str | None = None  # Station config name (e.g., "dmm_main")
    instrument_resource: str | None = None  # VISA address or connection string
    instrument_channel: str | None = None  # Channel on instrument (e.g., "CH1")
    fixture_connection: str | None = None  # Fixture connection name (e.g., "VOUT")

    def check_limit(self) -> Outcome:
        """Evaluate value against limits using comparator, set outcome, return result.

        Comparator meanings (per ATML/IEEE 1671):
            EQ: value == nominal
            NE: value != nominal
            LT: value < limit_high
            LE: value <= limit_high
            GT: value > limit_low
            GE: value >= limit_low
            GELE: limit_low <= value <= limit_high (default)
            GELT: limit_low <= value < limit_high
            GTLE: limit_low < value <= limit_high
            GTLT: limit_low < value < limit_high
        """
        if self.value is None:
            self.outcome = Outcome.ERRORED
            return self.outcome

        # Default to GELE (inclusive range) if no comparator specified
        comp = self.limit_comparator or "GELE"

        if comp == "EQ":
            # Exact match to nominal
            if self.limit_nominal is not None and self.value == self.limit_nominal:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "NE":
            # Not equal to nominal
            if self.limit_nominal is not None and self.value != self.limit_nominal:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "LT":
            # Less than high limit (no limit = no constraint = pass)
            if self.limit_high is None or self.value < self.limit_high:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "LE":
            # Less than or equal to high limit (no limit = no constraint = pass)
            if self.limit_high is None or self.value <= self.limit_high:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "GT":
            # Greater than low limit (no limit = no constraint = pass)
            if self.limit_low is None or self.value > self.limit_low:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "GE":
            # Greater than or equal to low limit (no limit = no constraint = pass)
            if self.limit_low is None or self.value >= self.limit_low:
                self.outcome = Outcome.PASSED
            else:
                self.outcome = Outcome.FAILED
        elif comp == "GELE":
            # Inclusive range: low <= value <= high
            low_ok = self.limit_low is None or self.value >= self.limit_low
            high_ok = self.limit_high is None or self.value <= self.limit_high
            self.outcome = Outcome.PASSED if (low_ok and high_ok) else Outcome.FAILED
        elif comp == "GELT":
            # low <= value < high
            low_ok = self.limit_low is None or self.value >= self.limit_low
            high_ok = self.limit_high is None or self.value < self.limit_high
            self.outcome = Outcome.PASSED if (low_ok and high_ok) else Outcome.FAILED
        elif comp == "GTLE":
            # low < value <= high
            low_ok = self.limit_low is None or self.value > self.limit_low
            high_ok = self.limit_high is None or self.value <= self.limit_high
            self.outcome = Outcome.PASSED if (low_ok and high_ok) else Outcome.FAILED
        elif comp == "GTLT":
            # Exclusive range: low < value < high
            low_ok = self.limit_low is None or self.value > self.limit_low
            high_ok = self.limit_high is None or self.value < self.limit_high
            self.outcome = Outcome.PASSED if (low_ok and high_ok) else Outcome.FAILED
        else:
            # Unknown comparator, fall back to GELE behavior
            low_ok = self.limit_low is None or self.value >= self.limit_low
            high_ok = self.limit_high is None or self.value <= self.limit_high
            self.outcome = Outcome.PASSED if (low_ok and high_ok) else Outcome.FAILED

        return self.outcome


class TestVector(BaseModel):
    """A test vector execution with its input parameters and observations.

    Represents a single execution of a test with specific input values.
    Parameters are stored once here, not duplicated on each measurement.

    This is the primary unit of test execution: the framework expands
    vectors from config (product, zip, range, nested loops) and iterates
    over them, calling the test function for each.

    Hierarchy:
        TestRun
        └── TestStep (one per pytest test function)
            └── TestVector[] (one per param set, expanded from config)
                └── Measurement[] (values captured in that vector)

    Data categories:
        - params (in_*): Configuration - commanded values, setpoints, settings
        - observations (out_*): Measured context, environmental readings, raw data
        - measurements: The actual test results (always scalars)
    """

    __test__ = False  # Prevent pytest collection

    id: UUID = Field(default_factory=uuid4)
    test_step_id: UUID | None = None  # FK to parent TestStep
    index: int = 0  # 0-based index in the parameter expansion
    params: dict[str, Any] = Field(default_factory=dict)  # Input parameter values (→ in_*)
    observations: dict[str, Any] = Field(default_factory=dict)  # Observed context (→ out_*)
    stimulus: list[StimulusRecord] = Field(default_factory=list)  # Stimulus signal paths
    attempt: int = 1  # Current attempt number (for retries)
    max_attempts: int = 1  # Maximum attempts allowed
    outcome: Outcome = Outcome.PASSED
    measurements: list[Measurement] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    error_message: str | None = None


class TestStep(BaseModel):
    """A test step containing test vectors.

    A step corresponds to a pytest test function. It may contain multiple
    test vectors if the test is parametrized or uses vector expansion.

    Hierarchy:
        TestRun
        └── TestStep (one per pytest test function)
            └── TestVector[] (one per param set, expanded from config)
                └── Measurement[] (values captured in that vector)
    """

    __test__ = False  # Prevent pytest collection

    id: UUID = Field(default_factory=uuid4)
    name: str
    step_path: str = ""
    parent_path: str = ""
    description: str | None = None

    # Code identity (populated from pytest.Item when available)
    node_id: str | None = None
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None
    markers: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    outcome: Outcome = Outcome.PASSED
    vectors: list[TestVector] = Field(default_factory=list)
    error_message: str | None = None
    instrument_arrays: dict[str, list] | None = None

    @property
    def total_vectors(self) -> int:
        """Total number of test vectors."""
        return len(self.vectors) if self.vectors else 1

    @property
    def passed_vectors(self) -> int:
        """Number of passed test vectors."""
        return sum(1 for v in self.vectors if v.outcome == Outcome.PASSED)

    @property
    def failed_vectors(self) -> int:
        """Number of failed test vectors."""
        return sum(1 for v in self.vectors if v.outcome == Outcome.FAILED)


class CollectedItem(BaseModel):
    """A pytest item collected for execution (before any run)."""

    node_id: str
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None
    markers: str | None = None


class DUT(BaseModel):
    """Device under test identification."""

    serial: str
    part_number: str | None = None
    revision: str | None = None
    lot_number: str | None = None


class RunSummary(BaseModel):
    """Lightweight run header read from parquet index (no steps/measurements)."""

    test_run_id: str
    session_id: str | None = None
    slot_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    dut_serial: str | None = None
    dut_part_number: str | None = None
    product_id: str | None = None
    station_id: str | None = None
    station_type: str | None = None
    test_phase: str | None = None
    operator: str | None = None
    outcome: str | None = None
    total_measurements: int = 0
    project_name: str | None = None
    file_path: str | None = None  # internal: parquet file location for fast measurement lookup


class TestRun(BaseModel):
    """A complete test run with steps and measurements."""

    __test__ = False  # Prevent pytest collection

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)  # Cross-store join key; set by logger
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None

    # DUT identification
    dut: DUT

    # Product traceability
    product_id: str | None = None
    product_name: str | None = None
    product_revision: str | None = None

    # Station traceability — ``station_id`` is None for bringup tier
    # runs (no station YAML on disk; tests use conftest-defined
    # instrument fixtures). ``station_hostname`` always populates from
    # ``socket.gethostname()`` so the run is traceable to a machine
    # even without a station id.
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    station_hostname: str | None = None

    # Fixture traceability
    fixture_id: str | None = None

    # Phase / profile traceability
    test_phase: str | None = None
    profile: str | None = None  # active --test-profile name, if any
    # Raw CLI facet values used to select the profile; combined with git SHA
    # this is the minimum reproducibility payload for the run.
    profile_facets: dict[str, str] = Field(default_factory=dict)
    # Resolved required_inputs at session start (serial_number, operator, etc.).
    # Each project declares the keys; the values come from CLI flags, env
    # vars, or operator prompts at the start of the run.
    session_inputs: dict[str, str] = Field(default_factory=dict)

    # Operator
    operator_id: str | None = None  # from --operator
    operator_name: str | None = None  # human-readable name

    # Code traceability
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None
    project_name: str | None = None

    # Results
    outcome: Outcome = Outcome.PASSED
    steps: list[TestStep] = Field(default_factory=list)

    # Collected items (full list from pytest collection, before execution)
    collected_items: list[CollectedItem] = Field(default_factory=list)

    # Custom metadata (user-defined fields)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    # Environment snapshot (stored in Parquet file-level metadata)
    environment_json: str | None = None

    def iter_rows(self) -> Iterator[MeasurementRow]:
        """Yield denormalized MeasurementRow for each measurement.

        Joins run-level context (DUT, station, operator, etc.) onto each
        measurement, producing the same flat view used by streaming and Parquet.

        Intended for analysis and denormalization (CSV export, DataFrames).
        No ``ref_saver`` is used, so non-serializable observation values
        (Waveform, ndarray, bytes, etc.) fall back to ``repr()`` strings
        in the output columns.  For full fidelity with reference file
        storage, use ``build_row(..., ref_saver=...)`` directly.
        """
        from litmus.data.backends._row_helpers import build_row

        for step_index, step in enumerate(self.steps):
            for vector in step.vectors:
                for measurement in vector.measurements:
                    yield build_row(
                        self,
                        measurement,
                        step.name,
                        step_index,
                        vector,
                        step.instrument_arrays or {},
                        step_path=step.step_path,
                        step_started_at=step.started_at,
                        step_ended_at=step.ended_at,
                        step_node_id=step.node_id,
                        step_module=step.module,
                        step_file=step.file,
                        step_class=step.class_name,
                        step_function=step.function,
                        step_markers=step.markers,
                    )


class Waveform(BaseModel):
    """Time-series waveform data with metadata.

    Uses compressed representation where time axis is reconstructed
    from t0 + i*dt instead of storing paired timestamps.

    Attributes:
        t0: Start time (seconds from trigger)
        dt: Sample interval (seconds)
        Y: Sample values (voltage, current, etc.)
        attrs: Metadata (units, channel, coupling, etc.)
    """

    t0: float = 0.0
    dt: float
    Y: list[float]  # Sample values
    attrs: dict[str, Any] = Field(default_factory=dict)

    @property
    def num_samples(self) -> int:
        """Number of samples in the waveform."""
        return len(self.Y)

    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return self.num_samples * self.dt

    def time_axis(self) -> list[float]:
        """Reconstruct time axis: t = t0 + i*dt."""
        return [self.t0 + i * self.dt for i in range(self.num_samples)]
