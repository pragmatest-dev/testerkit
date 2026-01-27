"""Tests for capability feature vocabulary."""

from litmus.capabilities import INPUT_FEATURES, OUTPUT_FEATURES
from litmus.capabilities.features import ALL_FEATURES


class TestInputFeatures:
    """Tests for INPUT_FEATURES vocabulary."""

    def test_connection_topology_features(self):
        assert "2_wire" in INPUT_FEATURES
        assert "4_wire" in INPUT_FEATURES
        assert "guarded" in INPUT_FEATURES
        assert "remote_sense" in INPUT_FEATURES

    def test_input_characteristics(self):
        assert "high_impedance" in INPUT_FEATURES
        assert "differential" in INPUT_FEATURES
        assert "isolated" in INPUT_FEATURES

    def test_ac_measurement_features(self):
        assert "true_rms" in INPUT_FEATURES

    def test_processing_features(self):
        assert "auto_range" in INPUT_FEATURES
        assert "auto_zero" in INPUT_FEATURES
        assert "null_offset" in INPUT_FEATURES
        assert "averaging" in INPUT_FEATURES

    def test_triggering_features(self):
        assert "edge_trigger" in INPUT_FEATURES
        assert "external_trigger" in INPUT_FEATURES


class TestOutputFeatures:
    """Tests for OUTPUT_FEATURES vocabulary."""

    def test_protection_features(self):
        assert "ovp" in OUTPUT_FEATURES
        assert "ocp" in OUTPUT_FEATURES
        assert "opp" in OUTPUT_FEATURES

    def test_topology_features(self):
        assert "remote_sense" in OUTPUT_FEATURES
        assert "parallel" in OUTPUT_FEATURES
        assert "series" in OUTPUT_FEATURES

    def test_characteristics(self):
        assert "bipolar" in OUTPUT_FEATURES
        assert "4_quadrant" in OUTPUT_FEATURES
        assert "low_noise" in OUTPUT_FEATURES

    def test_waveform_features(self):
        assert "pulse" in OUTPUT_FEATURES
        assert "sweep" in OUTPUT_FEATURES
        assert "list_mode" in OUTPUT_FEATURES


class TestAllFeatures:
    """Tests for combined feature set."""

    def test_all_features_is_union(self):
        assert ALL_FEATURES == INPUT_FEATURES | OUTPUT_FEATURES

    def test_shared_feature_remote_sense(self):
        # remote_sense is in both input and output
        assert "remote_sense" in INPUT_FEATURES
        assert "remote_sense" in OUTPUT_FEATURES
        assert "remote_sense" in ALL_FEATURES
