"""Canonical catalog-family grain keys — PRIVATE, internal dedup identity.

**Not public library surface**, same status as ``_schema_keys.py`` (the
fact-table family's grain module, which this one is the catalog-family
sibling of — see that module's docstring for the shared rationale). A bench
client has no reason to import an ingest surface's dedup identity; this
module exists so the LOGICAL grain for each **catalog-family** store
(events / files / channels — Arrow/Flight + Postgres-catalog stores served
by windowed/keyed reads, docs/25, as opposed to `_schema_keys.py`'s flat
BigQuery MERGE tables) is defined exactly once and consumed by every
internal renderer of it: the local stores that already enforce it (the WAL's
own event ``id``, `data/files/catalog.py`'s `file_catalog.uri PRIMARY KEY`,
`data/channels/index.py`'s query-time sample dedup) and the cloud's Postgres
catalog backends (`testerkit_server`'s `events_backend.py` / `files_backend.py`
/ `channels_backend.py`).

**Share the grain, not the storage engine.** Each `*_KEY` tuple is the
LOGICAL identity — plain field names, engine-agnostic. The cloud's tables key
on ``(org_id, *KEY)`` (org is the added tenancy dimension; see docs/42 §1.4).

- ``EVENTS_KEY`` — an event's own ``id`` (globally unique, minted once by its
  producer). Enforced today by the cloud's `events_index` `PRIMARY KEY
  (org_id, id)`; locally the WAL never duplicates an id by construction (no
  local uniqueness constraint enforces it — it doesn't need to, one writer,
  one id, once).
- ``FILES_KEY`` — a FileStore artifact's ``uri`` (``file://{date}/{session_id}/
  {name}``, minted once by `FileStore.write`/`open_stream` and never reused —
  see `data/files/store.py`). This is the field `data/files/catalog.py`
  primary-keys `file_catalog` on and upserts (`ON CONFLICT (uri) DO UPDATE`) —
  a re-landed uri REFRESHES its row, a new uri is a distinct artifact even
  with byte-identical content. ``content_hash`` is deliberately NOT part of
  this grain — it is a bandwidth/change-detection optimization, never
  identity (docs/42 §0: this is precisely the bug the cloud had).
- ``CHANNEL_SAMPLE_KEY`` — the full logical identity of one channel sample:
  ``(channel_id, session_id, sample_offset)``. Both consumers that dedup on
  it (`data/channels/index.py`'s `ChannelIndex.query`, the cloud's
  `channels_backend.windowed_series`) apply it INSIDE a call already scoped
  to one `channel_id` (a `WHERE channel_id = ?` / a `list_window(channel_id=
  ...)`-pruned segment set respectively), so the column that's actually
  compared at dedup time is just `(session_id, sample_offset)` — `channel_id`
  is constant there, not absent from the grain. A sample with an unstamped
  `sample_offset` (`NULL` or negative — legacy, pre-cursor data) is never
  collapsed by either consumer; see `data.channels.window.dedup_on_sample_offset`,
  the single shared implementation both `ChannelIndex.query` and
  `windowed_series` call.
"""

from __future__ import annotations

EVENTS_KEY: tuple[str, ...] = ("id",)

FILES_KEY: tuple[str, ...] = ("uri",)

CHANNEL_SAMPLE_KEY: tuple[str, ...] = ("channel_id", "session_id", "sample_offset")

# The URI scheme every FileStore artifact reference carries (`data/files/store.py`).
# Exported so a consumer that needs the backend-relative key encoded in a uri
# (the cloud's object-storage key derivation) can strip it via
# :func:`file_storage_key` instead of hand-rolling the same string slice
# `FileStore._resolve_key` already performs locally.
FILE_URI_SCHEME = "file://"


def file_storage_key(uri: str) -> str:
    """The backend-relative key encoded in a ``file://`` URI — pure parsing,
    mirroring `data.files.store.FileStore._resolve_key`'s own slice so a
    `file://` URI maps to the same key shape everywhere a consumer (local or
    cloud) needs to turn the identity back into a storage location. Raises
    ``ValueError`` for a non-``file://`` uri — callers here always hold a uri
    that already passed through `FileStore.write`, so an unrecognized scheme
    is a caller bug, not tolerable input.
    """
    if not uri.startswith(FILE_URI_SCHEME):
        raise ValueError(f"not a file:// uri: {uri!r}")
    return uri[len(FILE_URI_SCHEME) :]
