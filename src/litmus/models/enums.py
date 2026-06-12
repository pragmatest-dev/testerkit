"""Shared enums for Litmus configuration — capability vocabulary.

These enums are the shared vocabulary between parts and instruments.
They define WHAT can be measured/sourced (MeasurementFunction),
in which direction (Direction), and with what physical properties.
"""

from enum import StrEnum

# =============================================================================
# Core Capability Enums
# =============================================================================


class Direction(StrEnum):
    """Direction of signal flow for a capability."""

    INPUT = "input"  # Signal/sense from UUT
    OUTPUT = "output"  # Source/drive to UUT
    BIDIR = "bidir"  # Both (SMU, VNA)
    TRANSFORM = "transform"  # Signal-path component (amplifier, filter, mixer)


class MeasurementFunction(StrEnum):
    """Named signal measurement/stimulus functions.

    Standards-grounded taxonomy derived from IVI Foundation instrument classes,
    IEEE 1641 signal primitives, and SCPI naming conventions. Designed for ALL
    electronics hardware test: DC, AC, RF, mixed-signal, digital, optical, thermal.

    Design principles:
    - One enum for instruments AND parts (direction distinguishes measure vs source)
    - Functions describe WHAT, not HOW (dc_voltage not dmm_dc_volts)
    - Flat enum (grouped by comment only, no hierarchy)
    - Waveform shapes are parameters, not functions (use WaveformShape enum)
    - Instrument-class-neutral (both DMM and scope can measure dc_voltage)
    """

    # DMM functions (IVI-DMM)
    DC_VOLTAGE = "dc_voltage"
    AC_VOLTAGE = "ac_voltage"
    DC_CURRENT = "dc_current"
    AC_CURRENT = "ac_current"
    RESISTANCE = "resistance"
    RESISTANCE_4W = "resistance_4w"
    CAPACITANCE = "capacitance"
    INDUCTANCE = "inductance"
    IMPEDANCE = "impedance"
    FREQUENCY = "frequency"
    PERIOD = "period"
    TEMPERATURE = "temperature"

    # Oscilloscope / waveform capture (IVI-Scope)
    WAVEFORM = "waveform"

    # Power supply / load functions (IVI-DCPwr)
    DC_POWER = "dc_power"
    AC_POWER = "ac_power"

    # SMU functions (combined source-measure)
    # Use DC_VOLTAGE/DC_CURRENT with direction=bidir for SMU

    # RF functions (IVI-RFSigGen, IVI-PwrMeter, IVI-SpecAn)
    RF_POWER = "rf_power"
    RF_CW = "rf_cw"
    RF_AM = "rf_am"  # Amplitude modulation of RF carrier
    RF_FM = "rf_fm"  # Frequency modulation of RF carrier
    RF_PM = "rf_pm"  # Phase modulation of RF carrier
    RF_SWEEP = "rf_sweep"  # RF frequency/power sweep
    RF_IQ = "rf_iq"  # IQ vector modulation
    RF_PULSE = "rf_pulse"  # Pulse on/off modulation of RF carrier
    S_PARAMETERS = "s_parameters"
    SPECTRUM = "spectrum"
    PHASE_NOISE = "phase_noise"
    NOISE_FIGURE = "noise_figure"
    HARMONICS = "harmonics"

    # Digital / logic (IVI-Counter, digital I/O)
    DIGITAL_PATTERN = "digital_pattern"
    DIGITAL_IO = "digital_io"
    SERIAL_DATA = "serial_data"

    # DMM specialty functions
    DIODE = "diode"
    CONTINUITY = "continuity"
    DC_RATIO = "dc_ratio"

    # RLC meter functions (IVI-LCR)
    QUALITY_FACTOR = "quality_factor"
    DISSIPATION_FACTOR = "dissipation_factor"

    # Time/edge measurements (IVI-Counter, IVI-Scope)
    TIME_INTERVAL = "time_interval"
    PULSE_WIDTH = "pulse_width"
    DUTY_CYCLE = "duty_cycle"
    RISE_TIME = "rise_time"
    FALL_TIME = "fall_time"

    # Phase measurement
    PHASE = "phase"

    # Signal integrity
    POWER_QUALITY = "power_quality"
    JITTER = "jitter"
    EYE_DIAGRAM = "eye_diagram"

    # Signal quality metrics (part datasheet specs)
    THD = "thd"  # Total harmonic distortion (also THD+N)
    SNR = "snr"  # Signal-to-noise ratio (also SINAD)
    GAIN = "gain"  # Signal transfer ratio (RF amps, lock-in, signal chain)

    # RF network measurements (VNA-derived, but named part specs)
    RETURN_LOSS = "return_loss"  # S11 magnitude — "return loss > 20 dB"
    INSERTION_LOSS = "insertion_loss"  # S21 magnitude — "insertion loss < 0.5 dB"
    VSWR = "vswr"  # Voltage standing wave ratio — "VSWR < 1.5:1"
    GROUP_DELAY = "group_delay"  # Phase derivative — "group delay < 2 ns"

    # Optical (IVI-OpticalAttenuator, IVI-OpticalPowerMeter)
    OPTICAL_POWER = "optical_power"
    WAVELENGTH = "wavelength"

    # Environmental
    HUMIDITY = "humidity"  # Relative humidity measurement

    # Electrometer / charge measurement
    CHARGE = "charge"  # Accumulated charge (fC to µC)

    # Magnetic field (Gaussmeter)
    MAGNETIC_FIELD = "magnetic_field"

    # Position/motion (encoder, stage)
    POSITION = "position"

    # Lock-in amplifier
    LOCK_IN_DETECTION = "lock_in_detection"  # Phase-sensitive AC demodulation

    # Cryogenic/thermal control
    HEATER_POWER = "heater_power"  # Heater output for cryogenic/furnace controllers
    EXCITATION_CURRENT = "excitation_current"  # Precision current for bridge/RTD excitation

    # Pulse/trigger
    PULSE_GENERATION = "pulse_generation"  # Precision delay/pulse generator output
    TRIGGER = "trigger"  # Trigger signal input/output
    REFERENCE_CLOCK = "reference_clock"  # 10 MHz reference oscillator I/O

    # Impedance components (LCR meter)
    CONDUCTANCE = "conductance"  # DC conductance (G = 1/R, siemens)
    REACTANCE = "reactance"  # Reactive impedance component (Ω)
    SUSCEPTANCE = "susceptance"  # Imaginary admittance (siemens)

    # Electronic load
    DYNAMIC_LOAD = "dynamic_load"  # AC/transient electronic load mode


class WaveformShape(StrEnum):
    """Waveform shapes for function generator outputs.

    Used as a parameter value for capabilities with function=WAVEFORM,
    not as separate MeasurementFunction values. Per IEEE 1641, waveform
    shapes are characteristics of the signal, not distinct signal types.
    """

    SINE = "sine"
    SQUARE = "square"
    TRIANGLE = "triangle"
    RAMP = "ramp"
    PULSE = "pulse"
    ARBITRARY = "arbitrary"
    NOISE = "noise"
    DC = "dc"


class TerminalRole(StrEnum):
    """Physical terminal on an instrument channel (ATE/IVI standard names)."""

    HI = "hi"  # High-side force terminal (positive)
    LO = "lo"  # Low-side / return terminal (negative/ground)
    SENSE_HI = "sense_hi"  # Remote sense high (Kelvin connection)
    SENSE_LO = "sense_lo"  # Remote sense low
    GUARD = "guard"  # Guard terminal (triax center)
    GROUND = "ground"  # Chassis / earth ground terminal
    SIGNAL = "signal"  # Single-ended signal (BNC center, probe tip)
    TRIGGER = "trigger"  # Trigger I/O
    HCUR = "hcur"  # High current (impedance analyzer)
    HPOT = "hpot"  # High potential (impedance analyzer)
    LCUR = "lcur"  # Low current (impedance analyzer)
    LPOT = "lpot"  # Low potential (impedance analyzer)


class GroundTopology(StrEnum):
    """How channel grounds relate to each other and earth."""

    FLOATING = "floating"  # Channels isolated from each other (typical PSU)
    SHARED = "shared"  # All channels share common ground (typical scope, DMM)
    EARTH = "earth"  # Referenced to earth ground


class ConnectorType(StrEnum):
    """Physical connector type on instrument."""

    BINDING_POST = "binding_post"
    BANANA = "banana"
    BNC = "bnc"
    TERMINAL_BLOCK = "terminal_block"
    PROBE = "probe"
    TRIAX = "triax"
    SMA = "sma"
    SMB = "smb"
    SPRING = "spring"
    PXI = "pxi"
    SCREW_TERMINAL = "screw_terminal"
    DSUB = "dsub"
    VHDCI = "vhdci"
    APC_35 = "apc_3.5"
    TYPE_N = "type_n"
    K_24MM = "k_2.4mm"
    V_185MM = "v_1.85mm"
    PHOENIX = "phoenix"
    TEKVPI = "tekvpi"
    D_SUB_9 = "d_sub_9"
    D_SUB_15 = "d_sub_15"
    PROPRIETARY = "proprietary"


# Coaxial connectors that inherently include a shield/ground conductor
COAXIAL_CONNECTORS: frozenset[ConnectorType] = frozenset(
    {
        ConnectorType.BNC,
        ConnectorType.SMA,
        ConnectorType.SMB,
        ConnectorType.TYPE_N,
        ConnectorType.APC_35,
        ConnectorType.K_24MM,
        ConnectorType.V_185MM,
    }
)

# Triax connectors have both shield AND guard
TRIAX_CONNECTORS: frozenset[ConnectorType] = frozenset(
    {
        ConnectorType.TRIAX,
    }
)


class MatchDepth(StrEnum):
    """How deep to check when matching capabilities.

    Each level includes all checks from previous levels:
    - FUNCTION: MeasurementFunction match only
    - DIRECTION: + direction match
    - RANGE: + parameter range containment (current default)
    - ACCURACY: + accuracy comparison
    - RESOLUTION: + resolution comparison
    """

    FUNCTION = "function"
    DIRECTION = "direction"
    RANGE = "range"
    ACCURACY = "accuracy"
    RESOLUTION = "resolution"


class Comparator(StrEnum):
    """Limit comparators per ATML/IEEE 1671.

    Used for limit checking in both instrument capabilities and part specs.
    The comparator defines how a measured value is compared against limits.

    Single-bound comparators:
        EQ: value == nominal (exact match)
        NE: value != nominal (not equal)
        LT: value < high (less than)
        LE: value <= high (less than or equal)
        GT: value > low (greater than)
        GE: value >= low (greater than or equal)

    Range comparators (two bounds):
        GELE: low <= value <= high (inclusive range, most common)
        GELT: low <= value < high (inclusive low, exclusive high)
        GTLE: low < value <= high (exclusive low, inclusive high)
        GTLT: low < value < high (exclusive range)
    """

    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    GELE = "GELE"
    GELT = "GELT"
    GTLE = "GTLE"
    GTLT = "GTLT"


class InstrumentType(StrEnum):
    """Instrument classification vocabulary.

    The 13 IVI Foundation instrument classes form the core, extended with
    additional types common in electronics test (SMU, DAQ, LCR, etc.).

    Advisory, not enforced — InstrumentCatalogEntry.type is str so users
    can add custom types without modifying litmus. The normalizer warns
    on unknown types.
    """

    # IVI Foundation classes (https://www.ivifoundation.org/About-IVI/Instrument-Classes.html)
    DMM = "dmm"
    OSCILLOSCOPE = "oscilloscope"
    FUNCTION_GENERATOR = "function_generator"
    PSU = "psu"
    AC_POWER_SUPPLY = "ac_power_supply"
    SWITCH = "switch"
    POWER_METER = "power_meter"
    SPECTRUM_ANALYZER = "spectrum_analyzer"
    RF_SIGNAL_GENERATOR = "rf_signal_generator"
    UPCONVERTER = "upconverter"
    DOWNCONVERTER = "downconverter"
    DIGITIZER = "digitizer"
    COUNTER = "counter"

    # Extended types (not in IVI but common in electronics test)
    SMU = "smu"
    ELECTRONIC_LOAD = "electronic_load"
    DAQ = "daq"
    LCR_METER = "lcr_meter"
    VNA = "vna"
    TEMPERATURE_CONTROLLER = "temperature_controller"
    ELECTROMETER = "electrometer"
    LOCK_IN_AMPLIFIER = "lock_in_amplifier"
    CURRENT_SOURCE = "current_source"
    PULSE_GENERATOR = "pulse_generator"
    GAUSSMETER = "gaussmeter"
