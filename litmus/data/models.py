"""Data models for test results."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


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
    - fixture_point: Fixture routing point
    """

    param: str
    value: float | None = None
    units: str | None = None
    instrument: str | None = None  # Station config name (e.g., "psu_main")
    resource: str | None = None  # VISA address or connection string
    channel: str | None = None  # Channel on instrument (e.g., "CH1")
    dut_pin: str | None = None  # DUT pin being driven
    fixture_point: str | None = None  # Fixture routing point


class Outcome(StrEnum):
    """Test outcome per ATML/IEEE 1671 terminology."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"
    ABORTED = "aborted"
    NOT_TESTED = "not_tested"


class Measurement(BaseModel):
    """A single measurement with optional limit checking."""

    name: str
    value: float | None
    units: str | None = None
    low_limit: float | None = None
    high_limit: float | None = None
    nominal: float | None = None
    outcome: Outcome | None = None
    spec_id: str | None = None  # Characteristic ID for structured traceability
    spec_ref: str | None = None  # Human-readable spec reference with conditions
    comparator: str | None = None  # ATML comparator: EQ, NE, GE, LE, GELE, etc.
    timestamp: datetime = Field(default_factory=_utcnow)

    # Traceability (ATML: signal routing)
    dut_pin: str | None = None  # Which DUT pin was measured
    instrument_name: str | None = None  # Station config name (e.g., "dmm_main")
    instrument_resource: str | None = None  # VISA address or connection string
    instrument_channel: str | None = None  # Channel on instrument (e.g., "CH1")
    fixture_point: str | None = None  # Fixture point name (e.g., "VOUT")

    def check_limit(self) -> Outcome:
        """Evaluate value against limits using comparator, set outcome, return result.

        Comparator meanings (per ATML/IEEE 1671):
            EQ: value == nominal
            NE: value != nominal
            LT: value < high_limit
            LE: value <= high_limit
            GT: value > low_limit
            GE: value >= low_limit
            GELE: low_limit <= value <= high_limit (default)
            GELT: low_limit <= value < high_limit
            GTLE: low_limit < value <= high_limit
            GTLT: low_limit < value < high_limit
        """
        if self.value is None:
            self.outcome = Outcome.ERROR
            return self.outcome

        # Default to GELE (inclusive range) if no comparator specified
        comp = self.comparator or "GELE"

        if comp == "EQ":
            # Exact match to nominal
            if self.nominal is not None and self.value == self.nominal:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "NE":
            # Not equal to nominal
            if self.nominal is not None and self.value != self.nominal:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "LT":
            # Less than high limit
            if self.high_limit is not None and self.value < self.high_limit:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "LE":
            # Less than or equal to high limit
            if self.high_limit is not None and self.value <= self.high_limit:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "GT":
            # Greater than low limit
            if self.low_limit is not None and self.value > self.low_limit:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "GE":
            # Greater than or equal to low limit
            if self.low_limit is not None and self.value >= self.low_limit:
                self.outcome = Outcome.PASS
            else:
                self.outcome = Outcome.FAIL
        elif comp == "GELE":
            # Inclusive range: low <= value <= high
            low_ok = self.low_limit is None or self.value >= self.low_limit
            high_ok = self.high_limit is None or self.value <= self.high_limit
            self.outcome = Outcome.PASS if (low_ok and high_ok) else Outcome.FAIL
        elif comp == "GELT":
            # low <= value < high
            low_ok = self.low_limit is None or self.value >= self.low_limit
            high_ok = self.high_limit is None or self.value < self.high_limit
            self.outcome = Outcome.PASS if (low_ok and high_ok) else Outcome.FAIL
        elif comp == "GTLE":
            # low < value <= high
            low_ok = self.low_limit is None or self.value > self.low_limit
            high_ok = self.high_limit is None or self.value <= self.high_limit
            self.outcome = Outcome.PASS if (low_ok and high_ok) else Outcome.FAIL
        elif comp == "GTLT":
            # Exclusive range: low < value < high
            low_ok = self.low_limit is None or self.value > self.low_limit
            high_ok = self.high_limit is None or self.value < self.high_limit
            self.outcome = Outcome.PASS if (low_ok and high_ok) else Outcome.FAIL
        else:
            # Unknown comparator, fall back to GELE behavior
            low_ok = self.low_limit is None or self.value >= self.low_limit
            high_ok = self.high_limit is None or self.value <= self.high_limit
            self.outcome = Outcome.PASS if (low_ok and high_ok) else Outcome.FAIL

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
    outcome: Outcome = Outcome.PASS
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
    description: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    outcome: Outcome = Outcome.PASS
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
        return sum(1 for v in self.vectors if v.outcome == Outcome.PASS)

    @property
    def failed_vectors(self) -> int:
        """Number of failed test vectors."""
        return sum(1 for v in self.vectors if v.outcome == Outcome.FAIL)


class DUT(BaseModel):
    """Device under test identification."""

    serial: str
    part_number: str | None = None
    revision: str | None = None
    lot_number: str | None = None


class TestRun(BaseModel):
    """A complete test run with steps and measurements."""

    __test__ = False  # Prevent pytest collection

    id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None

    # DUT identification
    dut: DUT

    # Product traceability
    product_id: str | None = None
    product_name: str | None = None
    product_revision: str | None = None

    # Station traceability
    station_id: str
    station_type: str | None = None
    station_location: str | None = None

    # Fixture traceability
    fixture_id: str | None = None

    # Sequence traceability
    test_sequence_id: str
    test_phase: str = "production"

    # Operator
    operator_id: str | None = None  # from --operator
    operator_name: str | None = None  # human-readable name

    # Code traceability
    git_commit: str | None = None

    # Results
    outcome: Outcome = Outcome.PASS
    steps: list[TestStep] = Field(default_factory=list)

    # Custom metadata (user-defined fields)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    # Config snapshots for reconstruction (stored in Parquet file metadata)
    station_config_yaml: str | None = None
    product_spec_yaml: str | None = None
    fixture_config_yaml: str | None = None
    test_config_yaml: str | None = None


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
