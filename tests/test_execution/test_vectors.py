"""Tests for vector expansion functions."""

import pytest

from litmus.execution.vectors import (
    Vector,
    expand_list,
    expand_nested,
    expand_product,
    expand_range,
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


class TestExpandList:
    """Tests for expand_list function."""

    def test_basic_list(self):
        result = expand_list([{"voltage": 3.3}, {"voltage": 5.0}, {"voltage": 12.0}])
        assert len(result) == 3
        assert result[0]["voltage"] == 3.3
        assert result[1]["voltage"] == 5.0
        assert result[2]["voltage"] == 12.0

    def test_indices_set(self):
        result = expand_list([{"a": 1}, {"a": 2}])
        assert result[0]["_index"] == 0
        assert result[1]["_index"] == 1

    def test_prev_chain(self):
        result = expand_list([{"a": 1}, {"a": 2}, {"a": 3}])
        assert "_prev" not in result[0] or result[0].get("_prev") is None
        assert result[1]["_prev"] is result[0]
        assert result[2]["_prev"] is result[1]

    def test_empty_list(self):
        result = expand_list([])
        assert result == []


class TestExpandProduct:
    """Tests for expand_product function (Cartesian product)."""

    def test_basic_product(self):
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

    def test_three_way_product(self):
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
        with pytest.raises(ValueError, match="same length"):
            expand_zip(a=[1, 2, 3], b=[1, 2])

    def test_single_param(self):
        result = expand_zip(voltage=[3.3, 5.0])
        assert len(result) == 2

    def test_empty_params(self):
        result = expand_zip()
        assert len(result) == 1


class TestExpandRange:
    """Tests for expand_range function."""

    def test_basic_range_step(self):
        result = expand_range("voltage", start=0.0, stop=5.0, step=1.0)
        assert len(result) == 6  # 0, 1, 2, 3, 4, 5
        assert result[0]["voltage"] == 0.0
        assert result[5]["voltage"] == 5.0

    def test_range_step_float(self):
        result = expand_range("voltage", start=0.0, stop=1.0, step=0.25)
        assert len(result) == 5  # 0, 0.25, 0.5, 0.75, 1.0
        assert result[2]["voltage"] == 0.5

    def test_range_count(self):
        result = expand_range("voltage", start=0.0, stop=10.0, count=5)
        assert len(result) == 5
        # Should be 0, 2.5, 5, 7.5, 10
        assert result[0]["voltage"] == 0.0
        assert result[2]["voltage"] == 5.0
        assert result[4]["voltage"] == 10.0

    def test_range_count_one(self):
        result = expand_range("voltage", start=5.0, stop=10.0, count=1)
        assert len(result) == 1
        assert result[0]["voltage"] == 5.0

    def test_range_requires_step_or_count(self):
        with pytest.raises(ValueError, match="step.*count"):
            expand_range("voltage", start=0.0, stop=5.0)

    def test_range_both_step_and_count_raises(self):
        with pytest.raises(ValueError, match="step.*count"):
            expand_range("voltage", start=0.0, stop=5.0, step=1.0, count=5)

    def test_indices_set(self):
        result = expand_range("x", start=0, stop=2, step=1)
        assert result[0]["_index"] == 0
        assert result[1]["_index"] == 1
        assert result[2]["_index"] == 2


class TestExpandNested:
    """Tests for expand_nested function."""

    def test_basic_nested(self):
        loops = [
            {"name": "temperature", "values": [-40, 25, 85]},
            {"name": "voltage", "values": [3.3, 5.0]},
        ]
        result = expand_nested(loops)
        assert len(result) == 6  # 3 x 2

        # First loop (temperature) is outer, changes slowest
        assert result[0]["temperature"] == -40
        assert result[0]["voltage"] == 3.3
        assert result[1]["temperature"] == -40
        assert result[1]["voltage"] == 5.0
        assert result[2]["temperature"] == 25
        assert result[2]["voltage"] == 3.3

    def test_nested_with_range(self):
        loops = [
            {"name": "temperature", "values": [25, 85]},
            {"name": "voltage", "range": {"start": 3.0, "stop": 3.2, "step": 0.1}},
        ]
        result = expand_nested(loops)
        assert len(result) == 6  # 2 x 3

    def test_nested_with_zipped_group(self):
        """Test zipped variables at same loop level."""
        loops = [
            {"name": "temperature", "values": [-40, 25, 85]},
            {
                "zip": [
                    {"name": "voltage", "values": [3.3, 5.0, 12.0]},
                    {"name": "expected", "values": [3.2, 4.9, 11.8]},
                ]
            },
        ]
        result = expand_nested(loops)
        assert len(result) == 9  # 3 x 3 (NOT 3 x 3 x 3)

        # Verify zipped pairing
        assert result[0]["voltage"] == 3.3
        assert result[0]["expected"] == 3.2
        assert result[1]["voltage"] == 5.0
        assert result[1]["expected"] == 4.9

    def test_zipped_mismatched_lengths_raises(self):
        loops = [
            {
                "zip": [
                    {"name": "a", "values": [1, 2, 3]},
                    {"name": "b", "values": [1, 2]},  # Different length
                ]
            },
        ]
        with pytest.raises(ValueError, match="same length"):
            expand_nested(loops)

    def test_empty_loops(self):
        result = expand_nested([])
        assert len(result) == 1
        assert result[0].params() == {}

    def test_changed_detection_outer_loop(self):
        """Verify .changed() correctly detects outer loop transitions."""
        loops = [
            {"name": "temperature", "values": [-40, 25]},
            {"name": "voltage", "values": [3.3, 5.0, 12.0]},
        ]
        result = expand_nested(loops)

        # First vector: everything is "changed"
        assert result[0].changed("temperature")
        assert result[0].changed("voltage")

        # Second vector: only voltage changed
        assert not result[1].changed("temperature")
        assert result[1].changed("voltage")

        # Fourth vector (first with temp=25): temperature changed
        assert result[3].changed("temperature")
        assert result[3].changed("voltage")


class TestExpandVectors:
    """Tests for expand_vectors function (config-based dispatch)."""

    def test_expand_product_mode(self):
        config = {
            "expand": "product",
            "voltage": [3.3, 5.0],
            "current": [0.1, 0.5],
        }
        result = expand_vectors(config)
        assert len(result) == 4

    def test_expand_zip_mode(self):
        config = {
            "expand": "zip",
            "voltage": [3.3, 5.0],
            "expected": [3.2, 4.9],
        }
        result = expand_vectors(config)
        assert len(result) == 2

    def test_expand_nested_mode(self):
        config = {
            "expand": "nested",
            "loops": [
                {"name": "temp", "values": [25, 85]},
                {"name": "volt", "values": [3.3, 5.0]},
            ],
        }
        result = expand_vectors(config)
        assert len(result) == 4

    def test_expand_list_input(self):
        config = [{"voltage": 3.3}, {"voltage": 5.0}]
        result = expand_vectors(config)
        assert len(result) == 2

    def test_empty_config(self):
        result = expand_vectors({})
        assert len(result) == 1

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown expansion mode"):
            expand_vectors({"expand": "invalid"})


class TestStringRangeSyntax:
    """Tests for string range syntax in vector expansion."""

    def test_product_with_string_range(self):
        """expand_product should expand string range values."""
        result = expand_product(voltage="3.0:3.2:0.1", load=[0.1, 0.5])
        assert len(result) == 6  # 3 voltages x 2 loads

        # Check voltage values are floats from range expansion
        voltages = sorted(set(v["voltage"] for v in result))
        assert voltages == [3.0, 3.1, 3.2]

    def test_zip_with_string_range(self):
        """expand_zip should expand string range values."""
        result = expand_zip(voltage="3.0:3.2:0.1", expected="2.9:3.1:0.1")
        assert len(result) == 3

        assert result[0]["voltage"] == 3.0
        assert result[0]["expected"] == 2.9

    def test_nested_with_string_range(self):
        """expand_nested should expand string range values."""
        loops = [
            {"name": "temperature", "values": "-40:85:25"},  # String range
            {"name": "voltage", "values": [3.3, 5.0]},  # Regular list
        ]
        result = expand_nested(loops)
        # -40, -15, 10, 35, 60, 85 = 6 temps x 2 voltages = 12
        assert len(result) == 12

        # Check temperature values
        temps = sorted(set(v["temperature"] for v in result))
        assert temps == [
            -40.0, -15.0, 10.0,
            35.0, 60.0, 85.0,
        ]

    def test_nested_zip_with_string_range(self):
        """Zipped groups should expand string range values."""
        loops = [
            {
                "zip": [
                    {"name": "voltage", "values": "3.3:5.3:1.0"},
                    {"name": "expected", "values": "3.2:5.2:1.0"},
                ]
            },
        ]
        result = expand_nested(loops)
        assert len(result) == 3  # 3.3, 4.3, 5.3

        assert result[0]["voltage"] == 3.3
        assert result[0]["expected"] == 3.2

    def test_expand_vectors_product_with_string_range(self):
        """expand_vectors with product mode should handle string ranges."""
        config = {
            "expand": "product",
            "temperature": "-40:85:125",  # -40, 85 (step 125 gives 2 values)
            "voltage": [3.3, 5.0],
        }
        result = expand_vectors(config)
        assert len(result) == 4  # 2 temps x 2 voltages

    def test_expand_vectors_nested_with_string_range(self):
        """expand_vectors with nested mode should handle string ranges."""
        config = {
            "expand": "nested",
            "loops": [
                {"name": "load", "values": "0.1:0.5:0.2"},  # 0.1, 0.3, 0.5
            ],
        }
        result = expand_vectors(config)
        assert len(result) == 3
