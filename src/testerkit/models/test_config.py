"""Test execution configuration models.

Models for test limits, specifications, fixtures, markers,
sequences, and all test-runner configuration.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from testerkit.models.enums import Comparator, MeasurementFunction

# =============================================================================
# Marker-scope schema — runner-neutral TesterKit-marker fields, flat
# =============================================================================
#
# A "marker scope" is any node that carries TesterKit-marker fields,
# an opaque ``runner:`` overlay, and a recursive ``tests:`` tree.
# ``TestEntry`` is the recursive node; ``SidecarConfig`` is the
# file-level root (same shape); ``ProfileConfig`` (in ``models/project``)
# adds ``description`` / ``facets`` / ``extends`` on top of the same
# fields.
#
# Reserved keys at any level: ``runner``, ``tests``. Everything else
# is a TesterKit marker field with a typed Pydantic sub-model:
#
#   * ``limits``      → ``dict[str, MeasurementLimitConfig]``
#   * ``sweeps``      → ``list[SweepEntry]``
#   * ``mocks``       → ``list[MockEntry]``
#   * ``characteristics`` → ``list[str]``
#   * ``connections`` → ``list[str] | dict[str, list[Any] | str] | None``
#     (list shape = bind by fixture-connection name; dict shape =
#     bind by instrument → channel selectors. Pydantic discriminates
#     by literal shape — one-of enforced at YAML load.)
#   * ``retry``       → ``RetryConfig | None``
#   * ``prompts``     → ``dict[str, PromptConfig]``
#
# Pydantic validates every field at YAML load — typos and type errors
# fail with structured messages instead of silently passing through.


class SweepEntry(RootModel[dict[str, list[Any]]]):
    """One sweep level: ``{argname: argvalues, ...}``.

    Single key = one parametrize axis. Multiple keys = a zipped axis;
    every argvalues list must have the same length (enforced here at
    YAML-load time, before the test runs).

    The root is a dict of arbitrary user-named arg keys to their
    argvalues lists, so this is a :class:`RootModel` rather than a
    :class:`BaseModel`.
    """

    @model_validator(mode="after")
    def _validate_zip_dim_coherent(self) -> Self:
        groups = self.root
        if not groups:
            raise ValueError("sweep entry must declare at least one argname")
        if len(groups) > 1:
            lengths = {name: len(values) for name, values in groups.items()}
            unique = set(lengths.values())
            if len(unique) > 1:
                raise ValueError(
                    f"sweep zip requires all argvalues to have the same length; got {lengths}"
                )
        return self


class MockEntry(BaseModel):
    """One per-test mock — a target plus arbitrary ``patch.object`` kwargs.

    ``target`` is ``"<fixture>.<attr>"`` — the fixture name and the
    attribute on the resolved fixture value to patch. Every other key
    is forwarded verbatim to :func:`unittest.mock.patch.object`
    (``return_value``, ``side_effect``, ``wraps``, ``spec``,
    ``spec_set``, ``autospec``, ``new_callable``, …) so the surface
    tracks the stdlib's ``mock`` documentation. ``extra="allow"``
    keeps the schema permissive.
    """

    model_config = ConfigDict(extra="allow")

    target: str

    @model_validator(mode="after")
    def _validate_target_shape(self) -> Self:
        if "." not in self.target:
            raise ValueError(f"mock target {self.target!r} must be '<fixture>.<attr>' form")
        return self

    def patch_kwargs(self) -> dict[str, Any]:
        """Return all kwargs (everything except ``target``) for ``patch.object``.

        ``model_dump`` includes both declared and extra fields; ``exclude``
        drops the routing-only ``target`` so the rest passes verbatim to
        :func:`unittest.mock.patch.object`.
        """
        return self.model_dump(exclude={"target"})


class RetryConfig(BaseModel):
    """Runner-neutral retry config — translates to ``flaky`` under pytest.

    ``max_retries`` is 0-based: ``max_retries=0`` means no retries (a single
    execution); ``max_retries=2`` means up to 2 retries beyond the original
    (3 total executions). Mirrors pytest-rerunfailures' ``--reruns N`` and
    STDF's ``MIR.RTST_COD`` retest counter.
    """

    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=0, ge=0)
    delay: float = Field(default=0.0, ge=0.0)
    on: list[str] | None = None  # exception class names; None = retry on any


class TestEntry(BaseModel):
    """Recursive node in a sidecar / profile ``tests:`` tree.

    Mirrors pytest's node-id structure: a class is a branch carrying
    its own TesterKit-marker fields (applied to every nested test) and a
    ``tests:`` dict holding its methods; a function is a leaf with the
    same fields and an empty ``tests:``.

    Every TesterKit-marker field is a typed Pydantic sub-model — Pydantic
    validates at YAML load, so typos / type mismatches fail with
    structured errors before any test runs.

    ``runner:`` carries an opaque per-runner config block — for pytest,
    fields like ``markers`` (ecosystem markers like ``flaky`` / ``skip``)
    apply to this scope only. Validated by the active runner plugin
    against its own Pydantic schema.

    Example::

        tests:
          test_rail:                          # leaf
            limits:
              v_rail: {tolerance_pct: 1.0}
          TestRails:                          # branch
            sweeps:
              - {vin: [4.5, 5.0, 5.5]}
            tests:
              test_rail:                      # nested leaf
                limits:
                  v_rail: {tolerance_pct: 1.0}
                runner:
                  markers:
                    - flaky: {reruns: 2}
    """

    __test__ = False  # Prevent pytest collection (class name starts with "Test")

    model_config = ConfigDict(extra="forbid")

    limits: dict[str, MeasurementLimitConfig] = Field(default_factory=dict)
    sweeps: list[SweepEntry] = Field(default_factory=list)
    mocks: list[MockEntry] = Field(default_factory=list)
    characteristics: list[str] = Field(default_factory=list)
    # ``connections`` is a typed one-of, discriminated by shape:
    #
    #   * ``list[str]``  → bind by fixture-connection names (requires a
    #     fixture YAML to resolve the names against).
    #   * ``dict[str, Any]`` → bind by instrument → channel selectors
    #     (works pre-fixture-config for early bringup; synthesizes
    #     ``FixtureConnection`` stubs from the (instrument, channel)
    #     tuples). Values may be ``list[str | int]`` or the sentinel
    #     ``"all"`` meaning every channel on that instrument.
    #
    # Pydantic discriminates at YAML load — a string-keyed dict can't
    # be mistaken for a list, so the one-of is structurally enforced
    # without a model_validator.
    connections: list[str] | dict[str, Any] | None = None
    retry: RetryConfig | None = None
    prompts: dict[str, PromptConfig] = Field(default_factory=dict)
    runner: dict[str, Any] = Field(default_factory=dict)
    tests: dict[str, TestEntry] = Field(default_factory=dict)


class SidecarConfig(TestEntry):
    """Top-level shape of a test-module sidecar YAML.

    Same flat shape as a :class:`TestEntry`: file-level TesterKit-marker
    fields apply to every test in the module, nested ``tests:`` carries
    per-class / per-test overrides recursively.

    Example::

        limits:
          v_rail: {tolerance_pct: 5.0}
        tests:
          TestRails:
            sweeps:
              - {vin: [4.5, 5.0, 5.5]}
            tests:
              test_rail:
                limits:
                  v_rail: {tolerance_pct: 1.0}
          test_standalone:
            runner:
              markers:
                - skipif: "not os.getenv('HAS_BENCH')"
    """


# =============================================================================
# Limits & Specifications
# =============================================================================


# Per-comparator membership check. ``low``/``high``/``nominal`` are read off
# the Limit; the only thing that varies per comparator is which fields are
# required and which inequality applies.
_COMPARATOR_CHECKS: dict[str, Callable[[Limit, float], bool]] = {
    "EQ": lambda lim, v: lim.nominal is not None and v == lim.nominal,
    "NE": lambda lim, v: lim.nominal is not None and v != lim.nominal,
    "LT": lambda lim, v: lim.high is None or v < lim.high,
    "LE": lambda lim, v: lim.high is None or v <= lim.high,
    "GT": lambda lim, v: lim.low is None or v > lim.low,
    "GE": lambda lim, v: lim.low is None or v >= lim.low,
    "GELE": lambda lim, v: (
        (lim.low is None or v >= lim.low) and (lim.high is None or v <= lim.high)
    ),
    "GELT": lambda lim, v: (lim.low is None or v >= lim.low) and (lim.high is None or v < lim.high),
    "GTLE": lambda lim, v: (lim.low is None or v > lim.low) and (lim.high is None or v <= lim.high),
    "GTLT": lambda lim, v: (lim.low is None or v > lim.low) and (lim.high is None or v < lim.high),
}


class Limit(BaseModel):
    """A test limit with unit and optional spec reference.

    The comparator field (per ATML/IEEE 1671) defines how the measured
    value is compared against the limits:
        - GELE (default): low <= value <= high (inclusive range)
        - EQ: value == nominal
        - LE: value <= high
        - GE: value >= low
        - etc.

    Traceability fields:
        - characteristic_id: Structured identifier of the characteristic (e.g., "output_voltage")
        - spec_ref: Human-readable reference with conditions (e.g., "Table 4.2 @ temp=25")
    """

    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    unit: str
    characteristic_id: str | None = None  # Characteristic ID for structured traceability
    spec_ref: str | None = None  # Human-readable spec reference with conditions
    comparator: Comparator = Comparator.GELE

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "example": {
                "low": 4.5,
                "high": 5.5,
                "nominal": 5.0,
                "unit": "V",
                "characteristic_id": "output_voltage",
                "spec_ref": "Table 4.2 @ temp=25, load=0.8",
                "comparator": "GELE",
            }
        },
    }

    def __contains__(self, value: object) -> bool:
        """Return True iff ``value`` satisfies this limit's comparator.

        Supports ``value in limit`` — the pythonic assert form test
        authors use for ad-hoc checks:

            assert measured in limits["vout"]

        Pytest's assertion rewriter renders the failure via
        :meth:`__repr__` so failures include the limit fields inline.
        """
        # ``bool`` is a subclass of ``int`` in Python — explicitly reject it
        # so ``True in limits["vout"]`` doesn't silently pass.
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        check = _COMPARATOR_CHECKS.get(self.comparator)
        if check is None:
            return False
        return check(self, float(value))

    @classmethod
    def from_row(
        cls,
        *,
        low: float | None,
        high: float | None,
        nominal: float | None,
        unit: str | None,
        comparator: str | None,
        characteristic_id: str | None = None,
        spec_ref: str | None = None,
    ) -> Limit | None:
        """Build a Limit from row-level scalar fields, or None if no
        limit fields are stamped.

        Parquet rows carry the limit as scalars (``limit_low`` /
        ``limit_high`` / ``limit_nominal`` / ``limit_comparator``);
        this factory rebuilds the typed Limit so callers can use
        ``Limit.__contains__`` (and any future Limit methods) on row
        data without re-implementing the comparator logic.
        """
        if low is None and high is None and nominal is None:
            return None
        cmp = Comparator(comparator) if comparator else Comparator.GELE
        return cls(
            low=low,
            high=high,
            nominal=nominal,
            unit=unit or "",
            characteristic_id=characteristic_id,
            spec_ref=spec_ref,
            comparator=cmp,
        )

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.low is not None:
            parts.append(f"low={self.low}")
        if self.high is not None:
            parts.append(f"high={self.high}")
        if self.nominal is not None:
            parts.append(f"nominal={self.nominal}")
        if self.unit:
            parts.append(f"unit={self.unit!r}")
        parts.append(f"comparator={self.comparator.value!r}")
        return f"Limit({', '.join(parts)})"


def coerce_limit(value: Limit | dict | None) -> Limit | None:
    """Coerce a dict-form limit into a :class:`Limit` model.

    Used by ``verify`` and ``logger.measure`` to accept the YAML /
    marker dict shape directly at the call site
    (``limit={"low": ..., "high": ..., "unit": "V"}``) without
    forcing test authors to import ``Limit``. Pydantic owns the
    validation — bad keys raise ``ValidationError`` per
    ``Limit.model_config["extra"] == "forbid"``.
    """
    if isinstance(value, dict):
        return Limit.model_validate(value)
    return value


# =============================================================================
# Fixture models
# =============================================================================


class SwitchRoute(BaseModel):
    """Switch routing for a fixture point.

    Declares which switch channels to close before this point's
    instrument can be used. The platform manages the lifecycle:
    lock acquisition, channel closure, settling, and cleanup.

    Example YAML:
        route:
          switch: matrix
          channels: ["r0c0", "r0c1"]
          settling_ms: 10
    """

    model_config = {"extra": "forbid"}

    switch: str  # Station instrument role for the switch
    channels: list[str]  # Crosspoints/channels to close
    settling_ms: float = 0  # ms to wait after closing


class FixtureConnection(BaseModel):
    """A named connection on a test fixture.

    Maps a UUT pin to an instrument channel and terminal, enabling
    complete signal routing traceability. Each named connection is the
    addressable unit; an instrument channel alone is ambiguous because
    channels span instruments.

    Terminology:
    - Pin: Physical UUT connection point (J1.1, TP5)
    - Net: Schematic signal name (VOUT_3V3)
    - FixtureConnection: Named UUT-pin ↔ instrument-channel pairing (vout_measure)
    - InstrumentChannel: Physical channel on instrument (CH1, ai0)
    - InstrumentTerminal: Physical terminal (hi, lo, signal)

    Example YAML (direct wiring):
        vout_measure:
          name: vout_measure
          instrument: dmm
          instrument_channel: "1"
          instrument_terminal: hi
          uut_pin: VOUT
          net: "VOUT_3V3"

    Example YAML (switched routing):
        vout_measure:
          name: vout_measure
          instrument: dmm
          instrument_channel: "1"
          uut_pin: VOUT
          route:
            switch: matrix
            channels: ["r0c0"]
            settling_ms: 10
    """

    model_config = {"extra": "forbid"}

    name: str
    instrument: str  # Reference to instrument config
    instrument_channel: str | None = None
    instrument_terminal: str | None = None  # "hi", "lo", "signal", etc.
    description: str | None = None

    # UUT-side mapping (ATML: signal routing)
    uut_pin: str | None = None  # Reference to Part.pins key
    net: str | None = None  # Match by schematic net name

    # Measurement function this connection serves (e.g. dc_voltage,
    # ac_voltage). Optional. When set, the resolver matches connections
    # by (uut_pin, function) so a single pin can route to different
    # instruments for different characteristics (e.g. DMM for DC,
    # Scope for AC ripple). When unset, falls back to first-match by
    # pin — backward-compatible for fixtures without per-function
    # connections.
    function: MeasurementFunction | None = None

    # Switch routing (None = direct-wired, no switching needed)
    route: SwitchRoute | None = None


class FixtureSite(BaseModel):
    """A UUT site within a multi-UUT fixture.

    Each site has its own FixtureConnection mappings that route UUT pins
    to specific instrument channels. Sites share the same instrument
    roles but use different channels (or entirely different instruments).

    Example YAML:
        sites:
          - name: left
            uut_resource: /dev/ttyUSB0
            connections:
              vout_measure:
                name: vout_measure
                instrument: dmm
                instrument_channel: "1"
                uut_pin: VOUT
    """

    model_config = {"extra": "forbid"}

    name: str | None = None  # Optional human label; omit → site is just "site N"
    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    uut_resource: str | None = None  # Per-site UUT connection string
    description: str | None = None


class FixtureConfig(BaseModel):
    """Test fixture definition (UUT interface).

    Fixtures define how part pins connect to station instruments.
    They can be scoped to:
    - A specific part (part_id)
    - A part family (part_family) - for shared fixtures
    - A specific revision (part_revision) - optional refinement

    Single-UUT fixtures use ``connections`` directly. Multi-UUT fixtures
    use ``sites``, where each site has its own ``connections`` dict mapping
    UUT pins to instrument channels. The two are mutually exclusive.

    For simple setups without formal fixtures, tests can use:
    - Direct instrument access via fixtures (dmm, psu)
    - Ad-hoc pin mappings in test config
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None

    # Part scope - use one or both
    part_id: str | None = None  # Specific part (preferred)
    part_family: str | None = None  # Part family (for shared fixtures)
    part_revision: str | None = None  # Optional: specific revision

    # StationType compatibility — names the abstract station-type
    # layouts this fixture can wire against. Empty list = "any
    # station" (no cross-check fires). Validated at session start
    # against the active profile's ``station_type``.
    station_types: list[str] = Field(default_factory=list)

    # UUT connection string (e.g., COM3, /dev/ttyUSB0)
    uut_resource: str | None = None

    # UUT-pin ↔ instrument-channel pairings (single-UUT)
    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    # Multi-UUT site list (ordered; site_index = list position, never authored)
    sites: list[FixtureSite] = Field(default_factory=list)

    description: str | None = None

    @model_validator(mode="after")
    def _validate_connections_or_sites(self) -> Self:
        if self.connections and self.sites:
            raise ValueError(
                "FixtureConfig cannot have both 'connections' and 'sites'. "
                "Use 'connections' for single-UUT fixtures or 'sites' for multi-UUT."
            )
        names: list[str] = []
        for site in self.sites:
            if site.name is not None and site.name.strip().lstrip("-").isdigit():
                raise ValueError(
                    f"site name {site.name!r} is numeric; names must be non-numeric so "
                    f"--site / --uut-serials resolve index-vs-name unambiguously"
                )
            if site.name is not None:
                names.append(site.name)
        duplicates = sorted({name for name, count in Counter(names).items() if count >= 2})
        if duplicates:
            raise ValueError(
                f"FixtureConfig has duplicate site name(s): {duplicates}. "
                "Site names must be unique within a fixture so --site / "
                "--uut-serials resolve unambiguously."
            )
        return self

    @property
    def site_count(self) -> int:
        """Number of UUT sites (1 for single-UUT fixtures)."""
        return len(self.sites) if self.sites else 1

    @property
    def is_multi_site(self) -> bool:
        """True if this fixture has multiple UUT sites."""
        return len(self.sites) > 1


class PromptConfig(BaseModel):
    """Configuration for operator prompts.

    Example YAML:
        prompt:
          message: "Set chamber to {temperature}C"
          type: confirm
    """

    model_config = {"extra": "forbid"}

    message: str
    prompt_type: Literal["confirm", "choice", "input"] = "confirm"
    choices: list[str] | None = None
    timeout_seconds: int | None = None


class LimitLookupConfig(BaseModel):
    """Configuration for lookup-table based limits.

    Example YAML:
        limits:
          output_voltage:
            lookup:
              key: temperature
              table:
                -40: { low: 3.0, high: 3.6 }
                25: { low: 3.1, high: 3.5 }
    """

    model_config = {"extra": "forbid"}

    key: str
    table: dict[str, Limit]
    unit: str | None = None


class LimitStepConfig(BaseModel):
    """Configuration for step-function limits.

    Example YAML:
        limits:
          output_voltage:
            steps:
              param: load_current
              ranges:
                - below: 0.5
                  limit: { low: 3.2, high: 3.4, unit: V }
                - below: 1.0
                  limit: { low: 3.1, high: 3.5, unit: V }
                - default:
                  limit: { low: 3.0, high: 3.6, unit: V }
    """

    model_config = {"extra": "forbid"}

    param: str
    ranges: list[dict[str, Any]]  # List of {below: X, limit: {...}} or {default: ..., limit: {...}}


class MeasurementLimitConfig(BaseModel):
    """Per-measurement limit policy — direct, characteristic-derived, or banded.

    One config supports multiple shapes (resolved at measurement time):
      * **Direct** — ``low`` / ``high`` / ``nominal`` / ``unit`` literals.
      * **Characteristic policy** — ``characteristic: <id>`` plus
        ``tolerance_pct`` / ``tolerance_abs`` derives a band from the
        characteristic's nominal at the active vector params. The
        characteristic also acts as a spec reference if no explicit
        ``low`` / ``high`` is given (inherits the characteristic's
        nominal/unit/spec_ref).
      * **Banded** — ``bands: [...]`` is an ordered list of nested
        :class:`MeasurementLimitConfig` entries, each with its own
        ``when:`` predicate. The first band whose ``when:`` matches
        the active vector params wins; **if no band matches, the
        parent config itself is the catch-all** — its sibling fields
        (the ones next to ``bands:``) define the fallback limit.
      * **Callable** — ``callable: "pkg.module:function"`` — dotted
        path returning a :class:`Limit` per active vector params.
        Wired through :mod:`testerkit.execution.harness`.

    ``when`` matches against the active vector params using the same
    rule as :class:`SpecBand.when` (every key must match; empty
    ``when:`` always matches).

    **ROADMAP**: ``expr`` / ``lookup`` / ``steps`` fields are declared
    on this model but their resolver paths are not yet wired through
    :mod:`testerkit.execution.verify`. See ROADMAP "Limit resolution:
    expression / lookup / step / callable strategies" — each shape
    has a real test-engineering use case (load-curve specs,
    temperature-derated limits, formula-driven limits). Don't delete
    the fields; wire the resolver.
    """

    model_config = ConfigDict(extra="forbid")

    # Condition match (list-of-bands shape). Empty = always matches.
    # Mirrors :class:`SpecBand.when` on part characteristics.
    when: dict[str, Any] = Field(default_factory=dict)

    # Nested ordered bands; checked first when present. If none match,
    # the sibling fields on this config are the catch-all fallback.
    bands: list[MeasurementLimitConfig] = Field(default_factory=list)

    # Direct limit values
    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    unit: str | None = None
    # characteristic_id = characteristic id (structured traceability, stamped on Limit)
    # spec_ref = human-readable note about limit origin (documentation)
    characteristic_id: str | None = None
    spec_ref: str | None = None

    # Reference to a PartCharacteristic id on the active part.
    # When set, the resolver reads part.characteristics[characteristic]
    # .get_spec_at(active_vector_params) → SpecBand, using .value as the
    # nominal against which tolerance_pct / tolerance_abs / guardband_pct
    # are applied. Overrides the test-level characteristic only if the
    # test-level one is absent — one characteristic per test (see plan).
    characteristic: str | None = None

    guardband_pct: float | None = None
    comparator: Comparator | None = None

    # Expression
    expr: str | None = None
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None

    # Lookup table
    lookup: LimitLookupConfig | None = None

    # Step function
    steps: LimitStepConfig | None = None

    # Python callable
    callable: str | None = None

    def to_limit(self) -> Limit | None:
        """Convert direct limit values to a :class:`Limit`, or ``None`` if no direct values."""
        if self.low is not None or self.high is not None or self.nominal is not None:
            return Limit(
                low=self.low,
                high=self.high,
                nominal=self.nominal,
                unit=self.unit or "",
                characteristic_id=self.characteristic_id,
                spec_ref=self.spec_ref,
            )
        return None

    def has_direct_policy(self) -> bool:
        """Return True if this config carries any directly-resolvable policy.

        Used by the resolver to detect whether the parent (sibling-to-
        ``bands:``) config has its own catch-all values, vs. being a
        pure container of bands with no fallback.
        """
        return any(
            v is not None
            for v in (
                self.low,
                self.high,
                self.nominal,
                self.characteristic,
                self.tolerance_pct,
                self.tolerance_abs,
                self.expr,
                self.lookup,
                self.steps,
                self.callable,
            )
        )

    @model_validator(mode="after")
    def _require_some_policy(self) -> Self:
        """Require either a direct policy or at least one band.

        An empty config (no direct fields, no bands) would silently
        return no Limit at resolve time, which is almost certainly a
        YAML typo or a stub the user forgot to fill in. Fail at load.
        """
        if not self.has_direct_policy() and not self.bands:
            raise ValueError(
                "MeasurementLimitConfig requires at least one of: "
                "direct limit (low/high/nominal), characteristic + tolerance, "
                "expr, lookup, steps, callable, or a non-empty bands list."
            )
        return self


# Resolve forward references — TestEntry's ``limits`` / ``prompts`` /
# ``connections`` / ``retry`` field annotations are strings under
# ``from __future__ import annotations`` and reference models defined
# below this point in the module. ``model_rebuild`` finalizes the
# schema once every referenced class is in scope.
TestEntry.model_rebuild()
SidecarConfig.model_rebuild()
