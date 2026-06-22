"""Context.observe blob routing — item 3a (half of the image-drop fix).

Pre-3a: ``Context.observe(key, blob)`` silently stashed the raw blob in
``self._observations[key]``; bytes were lost on crash; only the at-RunEnded
materializer ``_ref`` path picked them up (and only when materialization
actually ran).

After 3a: blobs route through ``FileStore.write`` immediately; the URI is
stashed instead. Bytes survive crashes; consumers see a ``file://`` URI
in ``_observations``.

Per CLAUDE.md test conventions: uses ``resolve_data_dir()`` (canonical)
+ uuid4 session_ids for per-test isolation.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files import _reset_for_tests, get_filestore
from litmus.execution.harness import Context, TestHarness

# --------------------------------------------------------------------- #
# fixtures + helpers                                                    #
# --------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _reset_filestore_singleton() -> None:
    """Each test starts with a fresh ``get_filestore()`` resolution."""
    _reset_for_tests()


@pytest.fixture
def context_with_session() -> Context:
    """A Context that has a session_id — the production shape."""
    harness = TestHarness(session_id=uuid4())
    return Context(harness=harness)


@pytest.fixture
def context_without_session() -> Context:
    """A Context with no session_id — the legacy shape that pre-dated 3a."""
    return Context()


def _resolve_uri(uri: str) -> Path:
    """Resolve a ``file://{date}/{session}/{filename}`` URI to its on-disk path.

    The URI now carries the full backend-relative key, so the local path is
    just ``{data_dir}/files/{key}`` — no date-partition scan needed.
    """
    assert uri.startswith("file://"), uri
    return resolve_data_dir() / "files" / uri[len("file://") :]


# --------------------------------------------------------------------- #
# blob routing                                                          #
# --------------------------------------------------------------------- #


def test_observe_bytes_routes_to_filestore_and_stashes_uri(
    context_with_session: Context,
) -> None:
    ctx = context_with_session
    ctx.observe("payload", b"\xde\xad\xbe\xef")

    uri = ctx._observations["payload"]
    assert uri.startswith("file://"), f"expected file:// URI, got {uri!r}"
    assert _resolve_uri(uri).read_bytes() == b"\xde\xad\xbe\xef"


def test_observe_path_routes_to_filestore_with_suffix_preserved(
    context_with_session: Context, tmp_path: Path
) -> None:
    src = tmp_path / "uut_capture.tdms"
    src.write_bytes(b"fake-tdms-bytes")

    ctx = context_with_session
    ctx.observe("uut_capture", src)

    uri = ctx._observations["uut_capture"]
    assert uri.startswith("file://"), uri
    landed = _resolve_uri(uri)
    assert landed.suffix == ".tdms"
    assert landed.read_bytes() == b"fake-tdms-bytes"


def test_observe_pydantic_model_routes_to_filestore_as_json(
    context_with_session: Context,
) -> None:
    class Capture(BaseModel):
        sensor: str
        value: float

    cap = Capture(sensor="thermistor", value=23.5)
    ctx = context_with_session
    ctx.observe("ambient", cap)

    uri = ctx._observations["ambient"]
    assert uri.startswith("file://"), uri
    landed = _resolve_uri(uri)
    assert landed.suffix == ".json"
    roundtrip = Capture.model_validate_json(landed.read_text())
    assert roundtrip == cap


# --------------------------------------------------------------------- #
# regression: existing scalar / numeric_array branches unchanged        #
# --------------------------------------------------------------------- #


def test_observe_scalar_still_stashes_inline(context_with_session: Context) -> None:
    """Scalar values continue to land in ``_observations`` as-is."""
    ctx = context_with_session
    ctx.observe("temp", 23.5)
    ctx.observe("operator", "ALICE")
    ctx.observe("fault", False)

    assert ctx._observations["temp"] == 23.5
    assert ctx._observations["operator"] == "ALICE"
    assert ctx._observations["fault"] is False


def test_observe_none_value_stashes_none(context_with_session: Context) -> None:
    """Observing None continues to stash None (no FileStore call)."""
    ctx = context_with_session
    ctx.observe("nothing", None)
    assert ctx._observations["nothing"] is None


# --------------------------------------------------------------------- #
# session_id requirement for blob path                                  #
# --------------------------------------------------------------------- #


def test_observe_blob_without_session_id_raises(
    context_without_session: Context,
) -> None:
    """Blob observation without session_id raises a clear error.

    Production paths always plumb session_id (via TestHarness or
    parent Context); this guards against silent breakage if a test
    misses the plumbing.
    """
    ctx = context_without_session
    with pytest.raises(RuntimeError, match="session_id"):
        ctx.observe("blob", b"\xde\xad\xbe\xef")


def test_observe_scalar_without_session_id_still_works(
    context_without_session: Context,
) -> None:
    """Only the blob path requires session_id; scalars are unaffected."""
    ctx = context_without_session
    ctx.observe("temp", 23.5)
    assert ctx._observations["temp"] == 23.5


# --------------------------------------------------------------------- #
# session_id propagation through Context construction                   #
# --------------------------------------------------------------------- #


def test_context_inherits_session_id_from_harness() -> None:
    """Context constructed with a harness pulls session_id from it."""
    sid = uuid4()
    harness = TestHarness(session_id=sid)
    ctx = Context(harness=harness)
    assert ctx._session_id == sid


def test_child_context_inherits_session_id_from_parent() -> None:
    """``parent.child()`` propagates session_id to the child."""
    sid = uuid4()
    parent = Context(harness=TestHarness(session_id=sid))
    child = parent.child()
    assert child._session_id == sid


def test_explicit_session_id_overrides_harness() -> None:
    """Explicit session_id arg wins over harness's session_id."""
    harness_sid = uuid4()
    explicit_sid = uuid4()
    harness = TestHarness(session_id=harness_sid)
    ctx = Context(harness=harness, session_id=explicit_sid)
    assert ctx._session_id == explicit_sid


# --------------------------------------------------------------------- #
# get_filestore() singleton behavior                                    #
# --------------------------------------------------------------------- #


def test_get_filestore_returns_same_singleton() -> None:
    """Repeat calls return the same FileStore instance."""
    a = get_filestore()
    b = get_filestore()
    assert a is b


def test_reset_for_tests_forces_new_singleton() -> None:
    """``_reset_for_tests()`` discards the cached instance."""
    a = get_filestore()
    _reset_for_tests()
    b = get_filestore()
    assert a is not b
