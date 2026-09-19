"""Channels derived-index versioning parity with runs/events (#64).

Brings the channels warm index onto the same content-addressed-epoch scheme
runs/events already use (#53 P1, #64), reusing the shared, store-agnostic
``testerkit.data._index_epoch`` primitives. See
``docs/_internal/explorations/derived-index-versioning.md`` §3/§6 and
mirrors ``test_events_index_epoch.py``, scoped to channels' actual API: the
index is opened via ``ChannelStore``/``ChannelIndex`` (no bare daemon
function), so the fork-on-schema-change scenario writes real segments
through a producer ``ChannelStore`` and reads them back through an indexed
one — exactly the path that used to crash/misbehave against a fixed
``_index.duckdb`` name.

These exercise the index at the store layer directly (no daemon process, no
Flight, no threads) so they're fast and pid-cheap.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from testerkit.data import _index_epoch as index_epoch
from testerkit.data import schema_dispatch, schema_versions
from testerkit.data.channels import index as channel_index_module
from testerkit.data.channels.index import ChannelIndex
from testerkit.data.channels.store import ChannelStore
from testerkit.data.schema_versions import SchemaStore

# ── _projection_fingerprint determinism + widening ───────────────────


def test_fingerprint_is_stable_across_calls() -> None:
    fp1 = channel_index_module._projection_fingerprint()
    fp2 = channel_index_module._projection_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_when_columns_change(monkeypatch: pytest.MonkeyPatch) -> None:
    before = channel_index_module._projection_fingerprint()
    patched = (*ChannelIndex._CHANNEL_INDEX_COLUMNS, ("_fp_probe_col", "VARCHAR"))
    monkeypatch.setattr(ChannelIndex, "_CHANNEL_INDEX_COLUMNS", patched)
    after = channel_index_module._projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_adapter_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    before = channel_index_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.CHANNELS], "0.0-fp-probe", lambda rows: rows
    )
    after = channel_index_module._projection_fingerprint()
    assert after != before


def test_fingerprint_changes_when_whitelist_grows(monkeypatch: pytest.MonkeyPatch) -> None:
    before = channel_index_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_versions.KNOWN_SCHEMA_VERSIONS,
        SchemaStore.CHANNELS,
        schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.CHANNELS] | {"0.0-fp-probe"},
    )
    after = channel_index_module._projection_fingerprint()
    assert after != before


def test_fingerprint_unaffected_by_unrelated_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registering an adapter for a DIFFERENT store (e.g. runs) must not
    change the channels fingerprint."""
    before = channel_index_module._projection_fingerprint()
    monkeypatch.setitem(
        schema_dispatch._ADAPTERS[SchemaStore.RUNS], "0.0-fp-probe", lambda rows: rows
    )
    after = channel_index_module._projection_fingerprint()
    assert after == before


# ── content-addressed filename parity with runs/events ────────────────


def test_index_file_name_matches_current_fingerprint(tmp_path: Path) -> None:
    fp = channel_index_module._projection_fingerprint()
    idx = tmp_path / index_epoch.index_file_name(fp)
    assert idx.name == f"_index.{fp[:12]}.duckdb"


def test_open_creates_fingerprinted_file_not_fixed_name(tmp_path: Path) -> None:
    """The daemon/store no longer opens a fixed ``_index.duckdb`` — it opens
    the content-addressed ``_index.<fp>.duckdb`` (runs/events parity)."""
    producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
    producer.open()
    producer.write("dmm.dc_voltage", 1.0)
    producer.close()

    store = ChannelStore(tmp_path, uuid4(), index=True)
    store.open()
    store.close()

    channels_dir = tmp_path / "channels"
    assert not (channels_dir / "_index.duckdb").exists()
    fp = channel_index_module._projection_fingerprint()
    assert (channels_dir / index_epoch.index_file_name(fp)).exists()


# ── epochs ledger ───────────────────────────────────────────────────


def test_open_stamps_epochs_ledger(tmp_path: Path) -> None:
    store = ChannelStore(tmp_path, uuid4(), index=True)
    store.open()
    store.close()

    channels_dir = tmp_path / "channels"
    fp = channel_index_module._projection_fingerprint()
    ledger = json.loads((channels_dir / "_epochs.json").read_text())
    assert fp[:12] in ledger
    assert "last_seen" in ledger[fp[:12]]


# ── fork-on-schema-change (the scenario that crashed a fixed filename) ──


def test_fork_on_schema_change_coexists_and_rebuilds_from_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the crash a single fixed ``_index.duckdb`` produced: a
    schema/read-path change used to force either an in-place mutation of the
    one shared file or a hard crash on an incompatible reopen. With the
    epoch mechanism, a NEW fingerprint forks a NEW file — the old one is left
    completely untouched — and the new file rebuilds cleanly from the
    durable ``.arrow`` segments, never raising.
    """
    producer = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
    producer.open()
    producer.write("dmm.dc_voltage", 1.0)
    producer.write("dmm.dc_voltage", 2.0)
    producer.close()

    channels_dir = tmp_path / "channels"

    # Build the index at the CURRENT fingerprint.
    ix1 = ChannelStore(tmp_path, uuid4(), index=True)
    ix1.open()
    assert ix1.query("dmm.dc_voltage").num_rows == 2
    ix1.close()

    fp1 = channel_index_module._projection_fingerprint()
    idx1 = channels_dir / index_epoch.index_file_name(fp1)
    assert idx1.exists()

    # Simulate a schema/read-path change: a different fingerprint.
    fake_fp = "f" * 64
    monkeypatch.setattr(channel_index_module, "_projection_fingerprint", lambda: fake_fp)

    ix2 = ChannelStore(tmp_path, uuid4(), index=True)
    ix2.open()  # must NOT raise
    try:
        result = ix2.query("dmm.dc_voltage")
    finally:
        ix2.close()

    idx2 = channels_dir / index_epoch.index_file_name(fake_fp)
    assert idx2.exists()
    assert idx2 != idx1
    assert idx1.exists(), "the original fingerprinted file must be left untouched"
    assert result.num_rows == 2, "the new epoch rebuilds from the durable segments"
    assert {p.name for p in channels_dir.glob("_index.*.duckdb")} == {idx1.name, idx2.name}

    # The original epoch's data is intact — reopening it (fingerprint
    # restored) serves the same rows, unaffected by the new epoch's birth.
    monkeypatch.undo()
    ix3 = ChannelStore(tmp_path, uuid4(), index=True)
    ix3.open()
    try:
        assert ix3.query("dmm.dc_voltage").num_rows == 2
    finally:
        ix3.close()
