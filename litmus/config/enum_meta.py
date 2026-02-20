"""Metadata registry for MeasurementFunction and ConditionKey enums.

Provides abbreviation-based reverse lookup so AI tools can resolve
datasheet shorthand (e.g. "FRES" → resistance_4w, "DCV" → dc_voltage).

The enums themselves stay lean (StrEnum only); all metadata lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# =============================================================================
# Forward registries: enum_value → metadata
# =============================================================================

MEASUREMENT_FUNCTION_META: dict[str, dict] = {
    "dc_voltage": {
        "abbreviations": ["DCV", "VDC", "DC voltage", "DC volts", "VOLT:DC"],
        "name": "DC voltage",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:VOLT:DC?",
        "instrument_classes": ["dmm", "scope", "smu", "daq", "psu"],
    },
    "ac_voltage": {
        "abbreviations": ["ACV", "VAC", "AC voltage", "AC volts", "VOLT:AC"],
        "name": "AC voltage",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:VOLT:AC?",
        "instrument_classes": ["dmm", "scope", "daq"],
    },
    "dc_current": {
        "abbreviations": ["DCI", "ADC", "IDC", "DC current", "DC amps", "CURR:DC"],
        "name": "DC current",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:CURR:DC?",
        "instrument_classes": ["dmm", "smu", "psu", "eload"],
    },
    "ac_current": {
        "abbreviations": ["ACI", "AAC", "IAC", "AC current", "AC amps", "CURR:AC"],
        "name": "AC current",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:CURR:AC?",
        "instrument_classes": ["dmm"],
    },
    "resistance": {
        "abbreviations": ["RES", "OHM", "ohms", "2-wire", "2W"],
        "name": "Resistance (2-wire)",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:RES?",
        "instrument_classes": ["dmm", "daq"],
    },
    "resistance_4w": {
        "abbreviations": ["FRES", "4W", "4-wire", "four-wire", "Kelvin"],
        "name": "Resistance (4-wire)",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:FRES?",
        "instrument_classes": ["dmm", "daq"],
    },
    "capacitance": {
        "abbreviations": ["CAP", "farads", "F"],
        "name": "Capacitance",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:CAP?",
        "instrument_classes": ["dmm", "lcr"],
    },
    "inductance": {
        "abbreviations": ["IND", "henries", "H"],
        "name": "Inductance",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:IND?",
        "instrument_classes": ["lcr", "dmm"],
    },
    "impedance": {
        "abbreviations": ["Z", "IMP"],
        "name": "Impedance",
        "ivi_class": "IviLCR",
        "scpi": ":MEAS:Z?",
        "instrument_classes": ["lcr", "vna"],
    },
    "frequency": {
        "abbreviations": ["FREQ", "Hz", "hertz"],
        "name": "Frequency",
        "ivi_class": "IviDmm, IviCounter",
        "scpi": ":MEAS:FREQ?",
        "instrument_classes": ["dmm", "counter", "scope", "fgen"],
    },
    "period": {
        "abbreviations": ["PER"],
        "name": "Period",
        "ivi_class": "IviDmm, IviCounter",
        "scpi": ":MEAS:PER?",
        "instrument_classes": ["dmm", "counter"],
    },
    "temperature": {
        "abbreviations": ["TEMP", "RTD", "thermocouple", "TC"],
        "name": "Temperature",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:TEMP?",
        "instrument_classes": ["dmm", "daq", "temp_controller"],
    },
    "waveform": {
        "abbreviations": ["WAV", "wfm", "trace"],
        "name": "Waveform",
        "ivi_class": "IviScope, IviFgen, IviDigitizer",
        "scpi": "",
        "instrument_classes": ["scope", "fgen", "digitizer"],
    },
    "dc_power": {
        "abbreviations": ["DC power", "watts DC", "PDC"],
        "name": "DC power",
        "ivi_class": "IviDCPwr",
        "scpi": "",
        "instrument_classes": ["psu", "eload", "smu"],
    },
    "ac_power": {
        "abbreviations": ["AC power", "watts AC", "PAC"],
        "name": "AC power",
        "ivi_class": "IviACPwr",
        "scpi": "",
        "instrument_classes": ["ac_source", "power_analyzer"],
    },
    "rf_power": {
        "abbreviations": ["RF power", "dBm", "RF pwr"],
        "name": "RF power",
        "ivi_class": "IviPwrMeter",
        "scpi": "",
        "instrument_classes": ["rf_power_meter", "spectrum_analyzer"],
    },
    "rf_cw": {
        "abbreviations": ["CW", "continuous wave", "RF CW"],
        "name": "RF continuous wave",
        "ivi_class": "IviRFSigGen",
        "scpi": ":OUTP:FREQ",
        "instrument_classes": ["rf_siggen"],
    },
    "rf_am": {
        "abbreviations": ["AM", "amplitude modulation"],
        "name": "RF amplitude modulation",
        "ivi_class": "IviRFSigGen",
        "scpi": ":AM",
        "instrument_classes": ["rf_siggen"],
    },
    "rf_fm": {
        "abbreviations": ["FM", "frequency modulation"],
        "name": "RF frequency modulation",
        "ivi_class": "IviRFSigGen",
        "scpi": ":FM",
        "instrument_classes": ["rf_siggen"],
    },
    "rf_pm": {
        "abbreviations": ["PM", "phase modulation", "ΦM"],
        "name": "RF phase modulation",
        "ivi_class": "IviRFSigGen",
        "scpi": ":PM",
        "instrument_classes": ["rf_siggen"],
    },
    "rf_sweep": {
        "abbreviations": ["sweep", "freq sweep", "power sweep"],
        "name": "RF sweep",
        "ivi_class": "IviRFSigGen",
        "scpi": ":SWE",
        "instrument_classes": ["rf_siggen", "vna"],
    },
    "rf_iq": {
        "abbreviations": ["IQ", "I/Q", "vector modulation"],
        "name": "RF IQ modulation",
        "ivi_class": "IviRFSigGen",
        "scpi": ":IQ",
        "instrument_classes": ["rf_siggen"],
    },
    "rf_pulse": {
        "abbreviations": ["pulse mod", "RF pulse"],
        "name": "RF pulse modulation",
        "ivi_class": "IviRFSigGen",
        "scpi": ":PULM",
        "instrument_classes": ["rf_siggen"],
    },
    "s_parameters": {
        "abbreviations": ["S-param", "S11", "S21", "S12", "S22", "S-parameters", "Sparam"],
        "name": "S-parameters",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna"],
    },
    "spectrum": {
        "abbreviations": ["SPEC", "FFT", "spectral"],
        "name": "Spectrum",
        "ivi_class": "IviSpecAn",
        "scpi": "",
        "instrument_classes": ["spectrum_analyzer", "scope"],
    },
    "phase_noise": {
        "abbreviations": ["PN", "phase noise", "L(f)"],
        "name": "Phase noise",
        "ivi_class": "IviSpecAn",
        "scpi": "",
        "instrument_classes": ["spectrum_analyzer", "signal_analyzer"],
    },
    "noise_figure": {
        "abbreviations": ["NF", "noise figure"],
        "name": "Noise figure",
        "ivi_class": "IviSpecAn",
        "scpi": "",
        "instrument_classes": ["spectrum_analyzer", "noise_analyzer"],
    },
    "harmonics": {
        "abbreviations": ["HARM", "harmonic"],
        "name": "Harmonics",
        "ivi_class": "IviSpecAn",
        "scpi": "",
        "instrument_classes": ["spectrum_analyzer", "power_analyzer"],
    },
    "digital_pattern": {
        "abbreviations": ["DIG", "digital", "pattern"],
        "name": "Digital pattern",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["digital_io", "pattern_gen"],
    },
    "digital_io": {
        "abbreviations": ["DIO", "GPIO"],
        "name": "Digital I/O",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["digital_io", "daq"],
    },
    "serial_data": {
        "abbreviations": ["SPI", "I2C", "UART", "CAN", "serial", "protocol"],
        "name": "Serial data",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["scope", "protocol_analyzer"],
    },
    "diode": {
        "abbreviations": ["DIOD", "diode test", "Vf"],
        "name": "Diode",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:DIOD?",
        "instrument_classes": ["dmm"],
    },
    "continuity": {
        "abbreviations": ["CONT", "continuity test", "beep"],
        "name": "Continuity",
        "ivi_class": "IviDmm",
        "scpi": ":MEAS:CONT?",
        "instrument_classes": ["dmm"],
    },
    "quality_factor": {
        "abbreviations": ["Q", "Q factor", "QF"],
        "name": "Quality factor",
        "ivi_class": "IviLCR",
        "scpi": "",
        "instrument_classes": ["lcr"],
    },
    "dissipation_factor": {
        "abbreviations": ["D", "DF", "tan delta", "dissipation"],
        "name": "Dissipation factor",
        "ivi_class": "IviLCR",
        "scpi": "",
        "instrument_classes": ["lcr"],
    },
    "time_interval": {
        "abbreviations": ["TI", "time interval", "gate"],
        "name": "Time interval",
        "ivi_class": "IviCounter",
        "scpi": "",
        "instrument_classes": ["counter"],
    },
    "pulse_width": {
        "abbreviations": ["PW", "pulse width", "tpw"],
        "name": "Pulse width",
        "ivi_class": "IviCounter",
        "scpi": "",
        "instrument_classes": ["counter", "scope"],
    },
    "duty_cycle": {
        "abbreviations": ["DC", "duty", "duty cycle"],
        "name": "Duty cycle",
        "ivi_class": "IviCounter",
        "scpi": "",
        "instrument_classes": ["counter", "scope", "fgen"],
    },
    "rise_time": {
        "abbreviations": ["tr", "rise time", "trise"],
        "name": "Rise time",
        "ivi_class": "IviCounter, IviScope",
        "scpi": "",
        "instrument_classes": ["counter", "scope"],
    },
    "fall_time": {
        "abbreviations": ["tf", "fall time", "tfall"],
        "name": "Fall time",
        "ivi_class": "IviCounter, IviScope",
        "scpi": "",
        "instrument_classes": ["counter", "scope"],
    },
    "phase": {
        "abbreviations": ["PHI", "phase angle", "deg"],
        "name": "Phase",
        "ivi_class": "IviScope",
        "scpi": "",
        "instrument_classes": ["scope", "lockin", "vna"],
    },
    "power_quality": {
        "abbreviations": ["PQ", "power quality"],
        "name": "Power quality",
        "ivi_class": "IviACPwr",
        "scpi": "",
        "instrument_classes": ["power_analyzer"],
    },
    "jitter": {
        "abbreviations": ["JIT", "jitter", "TIE"],
        "name": "Jitter",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["scope", "timing_analyzer"],
    },
    "eye_diagram": {
        "abbreviations": ["eye", "eye diagram", "eye pattern"],
        "name": "Eye diagram",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["scope"],
    },
    "thd": {
        "abbreviations": ["THD", "total harmonic distortion"],
        "name": "Total harmonic distortion",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["power_analyzer", "audio_analyzer"],
    },
    "snr": {
        "abbreviations": ["SNR", "signal-to-noise", "S/N"],
        "name": "Signal-to-noise ratio",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["spectrum_analyzer", "audio_analyzer"],
    },
    "gain": {
        "abbreviations": ["GAIN", "amplification", "dB gain"],
        "name": "Gain",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna", "spectrum_analyzer", "lockin"],
    },
    "return_loss": {
        "abbreviations": ["RL", "return loss", "S11"],
        "name": "Return loss",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna"],
    },
    "insertion_loss": {
        "abbreviations": ["IL", "insertion loss", "S21 loss"],
        "name": "Insertion loss",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna"],
    },
    "vswr": {
        "abbreviations": ["VSWR", "SWR", "voltage standing wave ratio"],
        "name": "VSWR",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna"],
    },
    "group_delay": {
        "abbreviations": ["GD", "group delay", "τg"],
        "name": "Group delay",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["vna"],
    },
    "optical_power": {
        "abbreviations": ["OPT", "optical power", "dBm optical"],
        "name": "Optical power",
        "ivi_class": "IviOpticalPowerMeter",
        "scpi": "",
        "instrument_classes": ["optical_power_meter"],
    },
    "wavelength": {
        "abbreviations": ["WL", "lambda", "λ", "nm"],
        "name": "Wavelength",
        "ivi_class": "IviOpticalWavelengthMeter",
        "scpi": "",
        "instrument_classes": ["optical_wavelength_meter"],
    },
    "humidity": {
        "abbreviations": ["RH", "humidity", "relative humidity"],
        "name": "Humidity",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["environmental"],
    },
    "charge": {
        "abbreviations": ["Q", "charge", "coulombs", "C"],
        "name": "Charge",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["electrometer", "smu"],
    },
    "magnetic_field": {
        "abbreviations": ["B", "gauss", "tesla", "magnetic"],
        "name": "Magnetic field",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["gaussmeter"],
    },
    "position": {
        "abbreviations": ["POS", "position", "encoder"],
        "name": "Position",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["motion_controller"],
    },
    "lock_in_detection": {
        "abbreviations": ["lock-in", "LIA", "phase-sensitive detection", "PSD"],
        "name": "Lock-in detection",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["lockin"],
    },
    "heater_power": {
        "abbreviations": ["heater", "HTR"],
        "name": "Heater power",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["temp_controller"],
    },
    "excitation_current": {
        "abbreviations": ["excitation", "Iexc", "bridge current"],
        "name": "Excitation current",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["temp_controller", "lockin"],
    },
    "pulse_generation": {
        "abbreviations": ["pulse gen", "delay gen", "DG"],
        "name": "Pulse generation",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["pulse_gen"],
    },
    "trigger": {
        "abbreviations": ["TRIG", "trigger"],
        "name": "Trigger",
        "ivi_class": "",
        "scpi": ":TRIG",
        "instrument_classes": ["pulse_gen", "scope", "counter"],
    },
    "reference_clock": {
        "abbreviations": ["10MHz", "ref clock", "REFCLK", "ext ref"],
        "name": "Reference clock",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["rf_siggen", "counter", "pulse_gen"],
    },
    "conductance": {
        "abbreviations": ["G", "siemens", "conductance"],
        "name": "Conductance",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["electrometer", "smu"],
    },
    "reactance": {
        "abbreviations": ["X", "reactance"],
        "name": "Reactance",
        "ivi_class": "IviLCR",
        "scpi": "",
        "instrument_classes": ["lcr"],
    },
    "susceptance": {
        "abbreviations": ["B", "susceptance"],
        "name": "Susceptance",
        "ivi_class": "IviLCR",
        "scpi": "",
        "instrument_classes": ["lcr"],
    },
    "dynamic_load": {
        "abbreviations": ["dynamic", "transient load", "CC+CV"],
        "name": "Dynamic load",
        "ivi_class": "",
        "scpi": "",
        "instrument_classes": ["eload"],
    },
}


CONDITION_KEY_META: dict[str, dict] = {
    "frequency": {
        "abbreviations": ["freq", "f", "Hz"],
        "name": "Signal frequency",
        "units": "Hz",
        "ivi_equivalent": "ac_min_freq/ac_max_freq",
        "instrument_classes": ["dmm", "scope", "counter", "fgen", "lcr", "rf"],
    },
    "temperature": {
        "abbreviations": ["temp", "T", "degC", "°C"],
        "name": "Operating temperature",
        "units": "°C",
        "ivi_equivalent": "",
        "instrument_classes": ["all"],
    },
    "humidity": {
        "abbreviations": ["RH", "humid"],
        "name": "Relative humidity",
        "units": "%RH",
        "ivi_equivalent": "",
        "instrument_classes": ["all"],
    },
    "calibration_interval": {
        "abbreviations": ["cal", "cal interval", "tcal"],
        "name": "Calibration interval",
        "units": "days",
        "ivi_equivalent": "",
        "instrument_classes": ["all"],
    },
    "nplc": {
        "abbreviations": ["NPLC", "PLC", "integration time"],
        "name": "Power line cycles",
        "units": "PLC",
        "ivi_equivalent": "aperture_time",
        "instrument_classes": ["dmm", "smu", "daq"],
    },
    "auto_zero": {
        "abbreviations": ["AZ", "autozero", "auto-zero"],
        "name": "Auto-zero mode",
        "units": "",
        "ivi_equivalent": "auto_zero",
        "instrument_classes": ["dmm"],
    },
    "coupling": {
        "abbreviations": ["AC/DC", "coupling mode"],
        "name": "Input coupling",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["scope", "dmm", "lcr"],
    },
    "impedance": {
        "abbreviations": ["Zin", "input impedance", "50ohm", "1Mohm"],
        "name": "Input impedance",
        "units": "Ω",
        "ivi_equivalent": "input_impedance",
        "instrument_classes": ["scope", "dmm", "counter"],
    },
    "sense_mode": {
        "abbreviations": ["2W", "4W", "local", "remote", "Kelvin sense"],
        "name": "Sense mode",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "smu", "dmm"],
    },
    "sample_rate": {
        "abbreviations": ["SR", "Sa/s", "sample rate", "sampling"],
        "name": "Sample rate",
        "units": "Sa/s",
        "ivi_equivalent": "",
        "instrument_classes": ["scope", "digitizer", "daq"],
    },
    "bandwidth": {
        "abbreviations": ["BW", "bandwidth", "3dB"],
        "name": "Measurement bandwidth",
        "units": "Hz",
        "ivi_equivalent": "",
        "instrument_classes": ["scope", "lockin", "spectrum_analyzer"],
    },
    "filter": {
        "abbreviations": ["FIL", "filter", "digital filter"],
        "name": "Filter setting",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["dmm", "scope", "lockin", "lcr"],
    },
    "gate_time": {
        "abbreviations": ["gate", "gate time", "tgate"],
        "name": "Gate time",
        "units": "s",
        "ivi_equivalent": "",
        "instrument_classes": ["counter", "lockin"],
    },
    "acquisition_mode": {
        "abbreviations": ["acq", "acquisition", "normal", "average", "peak detect", "hi-res"],
        "name": "Acquisition mode",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["scope", "digitizer"],
    },
    "time_constant": {
        "abbreviations": ["tau", "τ", "time constant", "TC"],
        "name": "Time constant",
        "units": "s",
        "ivi_equivalent": "",
        "instrument_classes": ["lockin", "temp_controller"],
    },
    "signal_level": {
        "abbreviations": ["level", "signal level", "test level"],
        "name": "Signal level",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["lcr", "rf"],
    },
    "crest_factor": {
        "abbreviations": ["CF", "crest factor", "peak/RMS"],
        "name": "Crest factor",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["dmm", "power_analyzer"],
    },
    "load": {
        "abbreviations": ["load", "Iload", "output load"],
        "name": "Output load",
        "units": "A",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "eload"],
    },
    "input_voltage": {
        "abbreviations": ["Vin", "input voltage", "line voltage", "mains"],
        "name": "Input voltage",
        "units": "V",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "ac_source"],
    },
    "voltage": {
        "abbreviations": ["V", "voltage", "operating voltage"],
        "name": "Operating voltage",
        "units": "V",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "smu", "eload"],
    },
    "current": {
        "abbreviations": ["I", "A", "current", "operating current"],
        "name": "Operating current",
        "units": "A",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "smu", "eload"],
    },
    "duty_cycle": {
        "abbreviations": ["DC", "duty", "duty cycle"],
        "name": "Duty cycle",
        "units": "%",
        "ivi_equivalent": "",
        "instrument_classes": ["fgen", "psu"],
    },
    "slew_rate": {
        "abbreviations": ["SR", "slew", "slew rate", "dV/dt"],
        "name": "Slew rate",
        "units": "V/s",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "fgen"],
    },
    "settling_time": {
        "abbreviations": ["ts", "settling", "settling time"],
        "name": "Settling time",
        "units": "s",
        "ivi_equivalent": "",
        "instrument_classes": ["psu", "smu", "temp_controller"],
    },
    "sensor": {
        "abbreviations": ["sensor", "probe type", "detector"],
        "name": "Sensor type",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["temp_controller", "optical_power_meter", "gaussmeter"],
    },
    "wavelength": {
        "abbreviations": ["WL", "lambda", "λ", "nm"],
        "name": "Optical wavelength",
        "units": "nm",
        "ivi_equivalent": "",
        "instrument_classes": ["optical_power_meter", "optical_wavelength_meter"],
    },
    "offset": {
        "abbreviations": ["offset", "DC offset", "freq offset"],
        "name": "Offset",
        "units": "",
        "ivi_equivalent": "",
        "instrument_classes": ["fgen", "spectrum_analyzer"],
    },
}


# =============================================================================
# Lookup result and reverse index
# =============================================================================


@dataclass
class LookupResult:
    """A candidate match from abbreviation lookup."""

    enum_value: str
    enum_type: str  # "function" or "condition"
    name: str
    instrument_classes: list[str] = field(default_factory=list)
    matched_on: str = ""  # which abbreviation or name matched


def _build_reverse_index() -> dict[str, list[LookupResult]]:
    """Build lowercase abbreviation → list of LookupResult."""
    index: dict[str, list[LookupResult]] = {}

    for value, meta in MEASUREMENT_FUNCTION_META.items():
        result = LookupResult(
            enum_value=value,
            enum_type="function",
            name=meta["name"],
            instrument_classes=meta.get("instrument_classes", []),
        )
        # Index the enum value itself
        key = value.lower()
        index.setdefault(key, []).append(LookupResult(
            **{**result.__dict__, "matched_on": value},
        ))
        # Index each abbreviation
        for abbr in meta.get("abbreviations", []):
            key = abbr.lower()
            index.setdefault(key, []).append(LookupResult(
                **{**result.__dict__, "matched_on": abbr},
            ))

    for value, meta in CONDITION_KEY_META.items():
        result = LookupResult(
            enum_value=value,
            enum_type="condition",
            name=meta["name"],
            instrument_classes=meta.get("instrument_classes", []),
        )
        key = value.lower()
        index.setdefault(key, []).append(LookupResult(
            **{**result.__dict__, "matched_on": value},
        ))
        for abbr in meta.get("abbreviations", []):
            key = abbr.lower()
            index.setdefault(key, []).append(LookupResult(
                **{**result.__dict__, "matched_on": abbr},
            ))

    return index


_REVERSE_INDEX = _build_reverse_index()


# =============================================================================
# Public API
# =============================================================================


def lookup_enum(term: str) -> list[LookupResult]:
    """Look up MeasurementFunction or ConditionKey by abbreviation/name.

    Returns all candidates — caller uses context (instrument class, domain)
    to disambiguate. Case-insensitive.

    Examples:
        lookup_enum("FRES")  → [LookupResult(enum_value="resistance_4w", ...)]
        lookup_enum("Q")     → [LookupResult("quality_factor",...), LookupResult("charge",...)]
        lookup_enum("DCV")   → [LookupResult(enum_value="dc_voltage", ...)]
    """
    return _REVERSE_INDEX.get(term.lower(), [])


def render_enum_reference() -> str:
    """Render full abbreviation table as markdown for AI skill prompts."""
    lines = ["# Enum Quick Reference\n"]

    lines.append("## MeasurementFunction Values\n")
    lines.append("| Enum Value | Name | Abbreviations | Instrument Classes |")
    lines.append("|---|---|---|---|")
    for value, meta in MEASUREMENT_FUNCTION_META.items():
        abbrs = ", ".join(meta.get("abbreviations", []))
        classes = ", ".join(meta.get("instrument_classes", []))
        lines.append(f"| `{value}` | {meta['name']} | {abbrs} | {classes} |")

    lines.append("\n## ConditionKey Values\n")
    lines.append("| Enum Value | Name | Abbreviations | Units | Instrument Classes |")
    lines.append("|---|---|---|---|---|")
    for value, meta in CONDITION_KEY_META.items():
        abbrs = ", ".join(meta.get("abbreviations", []))
        classes = ", ".join(meta.get("instrument_classes", []))
        units = meta.get("units", "")
        lines.append(f"| `{value}` | {meta['name']} | {abbrs} | {units} | {classes} |")

    return "\n".join(lines)
