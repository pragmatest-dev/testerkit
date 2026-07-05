"""Store-agnostic derived-index epoch primitives (#53 P1/P4/P5, extracted #64).

Shared spine for every DuckDB-backed derived index (runs today; events/channels/
files adopt this later). See ``docs/_internal/explorations/derived-index-versioning.md``
§3 (content-addressed filenames — "the filename is the gate, the in-file
``_index_meta`` is provenance") and §6 (the ``_epochs.json`` last-access ledger).

Everything here is deliberately ignorant of any one store's projection shape:
it takes fingerprints, provenance strings, and schema-building callables as
plain arguments rather than computing them. A store's daemon module (e.g.
``_runs_duckdb_daemon.py``) owns the store-SPECIFIC half — its own projection
fingerprint, its own ``_ensure_schema`` DDL — and calls into these primitives
with that state injected.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


def index_file_name(fingerprint: str) -> str:
    """The content-addressed index filename for a full 64-char *fingerprint*.

    A 12-char hex prefix is used in the name (birthday-safe past ~77k files —
    8 would already do); the full digest lives inside the file as provenance
    (:func:`stamp_index_meta`).
    """
    return f"_index.{fingerprint[:12]}.duckdb"


def read_index_meta(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Read the stored provenance as a dict; ``{}`` when ``_index_meta`` is
    absent (a pre-epoch index, or a build that never reached this table)."""
    try:
        rows = conn.execute("SELECT key, value FROM _index_meta").fetchall()
    except duckdb.Error:
        return {}
    return {str(k): str(v) for k, v in rows}


def stamp_index_meta(
    conn: duckdb.DuckDBPyConnection,
    *,
    litmus_version: str,
    schema_version: str,
    fingerprint: str,
) -> None:
    """Write this build's provenance into ``_index_meta`` and mark it complete.

    Kept OUT of any store's schema-DDL function (it is versioning metadata,
    not projection shape, so it must not feed its own fingerprint). The
    build-complete marker (``built_at``) is written in a separate, LAST
    statement — DuckDB autocommits each ``execute()`` — so a crash between
    the provenance insert and this one leaves ``built_at`` absent, which
    :func:`open_index` reads as an incomplete build on the next open.
    Idempotent upsert — safe to call on every (re)build.

    Provenance is taken as explicit arguments (never computed here) so this
    stays store-agnostic — each store's daemon supplies its own
    ``(litmus_version, schema_version, fingerprint)`` triple.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS _index_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    conn.execute(
        "INSERT INTO _index_meta (key, value) VALUES "
        "('litmus_version', ?), ('schema_version', ?), ('projection_fingerprint', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [litmus_version, schema_version, fingerprint],
    )
    conn.execute(
        "INSERT INTO _index_meta (key, value) VALUES ('built_at', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [datetime.now(UTC).isoformat()],
    )


def discard_index(index_path: Path) -> None:
    """Delete the derived index file and its WAL sidecar (rebuildable from parquet)."""
    index_path.unlink(missing_ok=True)
    Path(f"{index_path}.wal").unlink(missing_ok=True)


def index_file_is_the_cause(index_dir: Path) -> bool:
    """True when DuckDB can open a fresh database in ``index_dir``.

    Distinguishes a corrupt *index file* from an *environmental* fault
    (disk full, read-only mount, broken install): if a throwaway probe DB
    opens and writes fine in the same directory, DuckDB and the disk are
    healthy, so the failure is the index file itself — safe to discard and
    rebuild. If even the probe fails, the fault is environmental and we must
    NOT delete the derived index. Gates the self-heal per "only rebuild if
    the index is the problem."
    """
    probe = index_dir / f"._index_probe_{os.getpid()}.duckdb"
    try:
        c = duckdb.connect(str(probe))
        c.execute("CREATE TABLE _probe(x INTEGER)")
        c.close()
        return True
    except duckdb.Error:
        return False
    finally:
        probe.unlink(missing_ok=True)
        Path(f"{probe}.wal").unlink(missing_ok=True)


def reset_index(
    index_path: Path,
    *,
    ensure_schema: Callable[[duckdb.DuckDBPyConnection], None],
) -> duckdb.DuckDBPyConnection:
    """Discard the on-disk index and reopen it empty with the current schema.

    ``ensure_schema`` is the store's own idempotent DDL function — injected
    so this stays store-agnostic.
    """
    discard_index(index_path)
    conn = duckdb.connect(str(index_path))
    ensure_schema(conn)
    return conn


def open_index(
    index_path: Path,
    *,
    ensure_schema: Callable[[duckdb.DuckDBPyConnection], None],
    stamp_meta: Callable[[duckdb.DuckDBPyConnection], None],
    index_file_is_the_cause: Callable[[Path], bool],
) -> tuple[duckdb.DuckDBPyConnection, bool]:
    """Open the content-addressed derived index at *index_path* (named
    ``_index.<fp>.duckdb`` by the caller, see :func:`index_file_name`) and
    ensure its schema is current.

    ``is_fresh`` is ``True`` when the caller should treat the index as
    (re)built-from-scratch — either the file didn't exist, or it was discarded
    by one of the two self-heal paths below. The cold-start ingest sweep then
    repopulates it from parquet (its ``_ingested`` ledger is empty). The schema
    is idempotently aligned with the code via the injected ``ensure_schema``.

    Two self-heal paths, both funnelling into a discard + rebuild:

    1. **Unreadable file** (corrupt, #47): a ``kill -9`` mid-write, a bad disk
       block, a DuckDB storage-format bump on upgrade. Opening raises a DuckDB
       error; if a fresh probe confirms the fault is the *file* (not the
       environment — disk full / read-only, see the injected
       ``index_file_is_the_cause``) the index is discarded and reopened empty.
       An environmental fault re-raises — we never delete the derived index on
       a transient disk error.
    2. **Readable but BUILD-INCOMPLETE** (#53 P1): *this* path is named after
       this code's own fingerprint — the projection DDL, adapter registry,
       and schema whitelist that produced ``index_path``'s name are, by
       construction, exactly this code's — so there is no shape left to
       compare against a stored stamp. What CAN still be wrong is a crash
       mid-build: the file was created but the build-complete marker
       (``built_at`` in ``_index_meta``) was never written. A missing marker
       is treated as an interrupted build and the whole index is discarded
       and rebuilt from parquet.

    Every (re)build re-stamps ``_index_meta`` via the injected ``stamp_meta``
    (provenance + build-complete marker); a complete pre-existing index opens
    normally with ``is_fresh=False``, rows intact.

    ``ensure_schema``, ``stamp_meta``, and ``index_file_is_the_cause`` are all
    injected callables — the store-specific state (schema DDL, provenance
    triple) is owned by the caller, keeping this function store-agnostic.
    """
    is_fresh = not index_path.exists()
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = duckdb.connect(str(index_path))
        ensure_schema(conn)
    except duckdb.Error as exc:
        # Path 1 — unreadable file. Self-heal only when BOTH conditions hold;
        # each other case re-raises for a distinct reason:
        if is_fresh:
            raise  # a brand-new file failing isn't corruption — an env/DuckDB fault
        if not index_file_is_the_cause(index_path.parent):
            raise  # a fresh probe also fails → environmental (disk full / read-only)
        logger.warning(
            "Derived index at %s is unreadable (%s: %s) — discarding the "
            "derived index and rebuilding from parquet.",
            index_path,
            type(exc).__name__,
            str(exc).splitlines()[0] if str(exc) else "",
        )
        if conn is not None:
            try:
                conn.close()
            except duckdb.Error:
                pass
        conn = reset_index(index_path, ensure_schema=ensure_schema)
        stamp_meta(conn)
        return conn, True

    # Path 2 — readable. A pre-existing file with no build-complete marker
    # was interrupted mid-build; a fresh file always needs (and gets) its
    # first stamp below, unconditionally.
    if not is_fresh:
        if "built_at" in read_index_meta(conn):
            return conn, False  # build complete → normal open, keep the existing rows
        logger.warning(
            "Derived index at %s has no build-complete marker (an interrupted "
            "build) — discarding and rebuilding from parquet.",
            index_path,
        )
        try:
            conn.close()
        except duckdb.Error:
            pass
        conn = reset_index(index_path, ensure_schema=ensure_schema)
    stamp_meta(conn)
    return conn, True


def stamp_epochs_ledger(store_dir: Path, fingerprint: str, litmus_version: str) -> None:
    """Best-effort upsert of ``(fingerprint, seen_by, last_seen)`` into
    the shared ``_epochs.json`` ledger in *store_dir* on daemon open.

    This is the *write* half of the §6 GC signal (derived-index-versioning.md)
    — a passive last-access record so ``litmus data index gc``/``list`` (P4/P5)
    can reap/report epochs without opening every index file. GC *policy* is
    out of scope here; this only stamps.

    ``seen_by`` accumulates every distinct ``litmus_version`` that has ever
    opened this epoch file (sorted, deduplicated) — human identity for
    ``litmus data index list`` (§7): a behaviorally-identical projection can
    be, and often is, opened by several package versions (the sharing
    collapse, §3). Tolerates a pre-P5 ledger entry shaped
    ``{litmus_version, last_seen}`` (a single version, not yet a set) by
    folding it into ``seen_by`` before appending — this ledger is pre-release
    and best-effort, so no formal migration is needed, just no crash on read.

    Keyed by the same 12-char prefix used in the index filename (matching the
    file it describes 1:1) — collisions are astronomically unlikely at this
    length (see :func:`index_file_name`).

    Deliberately best-effort: a ledger write is bookkeeping, never load-bearing
    for correctness (the index itself is the source of truth), so any failure
    (disk full, permissions, concurrent-write race) is logged and swallowed —
    it must NEVER crash or fail the daemon.
    """
    ledger_path = store_dir / "_epochs.json"
    try:
        try:
            existing = json.loads(ledger_path.read_text())
            data: dict[str, Any] = existing if isinstance(existing, dict) else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        key = fingerprint[:12]
        prior = data.get(key)
        seen_by: list[str] = []
        if isinstance(prior, dict):
            prior_seen_by = prior.get("seen_by")
            if isinstance(prior_seen_by, list):
                seen_by = [str(v) for v in prior_seen_by if v]
            else:
                legacy_version = prior.get("litmus_version")
                if legacy_version:
                    seen_by = [str(legacy_version)]
        seen_by = sorted({*seen_by, litmus_version})
        data[key] = {
            "seen_by": seen_by,
            "last_seen": datetime.now(UTC).isoformat(),
        }
        tmp_path = ledger_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp_path.replace(ledger_path)  # atomic rename — no torn/partial ledger
    except OSError as exc:
        logger.warning("Could not update epochs ledger at %s: %s", ledger_path, exc)


def read_epochs_ledger(store_dir: Path) -> dict[str, dict[str, Any]]:
    """Best-effort read of the shared ``_epochs.json`` ledger, normalized.

    Used by ``litmus data index list``/``gc`` (§7) to source SEEN BY / LAST
    SEEN without opening every epoch file. Tolerates the pre-P5 ledger shape
    (``{fp12: {litmus_version, last_seen}}``) by folding a legacy
    ``litmus_version`` into a single-element ``seen_by`` — mirrors the
    tolerance :func:`stamp_epochs_ledger` applies on write. Never raises;
    an unreadable/corrupt/missing ledger yields ``{}`` (the CLI degrades to
    showing "unknown" rather than crashing on best-effort bookkeeping).
    """
    ledger_path = store_dir / "_epochs.json"
    try:
        raw = json.loads(ledger_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for fp12, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        seen_by = entry.get("seen_by")
        if not isinstance(seen_by, list):
            legacy_version = entry.get("litmus_version")
            seen_by = [legacy_version] if legacy_version else []
        normalized[fp12] = {
            "seen_by": sorted({str(v) for v in seen_by if v}),
            "last_seen": entry.get("last_seen"),
        }
    return normalized


def remove_epochs_ledger_entries(store_dir: Path, fp12s: set[str]) -> None:
    """Best-effort removal of the given fp12 keys from ``_epochs.json``.

    Used by ``litmus data index rm``/``gc`` (§7) after unlinking an epoch
    file, so the ledger doesn't keep a stale entry for a file that's gone.
    Mirrors :func:`stamp_epochs_ledger`'s atomic tmp-rename write and
    best-effort ``OSError`` swallow — a ledger cleanup failure must never
    crash the CLI command that triggered it.
    """
    ledger_path = store_dir / "_epochs.json"
    if not ledger_path.exists():
        return
    try:
        try:
            existing = json.loads(ledger_path.read_text())
            data: dict[str, Any] = existing if isinstance(existing, dict) else {}
        except (OSError, json.JSONDecodeError):
            return
        if not any(fp12 in data for fp12 in fp12s):
            return
        for fp12 in fp12s:
            data.pop(fp12, None)
        tmp_path = ledger_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp_path.replace(ledger_path)
    except OSError as exc:
        logger.warning("Could not update epochs ledger at %s: %s", ledger_path, exc)
