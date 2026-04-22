"""Unit tests for ``litmus.execution.expand``."""

from __future__ import annotations

import pytest

from litmus.execution.expand import expand
from litmus.execution.vectors import Vector


def _as_params(vecs: list[Vector]) -> list[dict]:
    return [v.params() for v in vecs]


def test_list_passthrough() -> None:
    rows = [{"vin": 5.0}, {"vin": 3.3}]
    result = expand({"list": rows})
    assert _as_params(result) == rows
    assert [v["_index"] for v in result] == [0, 1]


def test_product_expansion_cartesian() -> None:
    result = expand({"product": {"vin": [4.5, 5.0], "load": [0.1, 0.8]}})
    assert _as_params(result) == [
        {"vin": 4.5, "load": 0.1},
        {"vin": 4.5, "load": 0.8},
        {"vin": 5.0, "load": 0.1},
        {"vin": 5.0, "load": 0.8},
    ]


def test_zip_expansion_lockstep() -> None:
    result = expand({"zip": {"vin": [3.3, 5.0], "expected": [3.2, 4.9]}})
    assert _as_params(result) == [
        {"vin": 3.3, "expected": 3.2},
        {"vin": 5.0, "expected": 4.9},
    ]


def test_empty_block_yields_single_vector() -> None:
    result = expand({})
    assert len(result) == 1
    assert result[0].params() == {}


def test_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="one of"):
        expand({"sweep": {"vin": [1]}})


def test_rejects_multiple_keys() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        expand({"list": [{"a": 1}], "product": {"b": [2]}})


def test_list_must_be_list() -> None:
    with pytest.raises(ValueError, match="'list' expansion"):
        expand({"list": {"vin": 5.0}})  # type: ignore[dict-item]


def test_product_must_be_mapping() -> None:
    with pytest.raises(ValueError, match="'product' expansion"):
        expand({"product": [1, 2, 3]})  # type: ignore[dict-item]


def test_zip_unequal_lengths_errors() -> None:
    with pytest.raises(ValueError, match="equal-length"):
        expand({"zip": {"a": [1, 2], "b": [3]}})


def test_range_string_expands_inside_product() -> None:
    # Reuses litmus.utils.ranges via expand_vectors — smoke test the passthrough.
    result = expand({"product": {"vin": "1:3:1"}})
    assert _as_params(result) == [{"vin": 1}, {"vin": 2}, {"vin": 3}]
