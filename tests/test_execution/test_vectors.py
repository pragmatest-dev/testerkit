"""Tests for vector expansion functions."""

import pytest

from testerkit.execution.vectors import (
    Vector,
    expand_product,
    expand_vectors,
    expand_zip,
)


class TestVector:
    """Tests for Vector class."""

    def test_basic_vector(self):
        v = Vector(voltage=3.3, current=0.1)
        assert v["voltage"] == 3.3
        assert v["current"] == 0.1

    def test_vector_from_dict(self):
        v = Vector({"voltage": 3.3, "current": 0.1})
        assert v["voltage"] == 3.3

    def test_changed_no_prev(self):
        """First vector should report all keys as changed."""
        v = Vector(voltage=3.3, _index=0)
        assert v.changed("voltage") is True
        assert v.changed("nonexistent") is True

    def test_changed_with_prev(self):
        """Check change detection against previous vector."""
        v1 = Vector(voltage=3.3, current=0.1, _index=0)
        v2 = Vector(voltage=3.3, current=0.5, _index=1, _prev=v1)

        assert v2.changed("voltage") is False  # Same value
        assert v2.changed("current") is True  # Different value

    def test_params_excludes_metadata(self):
        """params() should exclude _prefixed keys."""
        v = Vector(voltage=3.3, current=0.1, _index=0, _prev=None)
        params = v.params()
        assert params == {"voltage": 3.3, "current": 0.1}
        assert "_index" not in params
        assert "_prev" not in params


class TestExpandPart:
    """Tests for expand_product function (Cartesian product)."""

    def test_basic_part(self):
        result = expand_product(voltage=[3.3, 5.0], current=[0.1, 0.5])
        assert len(result) == 4  # 2 x 2

        # First param varies slowest (outer loop)
        assert result[0]["voltage"] == 3.3
        assert result[0]["current"] == 0.1
        assert result[1]["voltage"] == 3.3
        assert result[1]["current"] == 0.5
        assert result[2]["voltage"] == 5.0
        assert result[2]["current"] == 0.1
        assert result[3]["voltage"] == 5.0
        assert result[3]["current"] == 0.5

    def test_three_way_part(self):
        result = expand_product(a=[1, 2], b=[3, 4], c=[5, 6])
        assert len(result) == 8  # 2 x 2 x 2

    def test_single_param(self):
        result = expand_product(voltage=[3.3, 5.0, 12.0])
        assert len(result) == 3
        assert [v["voltage"] for v in result] == [3.3, 5.0, 12.0]

    def test_empty_params(self):
        result = expand_product()
        assert len(result) == 1
        assert result[0].params() == {}

    def test_indices_and_prev(self):
        result = expand_product(a=[1, 2], b=[3, 4])
        for i, v in enumerate(result):
            assert v["_index"] == i
        assert result[2]["_prev"] is result[1]

    def test_range_strings_in_part(self):
        """expand_product should expand string range values."""
        result = expand_product(voltage="3.0:3.2:0.1", load=[0.1, 0.5])
        assert len(result) == 6  # 3 voltages x 2 loads

        voltages = sorted(set(v["voltage"] for v in result))
        assert voltages == [3.0, 3.1, 3.2]


class TestExpandZip:
    """Tests for expand_zip function (parallel iteration)."""

    def test_basic_zip(self):
        result = expand_zip(voltage=[3.3, 5.0, 12.0], expected=[3.2, 4.9, 11.8])
        assert len(result) == 3

        assert result[0]["voltage"] == 3.3
        assert result[0]["expected"] == 3.2
        assert result[1]["voltage"] == 5.0
        assert result[1]["expected"] == 4.9
        assert result[2]["voltage"] == 12.0
        assert result[2]["expected"] == 11.8

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="equal-length"):
            expand_zip(a=[1, 2, 3], b=[1, 2])

    def test_single_param(self):
        result = expand_zip(voltage=[3.3, 5.0])
        assert len(result) == 2

    def test_empty_params(self):
        result = expand_zip()
        assert len(result) == 1

    def test_range_strings_in_zip(self):
        """expand_zip should expand string range values."""
        result = expand_zip(voltage="3.0:3.2:0.1", expected="2.9:3.1:0.1")
        assert len(result) == 3

        assert result[0]["voltage"] == 3.0
        assert result[0]["expected"] == 2.9


class TestExpandVectors:
    """Tests for expand_vectors function (config-based dispatch)."""

    def test_simple_part(self):
        config = {
            "expand": "product",
            "voltage": [3.3, 5.0],
            "current": [0.1, 0.5],
        }
        result = expand_vectors(config)
        assert len(result) == 4

    def test_simple_zip(self):
        config = {
            "expand": "zip",
            "voltage": [3.3, 5.0],
            "expected": [3.2, 4.9],
        }
        result = expand_vectors(config)
        assert len(result) == 2

    def test_range_strings_in_part(self):
        """Part mode should handle string ranges."""
        config = {
            "expand": "product",
            "temperature": "-40:85:125",  # -40, 85 (step 125 gives 2 values)
            "voltage": [3.3, 5.0],
        }
        result = expand_vectors(config)
        assert len(result) == 4  # 2 temps x 2 voltages

    def test_recursive_part_with_zip_sub_block(self):
        """Part with a vectors sub-block (zip inside)."""
        config = {
            "expand": "product",
            "temperature": [-40, 25, 85],
            "vectors": {
                "expand": "zip",
                "voltage": [3.3, 5.0],
                "expected": [3.2, 4.9],
            },
        }
        result = expand_vectors(config)
        assert len(result) == 6  # 3 temps x 2 zipped pairs

        # Check that all vectors have all three keys
        for v in result:
            assert "temperature" in v.params()
            assert "voltage" in v.params()
            assert "expected" in v.params()

        # First 2 vectors: temperature=-40, voltage/expected zipped
        assert result[0]["temperature"] == -40
        assert result[0]["voltage"] == 3.3
        assert result[0]["expected"] == 3.2
        assert result[1]["temperature"] == -40
        assert result[1]["voltage"] == 5.0
        assert result[1]["expected"] == 4.9

        # Next 2: temperature=25
        assert result[2]["temperature"] == 25
        assert result[2]["voltage"] == 3.3

    def test_single_vector_no_expand(self):
        """A dict without 'expand' key should produce a single vector."""
        config = {"voltage": 3.3, "current": 0.1}
        result = expand_vectors(config)
        assert len(result) == 1
        assert result[0]["voltage"] == 3.3
        assert result[0]["current"] == 0.1

    def test_explicit_list(self):
        config = [{"voltage": 3.3}, {"voltage": 5.0}]
        result = expand_vectors(config)
        assert len(result) == 2
        assert result[0]["voltage"] == 3.3
        assert result[1]["voltage"] == 5.0

    def test_empty_config(self):
        result = expand_vectors({})
        assert len(result) == 1

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown expansion mode"):
            expand_vectors({"expand": "invalid"})

    def test_unknown_mode_nested_raises(self):
        with pytest.raises(ValueError, match="Unknown expansion mode"):
            expand_vectors({"expand": "nested"})

    def test_indices_and_prev_chain(self):
        """All expansion modes should set _index and _prev."""
        result = expand_vectors(
            {
                "expand": "product",
                "a": [1, 2],
                "b": [3, 4],
            }
        )
        assert result[0]["_index"] == 0
        assert result[1]["_index"] == 1
        assert result[2]["_index"] == 2
        assert result[3]["_index"] == 3
        assert result[0].get("_prev") is None
        assert result[1]["_prev"] is result[0]
        assert result[3]["_prev"] is result[2]

    def test_changed_detection_in_part(self):
        """Verify .changed() correctly detects outer loop transitions in part."""
        result = expand_vectors(
            {
                "expand": "product",
                "temperature": [-40, 25],
                "voltage": [3.3, 5.0, 12.0],
            }
        )
        assert len(result) == 6

        # First vector: everything is "changed"
        assert result[0].changed("temperature")
        assert result[0].changed("voltage")

        # Second vector: only voltage changed
        assert not result[1].changed("temperature")
        assert result[1].changed("voltage")

        # Fourth vector (first with temp=25): temperature changed
        assert result[3].changed("temperature")
        assert result[3].changed("voltage")
