"""Central registry of durable-artifact schema versions.

The single home for *which durable stores exist and which schema versions
each can read*. Every durable artifact a Litmus daemon writes carries a
schema-version stamp in its file metadata; the stamp is the **migration
key**. At each store's ``durable-artifact -> rows`` read boundary a
whitelist-dispatch reader matches the stamp against
:data:`KNOWN_SCHEMA_VERSIONS` and picks the adapter that projects that
version forward to the current shape. See
``docs/_internal/explorations/schema-versioning-migration.md`` for the full
strategy (read-time adaptation, coexist-always + optional-migrate).

Versioning is SemVer and **decoupled from the litmus package version** — the
stores diverge on their own lines, so schema ``1.0`` at package ``0.3.0`` is
deliberate, not a mismatch:

- **MINOR** (``1.0 -> 1.1``) — additive only. ``union_by_name`` null-fills old
  files and ``ALTER TABLE ADD COLUMN IF NOT EXISTS`` extends the projection.
  No adapter.
- **MAJOR** (``1.x -> 2.0``) — breaking (rename / reshape / remove). Needs a
  per-version read-time adapter + a frozen reference doc for the outgoing
  major.

All stores reset to ``"1.0"`` at the 0.3.0 release — the designed baseline the
"support every shipped version forever" clock starts from. Pre-1.0 / unstamped
artifacts are unsupported by design (regenerate).
"""

from __future__ import annotations

from enum import StrEnum


class SchemaStore(StrEnum):
    """A durable-artifact store that stamps and dispatches on a schema version.

    Events carries **two** coordinates (§3 of the strategy doc): the storage
    ``ENVELOPE`` (the ``_IPC_SCHEMA`` column shape) and the payload
    ``EVENT_CATALOG`` (the event models). They version independently.
    """

    RUNS = "runs"
    """Runs parquet — content fused into the columnar schema (footer metadata)."""
    EVENTS_ENVELOPE = "events_envelope"
    """Event WAL Arrow IPC envelope — ``event_log._IPC_SCHEMA``."""
    EVENT_CATALOG = "event_catalog"
    """Event payload catalog — the event models in ``data.events``."""
    CHANNELS = "channels"
    """Channel ``.arrow`` skeleton, value typed by ``value_type``."""
    FILES = "files"
    """FileStore sidecar (``FileArtifactMetadata``); the blob is opaque."""


# The stamp a freshly written artifact carries, per store. This is the one
# home for the current version — every store's public constant aliases the
# matching entry here (do not hardcode the string at the write site).
CURRENT_SCHEMA_VERSION: dict[SchemaStore, str] = {
    SchemaStore.RUNS: "1.0",
    SchemaStore.EVENTS_ENVELOPE: "1.0",
    SchemaStore.EVENT_CATALOG: "1.0",
    SchemaStore.CHANNELS: "1.0",
    SchemaStore.FILES: "1.0",
}

# Older majors a store still ships a read-time adapter for. Empty today
# (1.0-only). Add an entry the *same commit* its ``vN -> current`` adapter
# lands, so the whitelist reader starts accepting that version exactly when it
# can transform it.
_LEGACY_READABLE: dict[SchemaStore, frozenset[str]] = {store: frozenset() for store in SchemaStore}

# Versions each store's reader will dispatch. Current ∪ legacy-readable, so the
# current version is always accepted by construction. Anything not in this set
# is refused at read time ("unsupported schema version"); an absent stamp is
# refused as pre-1.0 ("regenerate").
KNOWN_SCHEMA_VERSIONS: dict[SchemaStore, frozenset[str]] = {
    store: frozenset({CURRENT_SCHEMA_VERSION[store]}) | _LEGACY_READABLE[store]
    for store in SchemaStore
}
