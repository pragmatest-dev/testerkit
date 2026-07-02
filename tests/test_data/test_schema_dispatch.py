"""Schema-version dispatch seam + the deferrable-vs-permanent refusal fix (#43).

Two layers:
- ``dispatch()`` classification: known → identity; a *newer* version → deferrable
  refusal; absent / older / unparseable → permanent refusal.
- runs ingest ledger behaviour: a current file ingests, an absent file is
  permanently quarantined, a *newer* file is DEFERRED (left un-ledgered) and then
  HEALS — a newer daemon that knows the version re-reads and ingests it, instead
  of the file being permanently invisible.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data import schema_versions
from litmus.data._runs_duckdb_daemon import _ensure_schema, _ingest_one_file
from litmus.data.schema_dispatch import SchemaVersionRefused, dispatch
from litmus.data.schema_versions import SchemaStore
from litmus.data.schemas import RUN_ROW_SCHEMA


def _write_run_parquet(path: Path, *, version: str | None) -> None:
    """Write a minimal single-run parquet stamped with *version* (None strips the
    stamp entirely, simulating a pre-1.0 / unstamped artifact)."""
    row: dict[str, object] = {f.name: None for f in RUN_ROW_SCHEMA}
    row["record_type"] = "run"
    row["run_id"] = "R-DISPATCH"
    row["session_id"] = "S-DISPATCH"
    row["run_started_at"] = datetime(2026, 7, 2, tzinfo=UTC)
    row["run_ended_at"] = datetime(2026, 7, 2, tzinfo=UTC)
    row["run_outcome"] = "passed"
    table = pa.table({k: [v] for k, v in row.items()}, schema=RUN_ROW_SCHEMA)
    metadata = {} if version is None else {b"schema_version": version.encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)


def _runs_count(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(*) FROM runs_materialized").fetchone()
    return row[0] if row else 0


def _ledger_status(conn: duckdb.DuckDBPyConnection, path: Path) -> list[tuple]:
    return conn.execute("SELECT status FROM _ingested WHERE path = ?", [str(path)]).fetchall()


class TestDispatchClassification:
    def test_known_version_returns_identity(self) -> None:
        adapter = dispatch(SchemaStore.RUNS, "1.0")
        sentinel = object()
        assert adapter(sentinel) is sentinel

    @pytest.mark.parametrize("version", ["2.0", "1.5", "10.0"])
    def test_newer_version_is_deferrable(self, version: str) -> None:
        with pytest.raises(SchemaVersionRefused) as excinfo:
            dispatch(SchemaStore.RUNS, version)
        assert excinfo.value.deferrable is True

    @pytest.mark.parametrize("version", [None, "0.5", "junk"])
    def test_absent_older_or_unparseable_is_permanent(self, version: str | None) -> None:
        with pytest.raises(SchemaVersionRefused) as excinfo:
            dispatch(SchemaStore.RUNS, version)
        assert excinfo.value.deferrable is False


class TestRunsDeferralAndHealing:
    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect()
        _ensure_schema(c)
        yield c
        c.close()

    def test_current_version_ingests(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "current.parquet"
        _write_run_parquet(p, version="1.0")
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 1
        assert _ledger_status(conn, p) == [("ok",)]

    def test_absent_stamp_is_permanently_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "absent.parquet"
        _write_run_parquet(p, version=None)
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        # Permanent: ledgered quarantined so it is not re-attempted (regenerate).
        assert _ledger_status(conn, p) == [("quarantined",)]

    def test_newer_stamp_is_deferred_not_quarantined(self, conn, tmp_path: Path) -> None:
        p = tmp_path / "future.parquet"
        _write_run_parquet(p, version="2.0")
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        # The crux of #43: a newer file is NOT ledgered, so it stays re-attemptable.
        assert _ledger_status(conn, p) == []

    def test_deferred_file_heals_on_a_newer_daemon(self, conn, tmp_path: Path, monkeypatch) -> None:
        p = tmp_path / "future.parquet"
        _write_run_parquet(p, version="2.0")

        # Older daemon: 2.0 is unknown → deferred, invisible, but not lost.
        _ingest_one_file(conn, p, os.stat(p))
        assert _runs_count(conn) == 0
        assert _ledger_status(conn, p) == []

        # A newer daemon knows 2.0 (identity adapter here). Same on-disk file.
        monkeypatch.setitem(
            schema_versions.KNOWN_SCHEMA_VERSIONS,
            SchemaStore.RUNS,
            schema_versions.KNOWN_SCHEMA_VERSIONS[SchemaStore.RUNS] | {"2.0"},
        )
        _ingest_one_file(conn, p, os.stat(p))
        # Healed — the previously-deferred file is now ingested.
        assert _runs_count(conn) == 1
        assert _ledger_status(conn, p) == [("ok",)]
