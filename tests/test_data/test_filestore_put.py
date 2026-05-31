"""FileStore.put() — type dispatch, URI shape, collision handling.

Covers DoD for build item 1a (FileStore put API + URI scheme):
- put() returns ``file://{session_id}/{filename}``
- type dispatch: Path / Waveform / bytes / BaseModel / ndarray / fallback
- filename includes vector_id_short prefix when vector_id passed
- collision handling: same name twice produces distinct URIs + files
- on-disk layout: ``{data_dir}/files/{date}/{session_id}/{filename}``

Per CLAUDE.md test conventions: uses ``resolve_data_dir()`` (canonical)
+ uuid4 session_ids for per-test isolation. FileStore writes don't spawn
daemons, so no daemon-budget concern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from pydantic import BaseModel

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files import FileStore
from litmus.data.models import Waveform

# --------------------------------------------------------------------- #
# helpers                                                               #
# --------------------------------------------------------------------- #


def _session_id() -> str:
    """Unique session_id per test (isolation by identifier)."""
    return f"test-{uuid4().hex[:12]}"


def _vector_id() -> str:
    """Unique vector_id per test."""
    return uuid4().hex


def _expected_session_dir(store: FileStore, session_id: str) -> Path:
    """Reproduce the FileStore's session-dir computation for assertions."""
    today = datetime.now(UTC).date().isoformat()
    return resolve_data_dir() / "files" / today / session_id


def _parse_uri(uri: str) -> tuple[str, str]:
    """Return (session_id, filename) for a ``file://`` URI."""
    assert uri.startswith("file://"), uri
    parts = uri[len("file://") :].split("/", 1)
    assert len(parts) == 2, uri
    return parts[0], parts[1]


@pytest.fixture
def store() -> FileStore:
    return FileStore()


# --------------------------------------------------------------------- #
# type dispatch                                                         #
# --------------------------------------------------------------------- #


def test_put_path_copies_with_suffix_preserved(store: FileStore, tmp_path: Path) -> None:
    """Path values are copied; original suffix is preserved on disk."""
    sid = _session_id()
    src = tmp_path / "dut.tdms"
    src.write_bytes(b"\x00\x01\x02fake-tdms-bytes")

    uri = store.put("dut_capture", src, session_id=sid)

    parsed_sid, filename = _parse_uri(uri)
    assert parsed_sid == sid
    assert filename.endswith(".tdms")
    landed = _expected_session_dir(store, sid) / filename
    assert landed.read_bytes() == b"\x00\x01\x02fake-tdms-bytes"


def test_put_path_unsuffixed_defaults_to_bin(store: FileStore, tmp_path: Path) -> None:
    """Path with no suffix → ``.bin`` (matches save_ref_to_dir behavior)."""
    sid = _session_id()
    src = tmp_path / "no_suffix"
    src.write_bytes(b"data")

    uri = store.put("blob", src, session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".bin")


def test_put_bytes_writes_bin(store: FileStore) -> None:
    sid = _session_id()
    uri = store.put("payload", b"\xde\xad\xbe\xef", session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".bin")
    assert (_expected_session_dir(store, sid) / filename).read_bytes() == b"\xde\xad\xbe\xef"


def test_put_waveform_writes_npz_with_t0_dt_attrs(store: FileStore) -> None:
    sid = _session_id()
    wf = Waveform(
        Y=[1.0, 2.0, 3.0, 4.0],
        t0=0.1,
        dt=1e-6,
        attrs={"units": "V", "channel": "scope.ch1"},
    )

    uri = store.put("scope.capture", wf, session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".npz")
    npz = np.load(_expected_session_dir(store, sid) / filename)
    assert list(npz["Y"]) == [1.0, 2.0, 3.0, 4.0]
    assert float(npz["t0"]) == pytest.approx(0.1)
    assert float(npz["dt"]) == pytest.approx(1e-6)
    # attrs get inlined as keys in the npz
    assert str(npz["units"]) == "V"
    assert str(npz["channel"]) == "scope.ch1"


def test_put_pydantic_model_writes_json(store: FileStore) -> None:
    sid = _session_id()

    class Capture(BaseModel):
        sensor: str
        value: float

    cap = Capture(sensor="thermistor", value=23.5)
    uri = store.put("ambient", cap, session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".json")
    roundtrip = Capture.model_validate_json(
        (_expected_session_dir(store, sid) / filename).read_text()
    )
    assert roundtrip == cap


def test_put_ndarray_writes_npy(store: FileStore) -> None:
    sid = _session_id()
    arr = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float64)

    uri = store.put("samples", arr, session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".npy")
    loaded = np.load(_expected_session_dir(store, sid) / filename)
    np.testing.assert_array_equal(loaded, arr)


class _CustomPickleType:
    """Module-level (picklable) custom type for the pickle-fallback test."""

    def __init__(self, x: int = 0, y: str = "") -> None:
        self.x = x
        self.y = y

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _CustomPickleType) and self.x == other.x and self.y == other.y


def test_put_unrecognized_value_falls_back_to_pickle(store: FileStore) -> None:
    """Anything the dispatch doesn't recognize lands as ``.pkl``.

    Future build item 12 promotes save_ref_to_dir to a registry and
    emits a ``RuntimeWarning`` here naming the type; for 1a we just
    verify the bytes round-trip.
    """
    import pickle

    sid = _session_id()
    val = _CustomPickleType(42, "hello")

    uri = store.put("custom", val, session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename.endswith(".pkl")
    with open(_expected_session_dir(store, sid) / filename, "rb") as f:
        loaded = pickle.load(f)
    assert loaded == val


# --------------------------------------------------------------------- #
# URI shape + filename conventions                                      #
# --------------------------------------------------------------------- #


def test_uri_shape_session_id_then_filename(store: FileStore) -> None:
    """URI is ``file://{session_id}/{filename}`` — Option A logical reference."""
    sid = _session_id()
    uri = store.put("name", b"x", session_id=sid)

    assert uri.startswith(f"file://{sid}/")
    after_sid = uri[len(f"file://{sid}/") :]
    assert "/" not in after_sid, f"filename should not contain slash: {after_sid!r}"


def test_vector_id_prefix_in_filename(store: FileStore) -> None:
    """When ``vector_id`` is passed, first 8 chars prefix the filename."""
    sid = _session_id()
    vid = _vector_id()

    uri = store.put("scope.capture", b"x", session_id=sid, vector_id=vid)

    _, filename = _parse_uri(uri)
    assert filename.startswith(f"{vid[:8]}_scope.capture")
    assert filename.endswith(".bin")


def test_no_vector_id_means_no_prefix(store: FileStore) -> None:
    """Without ``vector_id``, the filename is just ``{name}.{ext}``."""
    sid = _session_id()
    uri = store.put("scope.capture", b"x", session_id=sid)

    _, filename = _parse_uri(uri)
    assert filename == "scope.capture.bin"


# --------------------------------------------------------------------- #
# collision handling                                                    #
# --------------------------------------------------------------------- #


def test_repeated_put_same_name_creates_distinct_uris(store: FileStore) -> None:
    """Two puts with the same name → two distinct URIs + files.

    Preserves claim-check immutability: a put never silently
    overwrites an existing artifact's bytes.
    """
    sid = _session_id()

    uri_a = store.put("scope.capture", b"first", session_id=sid)
    uri_b = store.put("scope.capture", b"second", session_id=sid)

    assert uri_a != uri_b
    _, fname_a = _parse_uri(uri_a)
    _, fname_b = _parse_uri(uri_b)
    assert fname_a == "scope.capture.bin"
    assert fname_b == "scope.capture_2.bin"

    session_dir = _expected_session_dir(store, sid)
    assert (session_dir / fname_a).read_bytes() == b"first"
    assert (session_dir / fname_b).read_bytes() == b"second"


def test_three_collisions_use_sequential_suffixes(store: FileStore) -> None:
    sid = _session_id()
    uri_a = store.put("dup", b"a", session_id=sid)
    uri_b = store.put("dup", b"b", session_id=sid)
    uri_c = store.put("dup", b"c", session_id=sid)

    _, fname_a = _parse_uri(uri_a)
    _, fname_b = _parse_uri(uri_b)
    _, fname_c = _parse_uri(uri_c)
    assert fname_a == "dup.bin"
    assert fname_b == "dup_2.bin"
    assert fname_c == "dup_3.bin"


def test_collision_under_vector_prefix(store: FileStore) -> None:
    """Collision suffix applies after the full ``{vector}_{name}`` stem."""
    sid = _session_id()
    vid = _vector_id()

    uri_a = store.put("capture", b"a", session_id=sid, vector_id=vid)
    uri_b = store.put("capture", b"b", session_id=sid, vector_id=vid)

    _, fname_a = _parse_uri(uri_a)
    _, fname_b = _parse_uri(uri_b)
    assert fname_a == f"{vid[:8]}_capture.bin"
    assert fname_b == f"{vid[:8]}_capture_2.bin"


# --------------------------------------------------------------------- #
# on-disk layout                                                        #
# --------------------------------------------------------------------- #


def test_on_disk_layout_is_files_date_session(store: FileStore) -> None:
    """Files land at ``{data_dir}/files/{date}/{session_id}/{filename}``."""
    sid = _session_id()
    today = datetime.now(UTC).date().isoformat()

    uri = store.put("x", b"y", session_id=sid)

    _, filename = _parse_uri(uri)
    expected_path = resolve_data_dir() / "files" / today / sid / filename
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"y"


def test_two_sessions_isolate_in_separate_subdirs(store: FileStore) -> None:
    """Different sessions write into different subdirs (no name conflict)."""
    sid_a = _session_id()
    sid_b = _session_id()

    store.put("shared_name", b"from_a", session_id=sid_a)
    store.put("shared_name", b"from_b", session_id=sid_b)

    today = datetime.now(UTC).date().isoformat()
    base = resolve_data_dir() / "files" / today
    assert (base / sid_a / "shared_name.bin").read_bytes() == b"from_a"
    assert (base / sid_b / "shared_name.bin").read_bytes() == b"from_b"


# --------------------------------------------------------------------- #
# attributes parameter (forward-compat; not persisted yet)              #
# --------------------------------------------------------------------- #


def test_attributes_argument_accepted_but_not_persisted_in_1a(store: FileStore) -> None:
    """``attributes`` is accepted today but not persisted — item 1c wires it.

    This test pins the forward-compatible signature so 1c can change
    only the persistence behavior, not the API.
    """
    sid = _session_id()

    uri = store.put(
        "x",
        b"y",
        session_id=sid,
        attributes={"mime": "application/octet-stream", "size": 1},
    )

    # URI shape unchanged; bytes land; attributes are not (yet) on disk
    _, filename = _parse_uri(uri)
    session_dir = _expected_session_dir(store, sid)
    assert (session_dir / filename).exists()
    # No sidecar metadata file yet (lands in 1c).
    assert not (session_dir / f"{filename}.meta.json").exists()
