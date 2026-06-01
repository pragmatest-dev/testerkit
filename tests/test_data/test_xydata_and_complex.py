"""Build item 15 — XYData model + complex-array round-trip coverage.

Two parts in one cluster:

1. **XYData**: a small Pydantic model for paired x/y data
   (IV curves, eye diagrams, S-parameter sweeps). Registered with
   the serializer registry from C6-remainder (item 12), so
   ``observe(name, XYData(...))`` lands on disk as a single
   ``.npz`` archive instead of falling through to the generic
   BaseModel JSON handler.

2. **Complex arrays**: ``numpy.complex64`` / ``complex128`` ndarrays
   already round-trip via the generic ndarray serializer (numpy
   handles complex dtypes natively through ``np.save`` / ``np.load``).
   This test pins that coverage explicitly so a future serializer
   change can't silently regress it.

Per CLAUDE.md test conventions: uses ``tmp_path``-backed
FileStore (FileStore writes don't spawn daemons).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from litmus.data.files import FileStore
from litmus.data.files.serializers import find_serializer
from litmus.data.models import XYData


def _sid() -> str:
    return f"test-{uuid4().hex[:12]}"


def _session_dir(store: FileStore, sid: str) -> Path:
    today = datetime.now(UTC).date().isoformat()
    return store._files_dir / today / sid


def _filename_from_uri(uri: str, sid: str) -> str:
    return uri[len(f"file://{sid}/") :]


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(data_dir=tmp_path)


# --------------------------------------------------------------------- #
# XYData — model shape                                                  #
# --------------------------------------------------------------------- #


class TestXYDataModel:
    def test_required_fields(self) -> None:
        xy = XYData(x=[1.0, 2.0, 3.0], y=[4.0, 5.0, 6.0])
        assert xy.x == [1.0, 2.0, 3.0]
        assert xy.y == [4.0, 5.0, 6.0]
        assert xy.x_units is None
        assert xy.y_units is None
        assert xy.x_name is None
        assert xy.y_name is None

    def test_optional_units_and_names(self) -> None:
        xy = XYData(
            x=[0.0, 1.0, 2.0],
            y=[0.0, 4.0, 16.0],
            x_units="V",
            y_units="A",
            x_name="Bias voltage",
            y_name="Diode current",
        )
        assert xy.x_units == "V"
        assert xy.y_units == "A"
        assert xy.x_name == "Bias voltage"
        assert xy.y_name == "Diode current"


# --------------------------------------------------------------------- #
# XYData — registered serializer routes to .npz, not BaseModel .json    #
# --------------------------------------------------------------------- #


class TestXYDataSerializerRegistration:
    def test_resolves_to_npz_not_json(self) -> None:
        """The XYData predicate sits BEFORE the generic BaseModel
        handler in the registry; otherwise XYData would fall through
        and land as .json."""
        xy = XYData(x=[1.0], y=[1.0])
        s = find_serializer(xy)
        assert s.extension == ".npz"
        assert s.mime == "application/x-numpy-npz"

    def test_round_trips_required_fields(self, store: FileStore) -> None:
        sid = _sid()
        xy = XYData(x=[1.0, 2.0, 3.0], y=[10.0, 20.0, 30.0])

        uri = store.put("iv_curve", xy, session_id=sid)
        assert uri.endswith(".npz")

        filename = _filename_from_uri(uri, sid)
        archive = np.load(_session_dir(store, sid) / filename)
        np.testing.assert_array_equal(archive["x"], [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(archive["y"], [10.0, 20.0, 30.0])
        # No optional keys when not set
        assert "x_units" not in archive.files
        assert "y_units" not in archive.files
        assert "x_name" not in archive.files
        assert "y_name" not in archive.files

    def test_round_trips_optional_unit_and_name_keys(self, store: FileStore) -> None:
        sid = _sid()
        xy = XYData(
            x=[0.0, 1.0],
            y=[0.0, 4.0],
            x_units="V",
            y_units="A",
            x_name="Bias",
            y_name="Current",
        )

        uri = store.put("iv", xy, session_id=sid)
        filename = _filename_from_uri(uri, sid)
        archive = np.load(_session_dir(store, sid) / filename)

        assert str(archive["x_units"]) == "V"
        assert str(archive["y_units"]) == "A"
        assert str(archive["x_name"]) == "Bias"
        assert str(archive["y_name"]) == "Current"

    def test_sidecar_metadata_matches_npz_convention(self, store: FileStore) -> None:
        """Item 1c sidecar lands with the npz MIME convention from item 13."""
        sid = _sid()
        xy = XYData(x=[0.0, 1.0], y=[2.0, 3.0])
        uri = store.put("curve", xy, session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/x-numpy-npz"
        assert meta.extension == ".npz"


# --------------------------------------------------------------------- #
# Complex arrays — round-trip via the generic ndarray serializer        #
# --------------------------------------------------------------------- #


class TestComplexArrayRoundTrip:
    def test_complex128_round_trips(self, store: FileStore) -> None:
        """A ``numpy.complex128`` array — the canonical S-parameter
        / FFT shape — round-trips cleanly through the registry."""
        sid = _sid()
        arr = np.array(
            [1 + 2j, 3 - 4j, 5 + 0j, 0 - 6j],
            dtype=np.complex128,
        )

        uri = store.put("s11", arr, session_id=sid)
        filename = _filename_from_uri(uri, sid)
        loaded = np.load(_session_dir(store, sid) / filename)

        assert loaded.dtype == np.complex128
        np.testing.assert_array_equal(loaded, arr)

    def test_complex64_round_trips(self, store: FileStore) -> None:
        """``numpy.complex64`` (lower-precision IQ sample stream)
        round-trips with dtype preserved."""
        sid = _sid()
        arr = np.array(
            [1 + 2j, 3 - 4j, 5 + 0j],
            dtype=np.complex64,
        )

        uri = store.put("iq", arr, session_id=sid)
        filename = _filename_from_uri(uri, sid)
        loaded = np.load(_session_dir(store, sid) / filename)

        assert loaded.dtype == np.complex64
        np.testing.assert_array_equal(loaded, arr)

    def test_complex_array_lands_as_npy_not_pickle(self, store: FileStore) -> None:
        """Complex arrays use the ndarray serializer (npy), not the
        pickle fallback. Pin loudly so a future predicate-order tweak
        can't silently regress to pickle and lose dtype info."""
        sid = _sid()
        arr = np.array([1 + 1j, 2 - 2j], dtype=np.complex128)

        uri = store.put("complex_signal", arr, session_id=sid)
        assert uri.endswith(".npy")
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/x-numpy-npy"

    def test_complex_eye_diagram_via_two_real_arrays_as_xydata(self, store: FileStore) -> None:
        """Practical use: an eye diagram is two real arrays packaged
        as XYData (not a complex array). Pin the split-into-XYData
        workflow as the canonical Pattern B shape from §4."""
        sid = _sid()
        eye = XYData(
            x=[-1.0, -0.5, 0.0, 0.5, 1.0],
            y=[0.0, 0.5, 1.0, 0.5, 0.0],
            x_units="UI",
            y_units="V",
            x_name="time",
            y_name="amplitude",
        )

        uri = store.put("eye", eye, session_id=sid)
        filename = _filename_from_uri(uri, sid)
        archive = np.load(_session_dir(store, sid) / filename)

        np.testing.assert_array_equal(archive["x"], [-1.0, -0.5, 0.0, 0.5, 1.0])
        np.testing.assert_array_equal(archive["y"], [0.0, 0.5, 1.0, 0.5, 0.0])
        assert str(archive["x_units"]) == "UI"
