"""Data models for test results."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


class Outcome(StrEnum):
    """Test outcome per ATML/IEEE 1671 terminology."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class Measurement(BaseModel):
    """A single measurement with optional limit checking."""

    name: str
    value: Decimal | None
    units: str | None = None
    low_limit: Decimal | None = None
    high_limit: Decimal | None = None
    nominal: Decimal | None = None
    outcome: Outcome | None = None
    spec_ref: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)

    def check_limit(self) -> Outcome:
        """Evaluate value against limits, set outcome, return result."""
        if self.value is None:
            self.outcome = Outcome.ERROR
        elif self.low_limit is not None and self.value < self.low_limit:
            self.outcome = Outcome.FAIL
        elif self.high_limit is not None and self.value > self.high_limit:
            self.outcome = Outcome.FAIL
        else:
            self.outcome = Outcome.PASS
        return self.outcome


class TestVector(BaseModel):
    """A test vector execution with its input parameters.

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
    """

    id: UUID = Field(default_factory=uuid4)
    test_step_id: UUID | None = None  # FK to parent TestStep
    index: int = 0  # 0-based index in the parameter expansion
    params: dict[str, Any] = Field(default_factory=dict)  # Input parameter values
    attempt: int = 1  # Current attempt number (for retries)
    max_attempts: int = 1  # Maximum attempts allowed
    outcome: Outcome = Outcome.PASS
    measurements: list[Measurement] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    error_message: str | None = None


# Alias for backward compatibility
TestCase = TestVector


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

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    outcome: Outcome = Outcome.PASS
    vectors: list[TestVector] = Field(default_factory=list)
    # Legacy: direct measurements for backward compatibility (deprecated)
    measurements: list[Measurement] = Field(default_factory=list)
    error_message: str | None = None

    # Alias for backward compatibility
    @property
    def cases(self) -> list[TestVector]:
        """Alias for vectors (deprecated, use vectors instead)."""
        return self.vectors

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

    # Legacy aliases
    @property
    def total_cases(self) -> int:
        """Alias for total_vectors (deprecated)."""
        return self.total_vectors

    @property
    def passed_cases(self) -> int:
        """Alias for passed_vectors (deprecated)."""
        return self.passed_vectors

    @property
    def failed_cases(self) -> int:
        """Alias for failed_vectors (deprecated)."""
        return self.failed_vectors


class DUT(BaseModel):
    """Device under test identification."""

    serial: str
    part_number: str | None = None
    revision: str | None = None
    lot_number: str | None = None


class TestRun(BaseModel):
    """A complete test run with steps and measurements."""

    id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    dut: DUT
    station_id: str
    station_type: str | None = None
    operator: str | None = None
    test_sequence_id: str
    test_phase: str = "production"
    outcome: Outcome = Outcome.PASS
    steps: list[TestStep] = Field(default_factory=list)
