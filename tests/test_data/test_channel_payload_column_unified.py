"""C3a-pre — ChannelStore row payload column unified as ``value``.

Pre-rename: array channel rows used ``samples`` (plural) as the
payload column; scalar channel rows used ``value`` (singular). The
asymmetry was leaky — one channel write IS one sample (one row),
regardless of whether the row's payload is a scalar or an array.

After this rename:

- Every channel row's payload column is named ``value`` uniformly.
- Scalar channels: ``value`` is typed scalar.
- Array channels: ``value`` is typed ``list<leaf>`` + an extra
  ``sample_interval`` column for the inner-value spacing.
- Struct (dict) channels: per-key columns; ``value`` may or may not
  appear depending on the dict's keys.

``sample_interval`` stays as-is (T&M-native term for "inner-value
spacing within an array write"); only ``samples`` was renamed.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from litmus.data.channels.models import (
    ARRAY_SCHEMA,
    SCALAR_SCHEMA,
    _infer_schema,
)
from litmus.data.channels.store import ChannelStore


def _make_store(tmp_path: Path) -> ChannelStore:
    store = ChannelStore(tmp_path, uuid4(), flush_threshold=1000)
    store.open()
    return store


# --------------------------------------------------------------------- #
# Schema top-level: no more ``samples`` column                          #
# --------------------------------------------------------------------- #


class TestSchemaUnification:
    def test_scalar_schema_payload_column_is_value(self) -> None:
        assert "value" in SCALAR_SCHEMA.names
        assert "samples" not in SCALAR_SCHEMA.names

    def test_array_schema_payload_column_is_value_not_samples(self) -> None:
        """The rename: array rows lose the ``samples`` column name."""
        assert "value" in ARRAY_SCHEMA.names
        assert "samples" not in ARRAY_SCHEMA.names
        # ``sample_interval`` stays — it's the inner-value spacing,
        # not the payload itself.
        assert "sample_interval" in ARRAY_SCHEMA.names

    def test_array_schema_value_column_is_list_typed(self) -> None:
        value_field = ARRAY_SCHEMA.field("value")
        assert pa.types.is_list(value_field.type)


# --------------------------------------------------------------------- #
# _infer_schema: array shapes emit ``value`` (not ``samples``)          #
# --------------------------------------------------------------------- #


class TestInferSchemaUsesValue:
    def test_inferred_array_schema_has_value_column(self) -> None:
        schema = _infer_schema([1.0, 2.0, 3.0])
        assert "value" in schema.names
        assert "samples" not in schema.names

    def test_inferred_scalar_schema_has_value_column(self) -> None:
        schema = _infer_schema(3.31)
        assert "value" in schema.names
        # Scalar shape has no sample_interval (no inner spacing)
        assert "sample_interval" not in schema.names

    def test_inferred_array_schema_value_is_typed_list(self) -> None:
        schema = _infer_schema([True, False, True])
        assert pa.types.is_list(schema.field("value").type)
        assert schema.field("value").type.value_type == pa.bool_()

    def test_inferred_dict_schema_has_per_key_columns(self) -> None:
        """Dict (struct) channel: per-key columns, no ``value`` or ``samples``."""
        schema = _infer_schema({"a": 1.0, "b": 2.0})
        assert "a" in schema.names
        assert "b" in schema.names
        assert "value" not in schema.names
        assert "samples" not in schema.names


# --------------------------------------------------------------------- #
# End-to-end: write array → read back from ``value`` column             #
# --------------------------------------------------------------------- #


class TestArrayWritesLandInValueColumn:
    def test_write_list_lands_in_value_column(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.write("daq.channel1", [1.0, 2.0, 3.0])
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        table = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb")).read_all()
        assert "value" in table.schema.names
        assert "samples" not in table.schema.names
        assert table.column("value")[0].as_py() == [1.0, 2.0, 3.0]

    def test_write_waveform_tuple_lands_in_value_column(self, tmp_path: Path) -> None:
        """Legacy ``([samples], dt)`` tuple shape still works."""
        store = _make_store(tmp_path)
        store.write("scope.waveform", ([1.0, 2.0, 3.0], 1e-5))
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        table = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb")).read_all()
        assert table.column("value")[0].as_py() == [1.0, 2.0, 3.0]
        assert table.column("sample_interval")[0].as_py() == 1e-5

    def test_write_numpy_array_lands_in_value_column(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        arr = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
        store.write("daq.samples", arr)
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        table = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb")).read_all()
        assert table.column("value")[0].as_py() == [1.0, 2.0, 3.0, 4.0]

    def test_write_scalar_lands_in_value_column(self, tmp_path: Path) -> None:
        """Scalar writes also land in ``value`` (uniform across shapes)."""
        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.31)
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        table = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb")).read_all()
        assert table.column("value")[0].as_py() == 3.31

    def test_query_array_returns_value_column(self, tmp_path: Path) -> None:
        """``ChannelStore.query`` returns rows with ``value`` (not ``samples``)."""
        store = _make_store(tmp_path)
        store.write("scope.ch1", ([1.0, 2.0, 3.0], 1e-5))
        result = store.query("scope.ch1")
        assert "value" in result.schema.names
        assert "samples" not in result.schema.names
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]
        store.close()


# --------------------------------------------------------------------- #
# Subscription: ChannelSample.value carries the dict shape               #
# --------------------------------------------------------------------- #


class TestSubscriptionPayloadShape:
    def test_array_subscription_dict_uses_value_key(self, tmp_path: Path) -> None:
        """Live subscription's normalized payload dict uses ``value`` key."""
        from litmus.data.channels.models import ChannelSample

        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        store.on_channel("scope.ch1", received.append)

        store.write("scope.ch1", ([1.0, 2.0, 3.0], 1e-5))

        assert len(received) == 1
        assert received[0].value == {"value": [1.0, 2.0, 3.0], "sample_interval": 1e-5}
        store.close()


# --------------------------------------------------------------------- #
# sample_interval stays (T&M-native term for inner-value spacing)        #
# --------------------------------------------------------------------- #


class TestSampleIntervalUnchanged:
    def test_array_schema_keeps_sample_interval(self) -> None:
        assert "sample_interval" in ARRAY_SCHEMA.names

    def test_scalar_schema_has_no_sample_interval(self) -> None:
        """Scalars don't have an inner time axis; no spacing field."""
        assert "sample_interval" not in SCALAR_SCHEMA.names


# --------------------------------------------------------------------- #
# Defensive: rejecting the old ``samples`` column on read                #
# --------------------------------------------------------------------- #


class TestNoStaleSamplesColumnAnywhere:
    @pytest.mark.parametrize(
        # Rename ``value`` → ``payload`` to avoid the pytest plugin
        # stamping mixed-type cases into a single ``in_value`` column —
        # see test_materializer_auto_promotion.py::TestObservationKind
        # for the same fix and the underlying daemon-materialization
        # constraint.
        "payload",
        [
            3.31,
            [1.0, 2.0, 3.0],
            np.array([1, 2, 3], dtype=np.int32),
            {"a": 1.0, "b": "ok"},
        ],
    )
    def test_inferred_schema_never_contains_samples(self, payload) -> None:
        schema = _infer_schema(payload)
        assert "samples" not in schema.names, schema.names
