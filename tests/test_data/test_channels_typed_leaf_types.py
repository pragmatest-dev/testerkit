"""Typed leaf-type support in ChannelStore — build item 14.

Pre-0.2: scalar ``int`` cast to ``float64`` (truncation hazard); array
element type hardcoded to ``pa.list_(pa.float64())`` regardless of
input (lossy for bool/int/str arrays; numpy dtypes erased).

After item 14: leaf types preserve through to the column dtype.

- scalar: bool / int / float / str all typed correctly
- array: list<bool>, list<int>, list<float>, list<str> all typed via
  first element OR numpy dtype
- ChannelDescriptor.data_type carries shape AND leaf type
  (``"scalar:int"`` / ``"array:bool"`` / etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from litmus.data.channels.models import (
    ChannelDescriptor,
    _data_type_for,
    _infer_field_type,
    _infer_schema,
)

# --------------------------------------------------------------------- #
# scalar leaf types                                                     #
# --------------------------------------------------------------------- #


def test_scalar_int_preserved_as_int64() -> None:
    """Pre-0.2 cast int → float64 (truncation hazard); now preserved."""
    assert _infer_field_type(42) == pa.int64()


def test_scalar_bool_preserved() -> None:
    assert _infer_field_type(True) == pa.bool_()


def test_scalar_bool_takes_precedence_over_int() -> None:
    """``True`` is also an ``int`` in Python; bool branch must come first."""
    # If int branch were checked first, True would become int64.
    assert _infer_field_type(True) == pa.bool_()
    assert _infer_field_type(False) == pa.bool_()


def test_scalar_float_preserved() -> None:
    assert _infer_field_type(3.14) == pa.float64()


def test_scalar_str_preserved() -> None:
    assert _infer_field_type("hello") == pa.utf8()


# --------------------------------------------------------------------- #
# array leaf types                                                      #
# --------------------------------------------------------------------- #


def test_array_bool_preserves_bool_leaf() -> None:
    """Digital waveform: list<bool> stays list<bool>, not list<float>."""
    assert _infer_field_type([True, False, True]) == pa.list_(pa.bool_())


def test_array_int_preserves_int_leaf() -> None:
    """Counter / error-code stream: list<int> stays list<int>."""
    assert _infer_field_type([1, 2, 3]) == pa.list_(pa.int64())


def test_array_float_unchanged() -> None:
    assert _infer_field_type([1.0, 2.0, 3.0]) == pa.list_(pa.float64())


def test_array_str_preserves_str_leaf() -> None:
    """Status / state stream: list<str> stays list<str>."""
    assert _infer_field_type(["IDLE", "RUNNING", "DONE"]) == pa.list_(pa.utf8())


def test_empty_array_defaults_to_float() -> None:
    """Empty list has no leaf to infer; default to float."""
    assert _infer_field_type([]) == pa.list_(pa.float64())


# --------------------------------------------------------------------- #
# numpy array leaf types                                                #
# --------------------------------------------------------------------- #


def test_numpy_bool_array_preserves_bool() -> None:
    np = pytest.importorskip("numpy")
    arr = np.array([True, False, True], dtype=np.bool_)
    assert _infer_field_type(arr) == pa.list_(pa.bool_())


def test_numpy_int_array_preserves_int() -> None:
    np = pytest.importorskip("numpy")
    arr = np.array([1, 2, 3], dtype=np.int32)
    inferred = _infer_field_type(arr)
    # pyarrow.from_numpy_dtype(np.int32) gives int32 — wrapping in list
    assert pa.types.is_list(inferred)
    assert pa.types.is_integer(inferred.value_type)


def test_numpy_float_array_preserves_float() -> None:
    np = pytest.importorskip("numpy")
    arr = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    assert _infer_field_type(arr) == pa.list_(pa.float64())


# --------------------------------------------------------------------- #
# _data_type_for — channel descriptor data_type string                  #
# --------------------------------------------------------------------- #


def test_data_type_for_scalar_int() -> None:
    assert _data_type_for(42) == "scalar:int"


def test_data_type_for_scalar_bool() -> None:
    assert _data_type_for(True) == "scalar:bool"


def test_data_type_for_scalar_float() -> None:
    assert _data_type_for(3.14) == "scalar:float"


def test_data_type_for_scalar_str() -> None:
    assert _data_type_for("ALICE") == "scalar:str"


def test_data_type_for_array_bool() -> None:
    """Digital waveform → ``array:bool``."""
    assert _data_type_for([True, False, True]) == "array:bool"


def test_data_type_for_array_int() -> None:
    assert _data_type_for([1, 2, 3]) == "array:int"


def test_data_type_for_array_float() -> None:
    assert _data_type_for([1.0, 2.0, 3.0]) == "array:float"


def test_data_type_for_array_str() -> None:
    assert _data_type_for(["IDLE", "RUNNING"]) == "array:str"


def test_data_type_for_numpy_bool_array() -> None:
    np = pytest.importorskip("numpy")
    arr = np.array([True, False], dtype=np.bool_)
    assert _data_type_for(arr) == "array:bool"


# --------------------------------------------------------------------- #
# _infer_schema — typed leaves flow into the per-channel Arrow schema   #
# --------------------------------------------------------------------- #


def test_schema_bool_array_samples_column_is_list_of_bool() -> None:
    """Critical: bool array round-trips as list<bool>, not [1.0, 0.0, 1.0]."""
    schema = _infer_schema([True, False, True])
    samples_field = schema.field("value")
    assert pa.types.is_list(samples_field.type)
    assert samples_field.type.value_type == pa.bool_()


def test_schema_int_array_samples_column_is_list_of_int() -> None:
    schema = _infer_schema([1, 2, 3])
    samples_field = schema.field("value")
    assert pa.types.is_list(samples_field.type)
    assert samples_field.type.value_type == pa.int64()


def test_schema_str_array_samples_column_is_list_of_str() -> None:
    schema = _infer_schema(["IDLE", "RUNNING"])
    samples_field = schema.field("value")
    assert pa.types.is_list(samples_field.type)
    assert samples_field.type.value_type == pa.utf8()


def test_schema_scalar_int_value_column_is_int64() -> None:
    """Pre-0.2 cast int → float64. After item 14: int64."""
    schema = _infer_schema(42)
    assert schema.field("value").type == pa.int64()


# --------------------------------------------------------------------- #
# build item 17 — ChannelDescriptor.attributes (was properties)         #
# --------------------------------------------------------------------- #


def test_channel_descriptor_field_renamed_to_attributes() -> None:
    """``properties`` → ``attributes`` per item 17 (no backcompat shim)."""
    d = ChannelDescriptor(channel_id="dmm.voltage")
    assert hasattr(d, "attributes")
    assert not hasattr(d, "properties")


def test_channel_descriptor_attributes_default_empty_dict() -> None:
    d = ChannelDescriptor(channel_id="x")
    assert d.attributes == {}


def test_channel_descriptor_attributes_settable() -> None:
    d = ChannelDescriptor(channel_id="x", attributes={"calibration": "2026-05-01"})
    assert d.attributes == {"calibration": "2026-05-01"}


def test_channel_descriptor_data_type_default_carries_leaf() -> None:
    """Default ``data_type`` is the typed form, not bare ``"scalar"``."""
    d = ChannelDescriptor(channel_id="x")
    assert d.data_type == "scalar:float"


# --------------------------------------------------------------------- #
# Waveform.attributes (was attrs)                                       #
# --------------------------------------------------------------------- #


def test_waveform_field_renamed_to_attributes() -> None:
    from litmus.data.models import Waveform

    wf = Waveform(t0=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC), dt=1e-6, Y=[1.0, 2.0])
    assert hasattr(wf, "attributes")
    assert not hasattr(wf, "attrs")


def test_waveform_attributes_settable_via_kwarg() -> None:
    from litmus.data.models import Waveform

    wf = Waveform(
        t0=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        dt=1e-6,
        Y=[1.0],
        attributes={"units": "V"},
    )
    assert wf.attributes == {"units": "V"}
