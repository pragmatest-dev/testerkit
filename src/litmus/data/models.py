"""Data models for test results."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from litmus.models.test_config import Limit


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
    - uut_pin: UUT pin being driven
    - fixture_connection: Named fixture connection
    """

    param: str
    value: float | None = None
    units: str | None = None
    instrument: str | None = None  # Station config name (e.g., "psu_main")
    resource: str | None = None  # VISA address or connection string
    channel: str | None = None  # Channel on instrument (e.g., "CH1")
    uut_pin: str | None = None  # UUT pin being driven
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
      ``execution.verify._compute_outcome`` when ``value in limit``;
      pytest ``passed`` propagates when no measurement-level outcome
      contradicts.
    * ``FAILED`` — measurement violated its limit OR the test failed an
      assertion. Producer: ``_compute_outcome`` when ``value not in
      limit``; ``pytest_runtest_makereport`` escalates ``step.outcome``
      to ``FAILED`` when pytest reports ``failed`` and no measurement
      already failed.
    * ``ERRORED`` — exception during execution (not an assertion).
      Producer: pytest ``error`` (setup/teardown failures); uncaught
      non-AssertionError during call; ``_compute_outcome`` when value
      is ``None`` (couldn't measure → can't judge).
    * ``SKIPPED`` — explicit skip by operator/marker/condition.
      Producer: pytest ``skipped`` (``pytest.skip()`` /
      ``@pytest.mark.skip`` / skipif); ``VectorBuilder.skip()`` on the
      catch-all client API.
    * ``DONE`` — recorded value, no judgment evaluated. Producer:
      plain ``logger.measure(name, value)`` (no limit, no verify) —
      the recorder semantic. Setup/action / characterization-mode
      measurements that explicitly aren't being judged. Test engineers
      reject ``PASSED`` for un-judged actions ("don't call my setup a
      pass") — this is the answer.
    * ``TERMINATED`` — operator stopped the run and cleanup ran (safe
      states reached, fixtures torn down, parquet finalized). The
      "expected, mid-flight, but graceful" stop. TestStand calls this
      Terminated; we follow that convention so test engineers
      crossing over from TestStand/WATS recognize the semantics.
      Producer: ``pytest_keyboard_interrupt`` once we've confirmed
      teardown ran; ``connect.__exit__`` on KeyboardInterrupt /
      SystemExit; SIGTERM handler when the cleanup chain completed.
    * ``ABORTED`` — terminated WITHOUT cleanup. Reserved for the
      no-safe-state case: the close()-time fallback when no
      ``RunEnded`` was ever emitted, partial signal-handler exits,
      etc. When you see Aborted in a report it means the rig may
      not be in a known state — operator should physically check.
      Producer: ``the materializer`` fallback;
      ``RunBuilder.abort()`` on the catch-all client.

    Note on the "never ran" case: there is no ``Planned`` value.
    A step that pytest collected but never executed is signaled by
    ``outcome is None`` at finalize time — the field-missingness IS
    the receipt. The display layer derives "Never Ran" from
    ``outcome is None`` plus the run's finalized state.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"
    TERMINATED = "terminated"
    ABORTED = "aborted"
    DONE = "done"

    @property
    def severity(self) -> int:
        """Severity rank for cascade ordering. Worst wins.

        ``ABORTED`` (7) > ``TERMINATED`` (6) > ``ERRORED`` (5) >
        ``FAILED`` (4) > ``PASSED`` (3) > ``DONE`` (2) > ``SKIPPED``
        (1). See :func:`escalate_outcome` for the rationale.

        ``None`` (no outcome stamped yet) is treated as
        severity ``-1`` by :func:`escalate_outcome` — anything
        wins over an unjudged row.
        """
        return _OUTCOME_SEVERITY[self]


_OUTCOME_SEVERITY: dict[Outcome, int] = {
    Outcome.ABORTED: 7,
    Outcome.TERMINATED: 6,
    Outcome.ERRORED: 5,
    Outcome.FAILED: 4,
    Outcome.PASSED: 3,
    Outcome.DONE: 2,
    Outcome.SKIPPED: 1,
}

# Drift guard: every Outcome variant must have a severity rank. Catches
# the common bug where a new variant is added but the dict isn't updated.
assert set(Outcome) == set(_OUTCOME_SEVERITY), (
    f"_OUTCOME_SEVERITY is missing ranks for: {set(Outcome) - set(_OUTCOME_SEVERITY)}"
)


def escalate_outcome(
    current: Outcome | None,
    incoming: Outcome | None,
) -> Outcome | None:
    """Return the worse (higher-severity) of two outcomes.

    Severity, worst first:
    ``ABORTED > TERMINATED > ERRORED > FAILED > PASSED > DONE > SKIPPED``,
    with ``None`` ranked below everything ("never judged").

    Use this everywhere outcome cascading is needed (vector → step → run)
    to keep severity logic in one place. Reading the ladder:

    * ``ABORTED`` preempts everything — when cleanup didn't run, the
      operator needs to know the rig isn't in a known state.
    * ``TERMINATED`` beats ``ERRORED`` — operator-initiated stop with
      cleanup is "louder" than a test-code blow-up because the run
      didn't complete normally.
    * ``ERRORED`` beats ``FAILED`` — an unexpected blow-up is worse than
      a judged-bad value.
    * ``PASSED`` beats ``DONE`` — an actual verdict outranks a recorded-
      but-unjudged value.
    * Any concrete outcome beats ``None`` — the row was unjudged and
      now has a verdict.
    """
    cur_sev = current.severity if current is not None else -1
    inc_sev = incoming.severity if incoming is not None else -1
    return current if cur_sev >= inc_sev else incoming


def retry_aware_rollup(steps: Iterable[TestStep]) -> Outcome | None:
    """Roll up step outcomes for a parent container (class step / run).

    Groups steps by ``node_id`` so multiple attempts of the same test
    (``litmus_retry`` / ``pytest-rerunfailures``) collapse to a single
    contribution. The LAST attempt's outcome wins per group — matching
    pytest-rerunfailures' final-attempt-is-the-outcome semantics and
    the STDF retest convention (retest count is metadata; the final
    disposition is the disposition). After per-group reduction the
    final attempts are escalated via the severity ladder.

    Steps without a ``node_id`` (container steps, autouse-wrapped
    cleanup) are not collapsed — each gets its own group keyed by
    object identity. Steps with ``outcome is None`` are skipped (they
    haven't recorded a verdict yet).
    """
    # Modern Python dicts preserve insertion order; a single dict is
    # enough to bucket-by-node_id AND remember which bucket came first.
    groups: dict[Any, list[TestStep]] = {}
    for step in steps:
        if step.outcome is None:
            continue
        key: Any = step.node_id if step.node_id is not None else id(step)
        groups.setdefault(key, []).append(step)

    result: Outcome | None = None
    for bucket in groups.values():
        # Last attempt's outcome is THE outcome for this node_id.
        result = escalate_outcome(result, bucket[-1].outcome)
    return result


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
    uut_pin: str | None = None  # Which UUT pin was measured
    instrument_name: str | None = None  # Station config name (e.g., "dmm_main")
    instrument_resource: str | None = None  # VISA address or connection string
    instrument_channel: str | None = None  # Channel on instrument (e.g., "CH1")
    fixture_connection: str | None = None  # Fixture connection name (e.g., "VOUT")

    def check_limit(self) -> Outcome:
        """Evaluate value against the row's limit fields; set + return outcome.

        Single judgment path: rebuilds a :class:`Limit` from the
        stamped row fields via :meth:`Limit.from_row` and delegates
        the comparator-aware check to ``Limit.__contains__``. The
        same path :func:`litmus.execution.verify._compute_outcome`
        uses, just starting from a row instead of a Limit object.

        Outcomes:

        * ``ERRORED`` — value is None (couldn't measure → can't judge).
        * ``DONE`` — no limit fields stamped (recorder semantic).
        * ``PASSED`` / ``FAILED`` — value evaluated against the
          reconstructed limit per its comparator.
        """
        if self.value is None:
            self.outcome = Outcome.ERRORED
            return self.outcome
        limit = Limit.from_row(
            low=self.limit_low,
            high=self.limit_high,
            nominal=self.limit_nominal,
            units=self.units,
            comparator=self.limit_comparator,
            characteristic_id=self.characteristic_id,
            spec_ref=self.spec_ref,
        )
        if limit is None:
            self.outcome = Outcome.DONE
            return self.outcome
        self.outcome = Outcome.PASSED if self.value in limit else Outcome.FAILED
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
    retry: int = 0  # Retry counter — 0 for the first execution, N for the Nth retry
    max_retries: int = 0  # Maximum retries allowed (0 = no retries, single execution)
    outcome: Outcome | None = None
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
    outcome: Outcome | None = None
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
    """A pytest item collected for execution (before any run).

    ``step_index`` and ``vector_index`` are computed at collection time,
    pre-reorder for class-level sweeps:

    * ``step_index`` = position within the parent sequence (root for classless
      methods, or within the test class). All sweep variants of the same
      function share one ``step_index``.
    * ``vector_index`` = 0-based position within the sweep expansion.
    * ``vector_count_planned`` = number of items collected for this logical
      step. Lets the manifest detect unrun vectors after the run.
    """

    node_id: str
    file: str | None = None
    module: str | None = None
    class_name: str | None = None
    function: str | None = None
    markers: str | None = None
    # step_path / parent_path: computed at collection time so that
    # unrun items (filtered out, errored before reach, or unrun
    # vectors of a partial sweep) carry the same hierarchical
    # identifier as executed step events would.
    step_path: str = ""
    parent_path: str = ""
    step_index: int = 0
    vector_index: int = 0
    vector_count_planned: int = 1


class UUT(BaseModel):
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
    uut_serial: str | None = None
    uut_part_number: str | None = None
    part_id: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_hostname: str | None = None
    fixture_id: str | None = None
    test_phase: str | None = None
    operator: str | None = None
    outcome: str | None = None
    total_measurements: int = 0
    total_steps: int = 0
    project_name: str | None = None
    file_path: str | None = None  # internal: parquet file location for fast measurement lookup


class TestRun(BaseModel):
    """A complete test run with steps and measurements."""

    __test__ = False  # Prevent pytest collection

    id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)  # Cross-store join key; set by logger
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None

    # UUT identification
    uut: UUT

    # Part traceability
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None

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
    outcome: Outcome | None = None
    steps: list[TestStep] = Field(default_factory=list)

    # Collected items (full list from pytest collection, before execution)
    collected_items: list[CollectedItem] = Field(default_factory=list)

    # Custom metadata (user-defined fields)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    # Environment snapshot (stored in Parquet file-level metadata)
    environment_json: str | None = None


class Waveform(BaseModel):
    """Time-series waveform data with metadata.

    Uses compressed representation where the time axis is reconstructed
    from ``t0 + i * dt`` instead of storing paired timestamps.

    Attributes:
        t0: Absolute UTC timestamp of the first sample. ``None`` when
            the producer doesn't know the wall-clock time (e.g. a
            synthesized or hardware-trigger-relative capture). For
            scope captures where samples are relative to a trigger,
            store the trigger offset in ``attributes`` and set ``t0``
            to the trigger's absolute time.
        dt: Sample interval (seconds).
        Y: Sample values (voltage, current, etc.).
        attributes: Metadata (units, channel, coupling, trigger
            offset, etc.). Renamed from ``attrs`` in build item 17 for
            cross-schema vocabulary consistency (matches
            FileArtifactMetadata.attributes and
            ChannelDescriptor.attributes).
    """

    t0: datetime | None = None
    dt: float
    Y: list[float]  # Sample values
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def num_samples(self) -> int:
        """Number of samples in the waveform."""
        return len(self.Y)

    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return self.num_samples * self.dt

    def time_axis(self) -> list[datetime]:
        """Reconstruct the absolute time axis: ``t0 + i * dt`` for each sample.

        Raises ``ValueError`` if ``t0`` is None — without an anchor,
        the absolute axis can't be reconstructed. Callers that only
        need relative time should compute ``[i * dt for i in
        range(num_samples)]`` directly.
        """
        if self.t0 is None:
            raise ValueError(
                "Waveform.time_axis() requires t0 to be set. "
                "For relative-only time, use [i * dt for i in range(num_samples)]."
            )
        return [self.t0 + timedelta(seconds=i * self.dt) for i in range(self.num_samples)]


class XYData(BaseModel):
    """Paired x/y arrays for related-but-non-time-series data (item 15).

    For data the test author thinks of as one artifact rather than two
    parallel channels: IV curves, eye diagrams, S-parameter sweeps,
    optical spectra. Per the §4 manifestation rules, this is "Pattern
    B" — one discrete artifact per vector that routes to FileStore.

    ``observe(name, XYData(...))`` registers via the serializer
    registry (build item 12) and lands on disk as a single ``.npz``
    holding ``x``, ``y``, and any of the optional unit/name keys
    that were set. The MIME convention (build item 13) is
    ``application/x-numpy-npz``.

    Use parallel channels (`stream`) instead when the data is
    continuous over time and you want it live-subscribable — see §4
    Pattern A.

    Attributes:
        x: Independent-axis values.
        y: Dependent-axis values. Must have the same length as ``x``.
        x_units: Optional units for the x axis ("V", "Hz", "dBm").
        y_units: Optional units for the y axis.
        x_name: Optional human label for the x axis ("Bias voltage").
        y_name: Optional human label for the y axis.
    """

    x: list[float]
    y: list[float]
    x_units: str | None = None
    y_units: str | None = None
    x_name: str | None = None
    y_name: str | None = None
