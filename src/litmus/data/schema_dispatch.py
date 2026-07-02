"""Read-time schema-version dispatch — the seam where a durable artifact's
stamp selects the adapter that projects it to the current shape.

Called exactly once per store, at that store's ``durable-artifact -> rows``
read boundary (§1/§4 of ``schema-versioning-migration.md``). The whole
versioning surface reduces to:

    stamp  --dispatch-->  adapter(source_version -> current)  -->  current rows

Three outcomes when a file's stamp is read:

- **present and whitelisted** (in :data:`KNOWN_SCHEMA_VERSIONS`) -> return that
  version's adapter (identity today: 1.0 == current, no transform).
- **present but unknown** (a future ``5.0``, or an abandoned version) -> refuse
  with :class:`SchemaVersionRefused` ("unsupported schema version").
- **absent** (unstamped, i.e. pre-1.0) -> refuse ("regenerate"). 1.0 is always
  stamped at write, so absence can only mean a pre-stamp file.

Each store's read boundary catches :class:`SchemaVersionRefused` and routes it
into that store's existing skip/quarantine path, so one bad-version file is
isolated and the good ones still land.

The adapter registry ships **1.0-identity only** — no speculative transforms.
A real ``vN -> current`` adapter is registered here the same commit its source
version joins :data:`KNOWN_SCHEMA_VERSIONS`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from litmus.data.schema_versions import KNOWN_SCHEMA_VERSIONS, SchemaStore

# An adapter is a pure transform from a source-version artifact to the current
# shape. For the Arrow stores (runs / events / channels) it is
# ``pa.Table -> pa.Table``; for the files sidecar it is
# ``FileArtifactMetadata -> FileArtifactMetadata``. Kept structurally typed
# (``Any -> Any``) because the registry spans both.
Adapter = Callable[[Any], Any]


def _version_tuple(v: str) -> tuple[int, ...] | None:
    """Parse a dotted version into a comparable int tuple; ``None`` if unparseable."""
    try:
        return tuple(int(part) for part in v.split("."))
    except (ValueError, AttributeError):
        return None


class SchemaVersionRefused(Exception):
    """A durable artifact carries a schema version this store cannot read.

    ``deferrable`` splits the two refusal classes that need different fates at
    the read boundary (see #43):

    - **deferrable=True** — the stamp is a version *newer* than anything this
      daemon knows. The machine's singleton daemon trends newest, so a newer
      daemon WILL read it — the file must stay re-attemptable (never permanently
      ledgered), else it goes invisible forever after the daemon is upgraded.
    - **deferrable=False** — absent (pre-1.0) or an unparseable / older-unknown
      stamp. No future daemon will read it: permanent skip (regenerate).
    """

    def __init__(
        self, store: SchemaStore, version: str | None, reason: str, *, deferrable: bool
    ) -> None:
        self.store = store
        self.version = version
        self.deferrable = deferrable
        super().__init__(f"[{store.value}] schema version {version!r}: {reason}")


# ── Adapter registry ────────────────────────────────────────────────────────
# One transform per (store, source_version) -> current. Empty today (every
# known version IS current, so dispatch returns identity). ``register_adapter``
# is how a future major's transform is added; ``_identity`` is the 1.0 no-op.


def _identity(rows: Any) -> Any:
    return rows


_ADAPTERS: dict[SchemaStore, dict[str, Adapter]] = {store: {} for store in SchemaStore}


def register_adapter(store: SchemaStore, source_version: str, adapter: Adapter) -> None:
    """Register a ``source_version -> current`` transform for *store*.

    Callers must also add ``source_version`` to the store's
    ``_LEGACY_READABLE`` set in ``schema_versions`` so the reader accepts it.
    """
    _ADAPTERS[store][source_version] = adapter


def dispatch(store: SchemaStore, version: str | None) -> Adapter:
    """Whitelist-dispatch *version* for *store*; return its adapter or refuse.

    Returns the registered adapter, or :func:`_identity` when the version is
    known but current-shaped (the 1.0 case). Raises
    :class:`SchemaVersionRefused` for an unknown or absent version.
    """
    if version is None:
        raise SchemaVersionRefused(
            store,
            None,
            "unstamped (pre-1.0) artifact — unsupported by design; regenerate",
            deferrable=False,
        )
    if version not in KNOWN_SCHEMA_VERSIONS[store]:
        known = ", ".join(sorted(KNOWN_SCHEMA_VERSIONS[store]))
        # Deferrable iff strictly NEWER than every known version — a newer daemon
        # will read it. Older / unparseable-unknown is pre-1.0 or garbage: permanent.
        vt = _version_tuple(version)
        known_tuples = [t for t in (_version_tuple(k) for k in KNOWN_SCHEMA_VERSIONS[store]) if t]
        deferrable = bool(vt and known_tuples and vt > max(known_tuples))
        raise SchemaVersionRefused(
            store,
            version,
            f"unsupported schema version (known: {known})",
            deferrable=deferrable,
        )
    return _ADAPTERS[store].get(version, _identity)


# ── Stamp extraction ────────────────────────────────────────────────────────


def stamp_from_arrow_metadata(
    metadata: dict[bytes, bytes] | None, key: bytes = b"schema_version"
) -> str | None:
    """Read a schema-version stamp from Arrow/parquet file-level metadata.

    Returns ``None`` if the metadata is absent or lacks *key* — which
    :func:`dispatch` treats as a pre-1.0 (unstamped) artifact.
    """
    if not metadata:
        return None
    raw = metadata.get(key)
    return raw.decode() if raw is not None else None
