"""Build items 12 + 13 — serialization registry + MIME convention.

Item 12 promotes the previously-hardcoded type dispatch in
``FileStore._write`` and ``save_ref_to_dir`` to a single registry
in :mod:`litmus.data.files.serializers`. Adds:

- Built-in handlers for Path / Waveform / bytes / Pydantic BaseModel
  / numpy ndarray (priority-ordered)
- Opportunistic handlers for PIL.Image (PNG) and pandas.DataFrame
  (Parquet) — registered only when the library is importable
- ``register_serializer(predicate, ...)`` for custom types
- The ``litmus_serialize(dest) -> Path`` protocol — objects that
  know their own format can take precedence over the registry
- Pickle as last-resort fallback, emitting ``RuntimeWarning``
  naming the type so the codebase moves toward typed serializers

Item 13 establishes the MIME convention table for the built-in
types — exercised here so the convention is pinned by tests
rather than living only in prose.
"""

from __future__ import annotations

import pickle as _pickle
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from pydantic import BaseModel

from litmus.data.files import FileStore, register_serializer
from litmus.data.files.serializers import (
    PICKLE_FALLBACK,
    Serializer,
    _reset_registry_for_tests,
    find_serializer,
)
from litmus.data.models import Waveform


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset the registry between tests so custom registrations don't leak."""
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# --------------------------------------------------------------------- #
# Built-in handlers — types + MIME convention table (item 13)            #
# --------------------------------------------------------------------- #


class TestBuiltInHandlers:
    """Each built-in type resolves to its expected extension + MIME."""

    def test_path_serializer(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        s = find_serializer(src)
        # Per the convention table, Path's serializer default is
        # ``application/octet-stream``; callers (FileStore.put,
        # save_ref_to_dir) override the EXTENSION with the source's
        # suffix but the MIME stays the registry default.
        assert s.mime == "application/octet-stream"
        assert s.extension == ".bin"  # default; caller overrides for actual suffix

    def test_waveform_serializer_uses_npz_with_numpy(self) -> None:
        wf = Waveform(t0=datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC), dt=1e-6, Y=[1.0, 2.0])
        s = find_serializer(wf)
        assert s.extension == ".npz"
        assert s.mime == "application/x-numpy-npz"

    def test_bytes_serializer(self) -> None:
        s = find_serializer(b"\x00\x01")
        assert s.extension == ".bin"
        assert s.mime == "application/octet-stream"

    def test_basemodel_serializer(self) -> None:
        class Demo(BaseModel):
            x: int

        s = find_serializer(Demo(x=1))
        assert s.extension == ".json"
        assert s.mime == "application/json"

    def test_ndarray_serializer_with_numpy(self) -> None:
        arr = np.array([1.0, 2.0], dtype=np.float64)
        s = find_serializer(arr)
        assert s.extension == ".npy"
        assert s.mime == "application/x-numpy-npy"


# --------------------------------------------------------------------- #
# Opportunistic handlers — only if the library is importable             #
# --------------------------------------------------------------------- #


class TestOpportunisticHandlers:
    def test_pil_image_resolves_to_png(self) -> None:
        PIL = pytest.importorskip("PIL.Image")
        img = PIL.new("RGB", (4, 4), "red")
        s = find_serializer(img)
        assert s.extension == ".png"
        assert s.mime == "image/png"

    def test_pandas_dataframe_resolves_to_parquet(self) -> None:
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        s = find_serializer(df)
        assert s.extension == ".parquet"
        assert s.mime == "application/vnd.apache.parquet"

    def test_pil_image_writes_actual_png_bytes(self, tmp_path: Path) -> None:
        PIL = pytest.importorskip("PIL.Image")
        img = PIL.new("RGB", (8, 8), "blue")
        store = FileStore(data_dir=tmp_path)
        uri = store.write("preview", img, session_id="testsess")
        assert uri.endswith(".png")

        # Verify the file is actually a PNG (magic bytes)
        sid, _, filename = uri.partition("file://")[2].partition("/")
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        landed = tmp_path / "files" / today / sid / filename
        assert landed.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    def test_pandas_dataframe_writes_actual_parquet(self, tmp_path: Path) -> None:
        pd = pytest.importorskip("pandas")
        pq = pytest.importorskip("pyarrow.parquet")
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        store = FileStore(data_dir=tmp_path)
        uri = store.write("export", df, session_id="testsess2")
        assert uri.endswith(".parquet")

        sid, _, filename = uri.partition("file://")[2].partition("/")
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        landed = tmp_path / "files" / today / sid / filename
        round_trip = pq.read_table(landed).to_pandas()
        assert list(round_trip.columns) == ["a", "b"]
        assert list(round_trip["a"]) == [1, 2, 3]


# --------------------------------------------------------------------- #
# register_serializer — custom registration shadows built-ins            #
# --------------------------------------------------------------------- #


class _MyType:
    def __init__(self, payload: str) -> None:
        self.payload = payload


class TestRegisterSerializer:
    def test_registered_handler_resolves_custom_type(self) -> None:
        def write_my_type(value: _MyType, dest: Path) -> None:
            dest.write_text(f"MY:{value.payload}")

        register_serializer(
            _MyType,
            extension=".my",
            mime="application/x-my",
            write=write_my_type,
        )

        s = find_serializer(_MyType("hello"))
        assert s.extension == ".my"
        assert s.mime == "application/x-my"

    def test_user_registration_shadows_builtin(self, tmp_path: Path) -> None:
        """Re-registering ``bytes`` overrides the built-in ``.bin`` handler."""

        def write_as_b64(value: bytes, dest: Path) -> None:
            import base64

            dest.write_text(base64.b64encode(value).decode())

        register_serializer(
            bytes,
            extension=".b64",
            mime="text/plain",
            write=write_as_b64,
        )

        store = FileStore(data_dir=tmp_path)
        uri = store.write("payload", b"\x01\x02\x03", session_id="testsess3")
        assert uri.endswith(".b64")

    def test_predicate_registration_for_un_importable_type(self) -> None:
        """Callable predicates support types we can't import at load time."""
        sentinel = object()

        def predicate(value):
            return value is sentinel

        def write(value, dest):
            dest.write_text("sentinel")

        register_serializer(
            predicate,
            extension=".sentinel",
            mime="application/x-sentinel",
            write=write,
        )

        s = find_serializer(sentinel)
        assert s.extension == ".sentinel"
        # Non-sentinel falls through to other handlers
        s2 = find_serializer(b"x")
        assert s2.extension == ".bin"


# --------------------------------------------------------------------- #
# litmus_serialize protocol — objects that know their own format         #
# --------------------------------------------------------------------- #


class TestLitmusSerializeProtocol:
    def test_protocol_takes_precedence_over_registry(self, tmp_path: Path) -> None:
        """An object exposing ``litmus_serialize`` uses its own writer."""

        class MyArtifact:
            litmus_extension = ".myz"
            litmus_mime = "application/x-myz"

            def litmus_serialize(self, dest: Path) -> Path:
                dest.write_text("MYZ:custom-format")
                return dest

        store = FileStore(data_dir=tmp_path)
        uri = store.write("artifact", MyArtifact(), session_id="testsess4")
        assert uri.endswith(".myz")

        sid, _, filename = uri.partition("file://")[2].partition("/")
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        landed = tmp_path / "files" / today / sid / filename
        assert landed.read_text() == "MYZ:custom-format"

    def test_protocol_defaults_when_attributes_omitted(self) -> None:
        """Without ``litmus_extension`` / ``litmus_mime``, defaults apply."""

        class Bare:
            def litmus_serialize(self, dest: Path) -> Path:
                dest.write_bytes(b"x")
                return dest

        s = find_serializer(Bare())
        assert s.extension == ".bin"
        assert s.mime == "application/octet-stream"


# --------------------------------------------------------------------- #
# Pickle fallback — emits RuntimeWarning naming the type                 #
# --------------------------------------------------------------------- #


class _UnregisteredCustom:
    def __init__(self, x: int = 0) -> None:
        self.x = x

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _UnregisteredCustom) and self.x == other.x


class TestPickleFallback:
    def test_unregistered_type_emits_runtime_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            s = find_serializer(_UnregisteredCustom(42))

        assert s is PICKLE_FALLBACK
        runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert len(runtime_warnings) == 1
        assert "_UnregisteredCustom" in str(runtime_warnings[0].message)
        assert "register_serializer" in str(runtime_warnings[0].message)

    def test_pickle_fallback_round_trips(self, tmp_path: Path) -> None:
        store = FileStore(data_dir=tmp_path)
        val = _UnregisteredCustom(7)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            uri = store.write("custom", val, session_id="testsess5")

        assert uri.endswith(".pkl")
        sid, _, filename = uri.partition("file://")[2].partition("/")
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        landed = tmp_path / "files" / today / sid / filename
        with open(landed, "rb") as f:
            loaded = _pickle.load(f)
        assert loaded == val

    def test_pickle_fallback_mime(self) -> None:
        """Item 13: pickle convention is ``application/x-python-pickle``."""
        assert PICKLE_FALLBACK.mime == "application/x-python-pickle"
        assert PICKLE_FALLBACK.extension == ".pkl"


# --------------------------------------------------------------------- #
# save_ref_to_dir — same registry, different file layout                  #
# --------------------------------------------------------------------- #


class TestSaveRefToDirShareRegistry:
    """``save_ref_to_dir`` writes to the parquet's ``_ref/`` sibling
    but uses the same dispatch table as FileStore."""

    def test_custom_registration_affects_save_ref_to_dir(self, tmp_path: Path) -> None:
        from litmus.data.backends._row_helpers import save_ref_to_dir

        class _CustomFormat:
            pass

        def write_custom(value, dest):
            dest.write_text("CUSTOM")

        register_serializer(
            _CustomFormat,
            extension=".custom",
            mime="application/x-custom",
            write=write_custom,
        )

        uri = save_ref_to_dir(tmp_path, "vec123", "key", _CustomFormat())
        assert uri.endswith(".custom")
        files = list(tmp_path.glob("*.custom"))
        assert len(files) == 1
        assert files[0].read_text() == "CUSTOM"

    def test_save_ref_to_dir_pickle_warning_fires(self, tmp_path: Path) -> None:
        from litmus.data.backends._row_helpers import save_ref_to_dir

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            uri = save_ref_to_dir(tmp_path, "vec123", "key", _UnregisteredCustom(99))

        assert uri.endswith(".pkl")
        runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert len(runtime_warnings) == 1
        assert "_UnregisteredCustom" in str(runtime_warnings[0].message)


# --------------------------------------------------------------------- #
# Serializer NamedTuple — public API shape                                #
# --------------------------------------------------------------------- #


class TestSerializerType:
    def test_serializer_is_a_named_tuple(self) -> None:
        """``Serializer`` is the user-facing handler-shape; pin its fields."""
        s = Serializer(extension=".x", mime="text/plain", write=lambda v, d: None)
        assert s.extension == ".x"
        assert s.mime == "text/plain"
        # NamedTuple — positional unpack also works
        ext, mime, _ = s
        assert ext == ".x"
        assert mime == "text/plain"
