"""Standard feature vocabulary for instrument capabilities.

These feature strings provide a standardized way to describe
instrument capabilities beyond basic domain and range specifications.
"""

# Features for input (measurement) capabilities
INPUT_FEATURES = {
    # Connection topology
    "2_wire",
    "4_wire",
    "guarded",
    "remote_sense",
    # Input characteristics
    "high_impedance",
    "differential",
    "isolated",
    # AC measurements
    "true_rms",
    # Processing
    "auto_range",
    "auto_zero",
    "null_offset",
    "averaging",
    # Triggering
    "edge_trigger",
    "external_trigger",
}

# Features for output (source) capabilities
OUTPUT_FEATURES = {
    # Protection
    "ovp",  # Over-voltage protection
    "ocp",  # Over-current protection
    "opp",  # Over-power protection
    # Topology
    "remote_sense",
    "parallel",
    "series",
    # Characteristics
    "bipolar",
    "4_quadrant",
    "low_noise",
    # Waveform
    "pulse",
    "sweep",
    "list_mode",
}

# Combined set of all valid features
ALL_FEATURES = INPUT_FEATURES | OUTPUT_FEATURES
