"""Capability models for matching products to stations.

Signal/Capability hierarchy: describes what an instrument or product
can measure/source, with typed parameter dictionaries for signals,
conditions, controls, and attributes.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, model_validator

from litmus.models.enums import (
    ConnectorType,
    Direction,
    GroundTopology,
    MeasurementFunction,
    TerminalRole,
)
from litmus.utils.ranges import expand_range

# =============================================================================
# Spec-level models
# =============================================================================


class SpecQualifier(StrEnum):
    """Qualification level for a specification value.

    Mirrors industry convention from test equipment manufacturers:
    - **guaranteed**: Warranted spec — product must meet it, guardbanded for
      measurement uncertainty and environmental variation.
    - **typical**: Expected performance measured across multiple units, not warranted.
    - **nominal**: Design target or expected value, not warranted or tested.
    - **supplemental**: Informational performance data, not warranted.
    """

    GUARANTEED = "guaranteed"
    TYPICAL = "typical"
    NOMINAL = "nominal"
    SUPPLEMENTAL = "supplemental"


class RangeSpec(BaseModel):
    """Specification for measurement or output range."""

    model_config = {"extra": "forbid"}

    min: float | None = None
    max: float | None = None
    units: str = ""


class PointSpec(BaseModel):
    """A single numeric value with optional units.

    Used in SpecBand ``when`` clauses when a point value needs explicit units
    (e.g., ``frequency: {value: 100000000, units: Hz}``).
    """

    model_config = {"extra": "forbid"}

    value: float
    units: str = ""


class ListSpec(BaseModel):
    """A discrete set of allowed values with optional units.

    Used in SpecBand ``when`` clauses for membership matching
    (e.g., ``impedance: {values: [50, 600], units: ohm}``).
    """

    model_config = {"extra": "forbid"}

    values: list[str | float | bool]
    units: str = ""


class AccuracySpec(BaseModel):
    """Specification for measurement accuracy."""

    model_config = {"extra": "forbid"}

    pct_reading: float | None = None  # % of reading
    pct_range: float | None = None  # % of range
    absolute: float | None = None  # Fixed offset
    units: str | None = None  # Units of absolute value (e.g., "dB") when different from signal

    def total_uncertainty(self, value: float, range_max: float) -> float:
        """Calculate total uncertainty at a given value and range.

        Combines all applicable uncertainty components:
        - pct_reading: percentage of the measured value
        - pct_range: percentage of the full-scale range
        - absolute: fixed offset

        Returns the total uncertainty as an absolute value.
        """
        u = 0.0
        if self.pct_reading is not None:
            u += (self.pct_reading / 100) * abs(value)
        if self.pct_range is not None:
            u += (self.pct_range / 100) * abs(range_max)
        if self.absolute is not None:
            u += self.absolute
        return u


class ResolutionSpec(BaseModel):
    """Specification for measurement resolution."""

    model_config = {"extra": "forbid"}

    bits: int | None = None  # ADC resolution
    digits: float | None = None  # Display digits (e.g., 6.5)
    value: float | None = None  # Absolute resolution
    units: str | None = None


class ChannelTopology(BaseModel):
    """Physical topology of a single instrument channel.

    Describes the physical terminals, connector type, and ground topology
    for a channel. Used in catalog and instrument library entries to model
    how instruments physically connect to the DUT.

    Example YAML:
        "1":
          label: "6V/5A Output"
          terminals: [hi, lo, sense_hi, sense_lo]
          connector: binding_post
          ground: floating
    """

    model_config = {"extra": "forbid"}

    label: str | None = None  # Display name, e.g., "6V/5A Output"
    terminals: list[TerminalRole] = Field(default_factory=list)
    connector: ConnectorType | None = None
    connector_pin: dict[str, int | str] | None = None  # Terminal role → pin number/name
    ground: GroundTopology = GroundTopology.SHARED
    optional: bool = False  # Channel may not be present on all configurations


class SpecBand(BaseModel):
    """Condition-dependent specification override for a parameter.

    Each band says "at this operating point, here are the specs."
    The ``when`` keys reference sibling parameter names (signals,
    conditions, or controls); multiple keys are ANDed (all must match).
    Empty dict means unconditional (always applies).

    Any field that is ``None`` means "no override — use the top-level default."

    Example YAML (accuracy varies with frequency):
        specs:
          - when:
              frequency: {min: 3, max: 5, units: Hz}
            accuracy: {pct_reading: 0.35, pct_range: 0.03}

    Example YAML (range derated at high frequency):
        specs:
          - when:
              frequency: {min: 3e9, max: 6e9, units: Hz}
            range: {min: -130, max: 5, units: dBm}
            accuracy: {absolute: 0.8}
    """

    model_config = {"extra": "forbid"}

    when: dict[
        str,
        "RangeSpec | PointSpec | ListSpec | str | float | bool | list[str | float | bool]",
    ] = Field(default_factory=dict)
    range: RangeSpec | None = None  # Derated range at this operating point
    value: float | str | None = None  # Nominal/typical at this operating point
    units: str | None = None  # Override parent units for this band
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    qualifier: SpecQualifier | None = None


# =============================================================================
# Parameter types
# =============================================================================


class Signal(BaseModel):
    """A measurable/sourceable parameter — the primary signal dimension.

    Used for what's being measured or sourced: range defines the operating
    envelope, accuracy/resolution define the quality of measurement.
    Top-level accuracy/resolution are defaults; ``specs`` holds condition-dependent
    overrides (e.g., accuracy varies with frequency).

    Example YAML (instrument):
        signals:
          voltage:
            range: {min: 0.1, max: 1000, units: V}
            accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
            resolution: {digits: 6.5}
            specs:
              - when:
                  frequency: {min: 3, max: 5, units: Hz}
                accuracy: {pct_reading: 0.35, pct_range: 0.03}

    Example YAML (product):
        signals:
          voltage:
            value: 3.3
            units: V
    """

    model_config = {"extra": "forbid"}

    range: RangeSpec | None = None
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    value: float | None = None
    units: str | None = None
    specs: list[SpecBand] | None = None
    qualifier: SpecQualifier | None = None


class Condition(BaseModel):
    """An operating condition that affects accuracy of other parameters.

    Conditions are NOT user-adjustable — they describe the operating
    environment or calibration state under which specs were characterized.
    Use controls for user-settable parameters.

    Example YAML (continuous):
        conditions:
          frequency:
            range: {min: 3, max: 300000, units: Hz}

    Example YAML (discrete):
        conditions:
          calibration_interval:
            options: ["24_hour", "90_day", "1_year", "2_year"]
    """

    model_config = {"extra": "forbid"}

    range: RangeSpec | None = None
    options: list[float | str | bool] | None = None
    units: str | None = None
    default: float | str | bool | None = None
    specs: list[SpecBand] | None = None


class Control(BaseModel):
    """A user-configurable knob or setting.

    Controls are instrument settings the user can adjust, like motor position,
    temperature setpoint, or compliance limit. They have a range of valid
    values or a set of discrete options.

    Example YAML:
        controls:
          position:
            range: {min: 0, max: 300, units: mm}
          coupling:
            options: ["AC", "DC"]
            default: "DC"
          autorange:
            options: [true, false]
            default: true
    """

    model_config = {"extra": "forbid"}

    range: RangeSpec | None = None
    options: list[float | str | bool] | None = None
    units: str | None = None
    default: float | str | bool | None = None
    resolution: ResolutionSpec | None = None
    specs: list[SpecBand] | None = None


class Attribute(BaseModel):
    """A fixed hardware fact or performance characteristic.

    Attributes are not adjustable — they describe inherent instrument
    capabilities like bandwidth, sample rate, or input impedance.

    When an attribute varies by operating condition (e.g., test current
    depends on resistance range), use ``specs`` for condition-dependent
    overrides — same pattern as Signal.specs.

    Example YAML:
        attributes:
          bandwidth:
            value: 200000000
            units: Hz
          operating_temperature:
            range: {min: 0, max: 55, units: degC}
          test_current:
            value: 0.001
            units: A
            specs:
              - when: {range: 100}
                value: 0.001
              - when: {range: 10000}
                value: 0.0001
          scpi_version:
            value: "1997.0"
          supported_emulations:
            options: ["8340", "8360", "83700"]
    """

    model_config = {"extra": "forbid"}

    value: float | str | bool | None = None
    range: RangeSpec | None = None
    options: list[float | str | bool] | None = None
    units: str | None = None
    specs: list[SpecBand] | None = None
    qualifier: SpecQualifier | None = None

    @model_validator(mode="after")
    def _require_value_range_or_options(self) -> "Attribute":
        has_value = self.value is not None
        has_range = self.range is not None
        has_options = self.options is not None
        has_specs = self.specs is not None and len(self.specs) > 0
        count = sum([has_value, has_range, has_options])
        if count == 0 and not has_specs:
            raise ValueError(
                "Attribute must provide one of: 'value', 'range', 'options',"
                " or 'specs' (condition-dependent)"
            )
        if count > 1:
            raise ValueError(
                "Attribute cannot have more than one of 'value', 'range', and 'options'"
            )
        return self


# =============================================================================
# Condition keys vocabulary
# =============================================================================


class ConditionKey(StrEnum):
    """Canonical keys for the ``conditions`` dict on a Capability.

    Not enforced at model level; used as a shared vocabulary so products
    and instruments use the same names.

    Derived from audit of 150+ instrument datasheets across 19 vendors and IVI
    Foundation class specifications (IVI-DMM, IVI-Scope, IVI-FGen, IVI-DCPwr).
    """

    # Universal operating conditions
    FREQUENCY = "frequency"  # AC measurement frequency band
    TEMPERATURE = "temperature"  # Ambient/operating temperature
    HUMIDITY = "humidity"  # Relative humidity (specs valid at < 80% RH)
    CALIBRATION_INTERVAL = "calibration_interval"  # Time since last cal (days)

    # Measurement configuration
    NPLC = "nplc"  # Integration time in power line cycles
    AUTO_ZERO = "auto_zero"  # Auto-zero ON/OFF state
    COUPLING = "coupling"  # AC/DC coupling mode
    IMPEDANCE = "impedance"  # Input impedance (50Ω vs 1MΩ)
    SENSE_MODE = "sense_mode"  # Local (2-wire) vs remote (4-wire) sense
    SAMPLE_RATE = "sample_rate"  # Digitizing sample rate
    BANDWIDTH = "bandwidth"  # Measurement bandwidth limit
    FILTER = "filter"  # Digital filter type/order (affects noise/accuracy)
    GATE_TIME = "gate_time"  # Counter/integrator gate period
    ACQUISITION_MODE = "acquisition_mode"  # Normal/average/peak-detect/hi-res
    TIME_CONSTANT = "time_constant"  # Lock-in amplifier tau, controller response

    # Signal characteristics
    SIGNAL_LEVEL = "signal_level"  # Signal amplitude relative to range
    CREST_FACTOR = "crest_factor"  # AC waveform peak-to-RMS ratio

    # Source/load conditions
    LOAD = "load"  # Output load current
    INPUT_VOLTAGE = "input_voltage"  # Input/line voltage
    VOLTAGE = "voltage"  # Operating voltage (derating)
    CURRENT = "current"  # Operating current (derating)
    DUTY_CYCLE = "duty_cycle"  # Pulsed operation duty cycle
    SLEW_RATE = "slew_rate"  # Programmable rise/fall rate
    SETTLING_TIME = "settling_time"  # Transient recovery time

    # Sensor/detector type
    SENSOR = "sensor"  # Sensor type (RTD/TC/diode, Si/InGaAs detector)
    WAVELENGTH = "wavelength"  # Optical wavelength (accuracy varies by λ)

    # RF/signal analysis
    OFFSET = "offset"  # Offset frequency (phase noise)


# =============================================================================
# Capability models
# =============================================================================


class Capability(BaseModel):
    """What a signal endpoint can do — shared by products and instruments.

    Base class for both product characteristics and instrument capabilities.
    Describes a measurement function with direction and typed parameter dicts.

    Parameter categories (ATML/IVI/IEEE 1641 lineage):
    - ``signals``: What's being measured/sourced (range + accuracy + resolution + specs)
    - ``conditions``: What affects accuracy (range only, feeds SpecBand lookup)
    - ``controls``: User-configurable knobs (range or options)
    - ``attributes``: Fixed hardware facts (value + units + compare)
    """

    function: MeasurementFunction
    direction: Direction
    signals: dict[str, Signal] = Field(default_factory=dict)
    conditions: dict[str, Condition] = Field(default_factory=dict)
    controls: dict[str, Control] = Field(default_factory=dict)
    attributes: dict[str, Attribute] = Field(default_factory=dict)
    units: str | None = None
    specs: list[SpecBand] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_spec_band_keys(self) -> "Capability":
        """Warn when SpecBand ``when`` keys don't reference known siblings.

        Every key in ``signal.specs[].when`` should match a name in
        either ``signals``, ``conditions``, or ``controls`` on the parent
        capability. Unknown keys indicate a typo or missing declaration.
        """
        # Enforce disjoint namespaces across signals/conditions/controls
        for a_name, a_keys, b_name, b_keys in [
            ("signals", set(self.signals), "conditions", set(self.conditions)),
            ("signals", set(self.signals), "controls", set(self.controls)),
            ("conditions", set(self.conditions), "controls", set(self.controls)),
        ]:
            overlap = a_keys & b_keys
            if overlap:
                raise ValueError(
                    f"{self.function.value}: keys {sorted(overlap)} appear in "
                    f"both {a_name} and {b_name} — each dimension must appear "
                    f"in exactly one"
                )

        known = set(self.signals) | set(self.conditions) | set(self.controls)
        if not known:
            return self

        def _check_specs(owner_label: str, specs: list[SpecBand] | None) -> None:
            if not specs:
                return
            for i, band in enumerate(specs):
                for key in band.when:
                    if key not in known:
                        raise ValueError(
                            f"{self.function.value}: {owner_label} "
                            f"specs[{i}] references unknown condition key "
                            f"'{key}' (known: {sorted(known)})"
                        )

        # Build units lookup from siblings: signal/condition/control name → units
        units_map: dict[str, str] = {}
        for name, sig in self.signals.items():
            if sig.range and sig.range.units:
                units_map[name] = sig.range.units
        for name, cond in self.conditions.items():
            if cond.range and cond.range.units:
                units_map[name] = cond.range.units
        for name, ctrl in self.controls.items():
            if ctrl.range and ctrl.range.units:
                units_map[name] = ctrl.range.units

        def _resolve_when_units(specs: list[SpecBand] | None) -> None:
            if not specs:
                return
            for band in specs:
                for key, val in band.when.items():
                    if isinstance(val, RangeSpec) and not val.units and key in units_map:
                        val.units = units_map[key]
                    elif isinstance(val, PointSpec) and not val.units and key in units_map:
                        val.units = units_map[key]
                    elif isinstance(val, ListSpec) and not val.units and key in units_map:
                        val.units = units_map[key]

        for sig_name, sig in self.signals.items():
            _check_specs(f"signal '{sig_name}'", sig.specs)
            _resolve_when_units(sig.specs)
        for cond_name, cond in self.conditions.items():
            _check_specs(f"condition '{cond_name}'", cond.specs)
            _resolve_when_units(cond.specs)
        for ctrl_name, ctrl in self.controls.items():
            _check_specs(f"control '{ctrl_name}'", ctrl.specs)
            _resolve_when_units(ctrl.specs)
        for attr_name, attr in self.attributes.items():
            _check_specs(f"attribute '{attr_name}'", attr.specs)
            _resolve_when_units(attr.specs)
        return self


class InstrumentCapability(Capability):
    """Instrument capability + channels + operational metadata.

    Example YAML:
        - function: dc_voltage
          direction: input
          signals:
            voltage:
              range: {min: 0.0001, max: 1000, units: V}
              accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
              resolution: {digits: 6.5}
          conditions:
            frequency:
              range: {min: 3, max: 300000, units: Hz}
          channels: ["1"]
          readback: false
    """

    channels: str | list[str] = Field(default_factory=list)  # Range: "1:4", list, or int
    readback: bool = False  # Built-in meter, not primary measurement

    @computed_field
    @property
    def resolved_channels(self) -> list[str]:
        """Expand channels to list, handling range syntax.

        Supports:
        - Explicit list: ["1", "2", "3"] → ["1", "2", "3"]
        - Range string: "CH[1:4]" → ["CH1", "CH2", "CH3", "CH4"]
        - Numeric range: "1:4" → ["1", "2", "3", "4"]
        - Single string: "1" → ["1"]
        """
        if isinstance(self.channels, list):
            return [str(ch) for ch in self.channels]
        return expand_range(self.channels)


def band_matches(band: SpecBand, params: dict[str, float | str | bool]) -> bool:
    """Check if all ``when`` clauses in a SpecBand match the given params.

    Shared by product spec lookup and instrument capability matching.
    An empty ``when`` dict matches any query (unconditional spec).
    """
    for key, spec in band.when.items():
        val = params.get(key)
        if val is None:
            return False
        if isinstance(spec, RangeSpec):
            if isinstance(val, (int, float)):
                if spec.min is not None and val < spec.min:
                    return False
                if spec.max is not None and val > spec.max:
                    return False
        elif isinstance(spec, PointSpec):
            if val != spec.value:
                return False
        elif isinstance(spec, ListSpec):
            if val not in spec.values:
                return False
        elif isinstance(spec, list):
            if val not in spec:
                return False
        else:  # str, float, bool — equality
            if val != spec:
                return False
    return True
