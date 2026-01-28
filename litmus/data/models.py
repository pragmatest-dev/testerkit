"""Data models for test results."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


class PassFail(str, Enum):
    """Test result status."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class Measurement(BaseModel):
    """A single measurement with optional limit checking."""

    name: str
    value: Decimal | None
    units: str | None = None
    low_limit: Decimal | None = None
    high_limit: Decimal | None = None
    nominal: Decimal | None = None
    pass_fail: PassFail | None = None
    spec_ref: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)

    def check_limit(self) -> PassFail:
        """Evaluate value against limits, set pass_fail, return result."""
        if self.value is None:
            self.pass_fail = PassFail.ERROR
        elif self.low_limit is not None and self.value < self.low_limit:
            self.pass_fail = PassFail.FAIL
        elif self.high_limit is not None and self.value > self.high_limit:
            self.pass_fail = PassFail.FAIL
        else:
            self.pass_fail = PassFail.PASS
        return self.pass_fail


class TestStep(BaseModel):
    """A test step containing measurements."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    pass_fail: PassFail = PassFail.PASS
    measurements: list[Measurement] = Field(default_factory=list)
    error_message: str | None = None


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
    pass_fail: PassFail = PassFail.PASS
    steps: list[TestStep] = Field(default_factory=list)
