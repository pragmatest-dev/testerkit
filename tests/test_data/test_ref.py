"""Tests for the unified reference protocol (testerkit.data.ref)."""

from __future__ import annotations

from pathlib import Path

import pytest

from testerkit.data.ref import (
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
        import numpy as np

        assert classify_value(np.array([1, 2, 3])) == "numeric_array"

    def test_dict_is_channel(self):
        assert classify_value({"key": "val"}) == "channel"
        assert classify_value({"channels": [[1.0], [2.0]], "dt": 1e-6}) == "channel"

    def test_blob_types(self):
        assert classify_value(b"binary data") == "blob"
        assert classify_value(Path("/tmp/img.png")) == "blob"

    def test_empty_list(self):
        # Empty list has no first element to check
        assert classify_value([]) == "blob"

    def test_string_list_is_numeric_array(self):
        """Item 6 + C2: str arrays route to ChannelStore via the typed-leaf path.

        Pre-item-6 ``list[str]`` was classified as ``blob`` and routed
        to FileStore (as ``.pkl`` via pickle fallback). C2 added the
        typed-str-array schema support; item 6 loosens the classifier
        so the gate accepts. "numeric_array" remains the literal even
        though str isn't numeric — the name is API-stable; the
        meaning is "channel-shaped array" post-item-6.
        """
        assert classify_value(["a", "b"]) == "numeric_array"

    def test_bool_list_is_numeric_array(self):
        """Item 6: ``list[bool]`` reaches the typed-bool-array path."""
        assert classify_value([True, False, True]) == "numeric_array"


class TestChannelUri:
    def test_make_and_parse_roundtrip(self):
        uri = make_channel_uri("scope.ch1.waveform", "abc123")
        assert uri.startswith("channel://")
        ticket = parse_channel_uri(uri)
        assert ticket.channel_id == "scope.ch1.waveform"
        assert ticket.session_id == "abc123"
        assert ticket.sample_offset is None

    def test_make_and_parse_roundtrip_with_offset(self):
        uri = make_channel_uri("scope.ch1.waveform", "abc123", sample_offset=7)
        # session stays first so the runs-index session regex keeps working.
        assert uri == "channel://scope.ch1.waveform?session=abc123&sample_offset=7"
        ticket = parse_channel_uri(uri)
        assert ticket.channel_id == "scope.ch1.waveform"
        assert ticket.session_id == "abc123"
        assert ticket.sample_offset == 7

    def test_offset_zero_roundtrips(self):
        # sample_offset 0 is a real sample, not "absent" — must survive the round trip.
        ticket = parse_channel_uri(make_channel_uri("c", "s", sample_offset=0))
        assert ticket.sample_offset == 0

    def test_parse_invalid_raises(self):
        with pytest.raises(ValueError, match="Not a channel URI"):
            parse_channel_uri("file://something")

    def test_parse_no_query(self):
        ticket = parse_channel_uri("channel://foo.bar")
        assert ticket.channel_id == "foo.bar"
        assert ticket.session_id == ""
        assert ticket.sample_offset is None


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
