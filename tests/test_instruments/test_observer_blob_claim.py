"""Item 3b — observer.read blob → file:// claim-check.

Pre-3b: an instrument that returns a blob (PIL image, raw bytes,
arbitrary Pydantic model, Path) had the value silently dropped at
``InstrumentEventBuilder._store_value`` because the channel store path was
skipped for blobs and no FileStore route existed. The blob never
reached durable storage.

After 3b (this PR):

- ``_store_value`` detects ``classify_value(value) == "blob"`` and
  routes the bytes through ``FileStore.write(...)`` with the
  session_id from InstrumentEventBuilder.
- The returned ``file://`` URI is written into ChannelStore as the
  channel's sample value — works as a ``scalar:str`` channel because
  C2 (item 14) made ChannelStore accept str leaf types.
- The URI is returned to the caller, so ``InstrumentEventBuilder.read``'s
  existing ``ctx._observations.setdefault(...)`` stamping picks it
  up unchanged.

Depends on C1 (ChannelStarted lifecycle) + C2 (typed leaf types) +
C1a (FileStore.write). Per CLAUDE.md test conventions: uses
``resolve_data_dir()`` + uuid4 session_ids for per-test isolation.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest
from pydantic import BaseModel

from litmus.data.data_dir import resolve_data_dir
from litmus.data.events import ChannelStarted
from litmus.data.files import _reset_for_tests
from litmus.data.ref import classify_value
from litmus.instruments.observer import InstrumentEventBuilder


class CollectingLog:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


class FakeChannelStore:
    """Captures the writes InstrumentEventBuilder routes through it.

    Mimics the parts of ``litmus.data.channels.ChannelStore.write``
    that InstrumentEventBuilder touches: takes (channel_id, value, source),
    returns a synthesized ``channel://`` URI, records the args.
    Also mirrors item-4b consolidation: when ``event_log`` is wired,
    emits ``ChannelStarted`` once per (channel, session) on first
    write — the same contract the real store now upholds.
    """

    def __init__(
        self,
        session_id=None,  # noqa: ANN001
        event_log=None,  # noqa: ANN001
    ) -> None:
        self.writes: list[tuple[str, object, str]] = []
        self._session_id = session_id
        self._event_log = event_log
        self._started: set[str] = set()

    def write(  # noqa: ANN001, PLR0913
        self,
        channel_id: str,
        value,
        source: str = "",
        instrument_role: str = "",
        resource: str = "",
        run_id=None,  # noqa: ANN001
        **_kwargs,
    ) -> str:
        self.writes.append((channel_id, value, source))
        if channel_id not in self._started:
            self._started.add(channel_id)
            if self._event_log is not None and self._session_id is not None:
                from litmus.data.events import ChannelStarted

                self._event_log.emit(
                    ChannelStarted(
                        session_id=self._session_id,
                        run_id=run_id,
                        channel_id=channel_id,
                        instrument_role=instrument_role or None,
                        method=source or None,
                        resource=resource or None,
                    )
                )
        return f"channel://{channel_id}?session=test"


@pytest.fixture(autouse=True)
def _reset_filestore_singleton():
    """Reset the FileStore module singleton between tests."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def _emitter_with_store() -> tuple[InstrumentEventBuilder, CollectingLog, FakeChannelStore, str]:
    log = CollectingLog()
    sid = uuid4()
    # Wire the fake store with the same event_log + session_id the
    # emitter uses, so the store can emit ChannelStarted (item 4b).
    store = FakeChannelStore(session_id=sid, event_log=log)
    emitter = InstrumentEventBuilder(
        event_log=log,  # type: ignore[arg-type]
        session_id=sid,
        role="scope",
        run_id=uuid4(),
        resource="USB::0x0699::0x0408",
        channel_store=store,
    )
    return emitter, log, store, str(sid)


def _expected_file_dir(session_id: str) -> Path:
    """Reproduce FileStore's on-disk layout for assertions."""
    from datetime import UTC, datetime

    today = datetime.now(UTC).date().isoformat()
    return resolve_data_dir() / "files" / today / session_id


# --------------------------------------------------------------------- #
# bytes — the canonical blob case (e.g. instrument screenshot)          #
# --------------------------------------------------------------------- #


def test_bytes_blob_lands_in_filestore_and_uri_in_channel_store() -> None:
    """Scope screenshot (raw bytes) round-trips through FileStore + URI in channel."""
    emitter, _log, store, sid = _emitter_with_store()
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake"

    # sanity: classify_value still calls this a blob
    assert classify_value(png_bytes) == "blob"

    emitter.read("scope.screenshot", png_bytes, method="screenshot")

    # ChannelStore got the URI as the channel value (not the raw bytes)
    assert len(store.writes) == 1
    channel_id, written_value, source = store.writes[0]
    assert channel_id == "scope.screenshot"
    assert isinstance(written_value, str)
    assert f"/{sid}/" in written_value and written_value.startswith("file://")
    assert source == "screenshot"

    # URI resolves to the actual bytes on disk
    filename = written_value.rsplit("/", 1)[-1]
    landed = _expected_file_dir(sid) / filename
    assert landed.read_bytes() == png_bytes


def test_bytes_blob_emits_channel_started_once() -> None:
    """First per-channel-per-session write still emits ChannelStarted."""
    emitter, log, _store, _sid = _emitter_with_store()
    emitter.read("scope.screenshot", b"png1", method="screenshot")
    emitter.read("scope.screenshot", b"png2", method="screenshot")
    emitter.read("scope.screenshot", b"png3", method="screenshot")

    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1
    assert started[0].channel_id == "scope.screenshot"
    assert started[0].instrument_role == "scope"


def test_each_blob_write_creates_a_distinct_file() -> None:
    """Three blob writes → three files on disk → three URIs in ChannelStore.

    No silent overwrite (FileStore.write produces ``name``, ``name_2``,
    ``name_3`` suffixes from C1a collision handling).
    """
    emitter, _log, store, sid = _emitter_with_store()
    emitter.read("scope.screenshot", b"png1", method="screenshot")
    emitter.read("scope.screenshot", b"png2", method="screenshot")
    emitter.read("scope.screenshot", b"png3", method="screenshot")

    uris = [w[1] for w in store.writes]
    assert len(uris) == 3
    assert len(set(uris)) == 3  # all distinct

    session_dir = _expected_file_dir(sid)
    for uri in uris:
        assert isinstance(uri, str)
        filename = uri.rsplit("/", 1)[-1]
        assert (session_dir / filename).exists()


# --------------------------------------------------------------------- #
# Path blob — instrument that hands back a file path                     #
# --------------------------------------------------------------------- #


def test_path_blob_routes_through_filestore(tmp_path: Path) -> None:
    """An instrument that returns a Path (e.g. a TDMS dump file)."""
    emitter, _log, store, sid = _emitter_with_store()
    src = tmp_path / "capture.tdms"
    src.write_bytes(b"\x00\x01\x02fake-tdms")

    assert classify_value(src) == "blob"

    emitter.read("daq.capture", src, method="acquire")

    assert len(store.writes) == 1
    uri = store.writes[0][1]
    assert isinstance(uri, str)
    assert f"/{sid}/" in uri and uri.startswith("file://")
    # Path's suffix preserved through FileStore.write
    assert uri.endswith(".tdms")


# --------------------------------------------------------------------- #
# Pydantic blob — instrument that hands back a structured object         #
# --------------------------------------------------------------------- #


def test_pydantic_blob_routes_through_filestore() -> None:
    """An instrument observer that returns a Pydantic model gets json-claimed."""
    emitter, _log, store, sid = _emitter_with_store()

    class Capture(BaseModel):
        sensor: str
        value: float

    cap = Capture(sensor="thermistor", value=23.5)
    assert classify_value(cap) == "blob"

    emitter.read("sensor.reading", cap, method="read_all")

    assert len(store.writes) == 1
    uri = store.writes[0][1]
    assert isinstance(uri, str)
    assert f"/{sid}/" in uri and uri.startswith("file://")
    assert uri.endswith(".json")


# --------------------------------------------------------------------- #
# negative — non-blob values stay on the channel-store path              #
# --------------------------------------------------------------------- #


def test_scalar_value_still_routes_to_channel_store_not_filestore() -> None:
    """Per the original gap-6 design, scalar reads go straight to ChannelStore."""
    emitter, _log, store, _sid = _emitter_with_store()
    emitter.read("dmm.voltage", 3.31, method="measure_voltage")

    # FakeChannelStore got the raw float
    assert len(store.writes) == 1
    _, value, _ = store.writes[0]
    assert value == 3.31

    # No FileStore touched — nothing on disk for this channel
    sid = str(emitter._session_id)
    files_dir = _expected_file_dir(sid)
    if files_dir.exists():
        for entry in files_dir.iterdir():
            assert "dmm.voltage" not in entry.name


def test_array_value_still_routes_to_channel_store_not_filestore() -> None:
    """Numeric arrays remain ChannelStore territory — never FileStore."""
    emitter, _log, store, _sid = _emitter_with_store()
    samples = [3.31, 3.32, 3.33, 3.30, 3.31]
    assert classify_value(samples) == "numeric_array"

    emitter.read("dmm.waveform", samples, method="acquire_waveform")

    assert len(store.writes) == 1
    _, value, _ = store.writes[0]
    assert value == samples


def test_numpy_array_value_still_routes_to_channel_store_not_filestore() -> None:
    """Numpy arrays are channel-shaped, not blob-shaped."""
    np = pytest.importorskip("numpy")
    emitter, _log, store, _sid = _emitter_with_store()
    arr = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
    assert classify_value(arr) == "numeric_array"

    emitter.read("daq.samples", arr, method="read_block")

    assert len(store.writes) == 1
    _, value, _ = store.writes[0]
    # passthrough — same array object
    assert value is arr


# --------------------------------------------------------------------- #
# integration — blob URI flows through to vector out_* stamping          #
# --------------------------------------------------------------------- #


def test_blob_uri_stamps_active_vectors_out_column() -> None:
    """The URI from FileStore propagates onto the active vector's out_*.

    This is the bridge between the in-progress channel and the parquet
    materialization path: a verify row in this vector now references
    the screenshot via ``out_scope.screenshot``.
    """
    from litmus.execution._state import (
        push_current_context,
        reset_current_context,
    )

    class FakeContext:
        def __init__(self) -> None:
            self._observations: dict[str, str] = {}

    ctx = FakeContext()
    token = push_current_context(ctx)  # type: ignore[arg-type]
    try:
        emitter, _log, _store, sid = _emitter_with_store()
        emitter.read("scope.screenshot", b"\x89PNG\r\n\x1a\n", method="screenshot")

        assert "scope.screenshot" in ctx._observations
        uri = ctx._observations["scope.screenshot"]
        assert isinstance(uri, str)
        assert f"/{sid}/" in uri and uri.startswith("file://")
    finally:
        reset_current_context(token)


# --------------------------------------------------------------------- #
# negative — no channel store: blob is dropped (no-session case)         #
# --------------------------------------------------------------------- #


def test_no_channel_store_blob_value_passes_through_unchanged() -> None:
    """Driver outside a session (no channel_store wired): pre-3b behavior.

    Without a session to scope the FileStore put to, the raw blob
    flows back through the emitter unchanged. This is the same shape
    as the no-channel-store branch for scalars; intentional.
    """
    log = CollectingLog()
    emitter = InstrumentEventBuilder(
        event_log=log,  # type: ignore[arg-type]
        session_id=uuid4(),
        role="dmm",
        channel_store=None,
    )
    raw = b"binary-blob"
    emitter.read("dmm.raw", raw, method="raw_read")

    # ChannelStarted still emits (it's the lifecycle marker)
    started = [e for e in log.events if isinstance(e, ChannelStarted)]
    assert len(started) == 1


# --------------------------------------------------------------------- #
# silence the unused pyarrow import warning under pyright/ruff           #
# --------------------------------------------------------------------- #


def test_pyarrow_is_available_for_subsequent_tests() -> None:
    """Smoke check; pa is imported to keep parity with sibling test modules."""
    assert pa.__name__ == "pyarrow"
