"""Build item 1c — FileStore attributes + MIME typing persistence.

Every :meth:`FileStore.write` writes a sidecar
``{filename}.meta.json`` next to the artifact carrying:

- ``mime``: from the registered serializer (item 13 convention table)
- ``extension``: actual on-disk extension (Path puts preserve source
  suffix, so .extension can differ from the serializer's default)
- ``size_bytes``: real on-disk size after the write
- ``attributes``: caller-supplied bag (``put(..., attributes={...})``)

Format-specific extraction (image dimensions, audio/video duration)
is deferred — initial cut captures the four fields above.

Read back via :meth:`FileStore.read_attributes(uri)`. URIs are
logical references (``file://{session_id}/{filename}`` — date is
absent), so the store walks date directories to resolve.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from pydantic import BaseModel

from litmus.data.files import FileArtifactMetadata, FileStore
from litmus.data.models import Waveform


def _session_id() -> str:
    return f"test-{uuid4().hex[:12]}"


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(data_dir=tmp_path)


# --------------------------------------------------------------------- #
# Sidecar shape                                                         #
# --------------------------------------------------------------------- #


class TestSidecarShape:
    def test_sidecar_written_next_to_artifact(self, store: FileStore) -> None:
        sid = _session_id()
        uri = store.write("x", b"abc", session_id=sid)

        # URI shape: file://{sid}/{filename}
        filename = uri[len(f"file://{sid}/") :]
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        session_dir = store._files_dir / today / sid

        assert (session_dir / filename).exists()
        assert (session_dir / f"{filename}.meta.json").exists()

    def test_sidecar_is_valid_json_matching_FileArtifactMetadata(self, store: FileStore) -> None:
        sid = _session_id()
        uri = store.write("x", b"abc", session_id=sid)
        metadata = store.read_attributes(uri)

        assert isinstance(metadata, FileArtifactMetadata)
        assert metadata.mime == "application/octet-stream"
        assert metadata.extension == ".bin"
        assert metadata.size_bytes == 3
        assert metadata.attributes == {}


# --------------------------------------------------------------------- #
# MIME convention — round-trip the §13 table per type                    #
# --------------------------------------------------------------------- #


class TestMIMETableRoundTrip:
    def test_bytes_lands_as_octet_stream(self, store: FileStore) -> None:
        sid = _session_id()
        uri = store.write("x", b"abc", session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/octet-stream"
        assert meta.extension == ".bin"

    def test_basemodel_lands_as_json(self, store: FileStore) -> None:
        class Cap(BaseModel):
            a: int

        sid = _session_id()
        uri = store.write("c", Cap(a=7), session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/json"
        assert meta.extension == ".json"

    def test_waveform_lands_as_npz(self, store: FileStore) -> None:
        sid = _session_id()
        wf = Waveform(t0=0.0, dt=1e-6, Y=[1.0, 2.0, 3.0])
        uri = store.write("scope.cap", wf, session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/x-numpy-npz"
        assert meta.extension == ".npz"

    def test_ndarray_lands_as_npy(self, store: FileStore) -> None:
        sid = _session_id()
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        uri = store.write("samples", arr, session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.mime == "application/x-numpy-npy"
        assert meta.extension == ".npy"

    def test_path_with_tdms_suffix_preserves_extension(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        sid = _session_id()
        src = tmp_path / "capture.tdms"
        src.write_bytes(b"\x00fake-tdms")

        uri = store.write("daq", src, session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        # MIME comes from the registry default (Path → octet-stream) —
        # the caller knows the real MIME and can override via attributes
        assert meta.mime == "application/octet-stream"
        # Extension follows the source's suffix, not the registry default
        assert meta.extension == ".tdms"


# --------------------------------------------------------------------- #
# size_bytes — real on-disk size                                         #
# --------------------------------------------------------------------- #


class TestSizeBytes:
    def test_size_reflects_actual_bytes_for_bytes_value(self, store: FileStore) -> None:
        sid = _session_id()
        payload = b"\x00" * 1024
        uri = store.write("blob", payload, session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.size_bytes == 1024

    def test_size_reflects_post_serialization_size(self, store: FileStore) -> None:
        """Pydantic model → .json: size is the JSON byte count, not the
        Python object's memory footprint."""

        class M(BaseModel):
            value: int

        sid = _session_id()
        uri = store.write("m", M(value=42), session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        # The on-disk content is the JSON string ``{"value":42}``
        # (12 bytes). Pin exact value so a future serializer change
        # surfaces here loudly.
        assert meta.size_bytes == len(b'{"value":42}')


# --------------------------------------------------------------------- #
# attributes round-trip                                                  #
# --------------------------------------------------------------------- #


class TestAttributesRoundTrip:
    def test_user_attributes_round_trip(self, store: FileStore) -> None:
        sid = _session_id()
        attrs = {
            "camera": "scope-a",
            "scale_v_div": 0.5,
            "trigger_source": "ch1",
            "notes": "warm-up complete",
        }
        uri = store.write("scope.ch1.cap", b"data", session_id=sid, attributes=attrs)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.attributes == attrs

    def test_attributes_none_yields_empty_dict(self, store: FileStore) -> None:
        sid = _session_id()
        uri = store.write("x", b"d", session_id=sid, attributes=None)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.attributes == {}

    def test_attributes_omitted_yields_empty_dict(self, store: FileStore) -> None:
        sid = _session_id()
        uri = store.write("x", b"d", session_id=sid)
        meta = store.read_attributes(uri)
        assert meta is not None
        assert meta.attributes == {}


# --------------------------------------------------------------------- #
# read_attributes resolution                                             #
# --------------------------------------------------------------------- #


class TestReadAttributes:
    def test_read_attributes_returns_none_for_unknown_uri(self, store: FileStore) -> None:
        sid = _session_id()
        # Put something so the session dir exists, then read a different URI
        store.write("x", b"d", session_id=sid)
        meta = store.read_attributes(f"file://{sid}/does-not-exist.bin")
        assert meta is None

    def test_read_attributes_returns_none_for_non_file_uri(self, store: FileStore) -> None:
        assert store.read_attributes("channel://x?session=abc") is None
        assert store.read_attributes("not-a-uri") is None
        assert store.read_attributes("") is None

    def test_read_attributes_returns_none_when_sidecar_missing(self, store: FileStore) -> None:
        """Pre-1c artifacts (no sidecar) yield None — backwards-readable."""
        sid = _session_id()
        uri = store.write("x", b"d", session_id=sid)
        # Manually delete the sidecar to simulate a pre-1c artifact
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        session_dir = store._files_dir / today / sid
        filename = uri[len(f"file://{sid}/") :]
        (session_dir / f"{filename}.meta.json").unlink()

        assert store.read_attributes(uri) is None

    def test_read_attributes_refuses_to_resolve_a_sidecar_uri(self, store: FileStore) -> None:
        """``read_attributes`` doesn't treat a sidecar as its own artifact."""
        sid = _session_id()
        uri = store.write("x", b"d", session_id=sid)
        # Try to read the sidecar's "URI" — should resolve to None
        sidecar_uri = uri + ".meta.json"
        assert store.read_attributes(sidecar_uri) is None


# --------------------------------------------------------------------- #
# Collision behavior + sidecar pairing                                   #
# --------------------------------------------------------------------- #


class TestCollisionPairsSidecar:
    def test_collision_creates_paired_artifact_and_sidecar(self, store: FileStore) -> None:
        sid = _session_id()
        uri_a = store.write("dup", b"a", session_id=sid)
        uri_b = store.write("dup", b"b", session_id=sid)

        assert uri_a != uri_b
        # Both artifacts have sidecars; metadata reads back independently
        meta_a = store.read_attributes(uri_a)
        meta_b = store.read_attributes(uri_b)
        assert meta_a is not None and meta_b is not None
        assert meta_a.size_bytes == 1
        assert meta_b.size_bytes == 1
        # Filenames are distinct (...dup.bin / ...dup_2.bin)
        assert uri_a.endswith("dup.bin")
        assert uri_b.endswith("dup_2.bin")
