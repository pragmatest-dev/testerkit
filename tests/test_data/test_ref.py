"""Tests for the unified reference protocol (litmus.data.ref)."""

from __future__ import annotations

from pathlib import Path

import pytest

from litmus.data.ref import (
    classify_value,
    is_ref,
    make_channel_uri,
    parse_channel_uri,
    ref_scheme,
)


class TestClassifyValue:
    def test_scalars(self):
        assert classify_value(42) == "scalar"
        assert classify_value(3.14) == "scalar"
        assert classify_value("hello") == "scalar"
        assert classify_value(True) == "scalar"
        assert classify_value(None) == "scalar"

    def test_flat_numeric_list(self):
        assert classify_value([1.0, 2.0, 3.0]) == "numeric_array"

    def test_waveform_tuple(self):
        # ([samples], dt)
        assert classify_value(([1.0, 2.0], 1e-5)) == "numeric_array"

    def test_numpy_array(self):
        try:
            import numpy as np
            assert classify_value(np.array([1, 2, 3])) == "numeric_array"
        except ImportError:
            pytest.skip("numpy not installed")

    def test_dict_is_channel(self):
        assert classify_value({"key": "val"}) == "channel"
        assert classify_value({"channels": [[1.0], [2.0]], "dt": 1e-6}) == "channel"

    def test_blob_types(self):
        assert classify_value(b"binary data") == "blob"
        assert classify_value(Path("/tmp/img.png")) == "blob"

    def test_empty_list(self):
        # Empty list has no first element to check
        assert classify_value([]) == "blob"

    def test_string_list(self):
        # List of strings is not numeric
        assert classify_value(["a", "b"]) == "blob"


class TestChannelUri:
    def test_make_and_parse_roundtrip(self):
        uri = make_channel_uri("scope.ch1.waveform", "abc123")
        assert uri.startswith("channel://")
        channel_id, session_id = parse_channel_uri(uri)
        assert channel_id == "scope.ch1.waveform"
        assert session_id == "abc123"

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError, match="Not a channel URI"):
            parse_channel_uri("file://something")

    def test_parse_no_query(self):
        channel_id, session_id = parse_channel_uri("channel://foo.bar")
        assert channel_id == "foo.bar"
        assert session_id == ""


class TestIsRef:
    def test_channel_uri(self):
        assert is_ref("channel://scope.ch1?session=abc") is True

    def test_file_uri(self):
        assert is_ref("file://_ref/wave.npz") is True

    def test_legacy_ref(self):
        assert is_ref("_ref/wave.npz") is False

    def test_plain_scalar(self):
        assert is_ref("hello") is False

    def test_non_string(self):
        assert is_ref(42) is False


class TestRefScheme:
    def test_channel(self):
        assert ref_scheme("channel://scope.ch1?session=abc") == "channel"

    def test_file(self):
        assert ref_scheme("file://_ref/wave.npz") == "file"

    def test_s3(self):
        assert ref_scheme("s3://bucket/key") == "s3"
