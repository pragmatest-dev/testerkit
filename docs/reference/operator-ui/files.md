# Files

`/files` lists the artifacts in the FileStore — images, waveforms, byte
streams, and any blob a test recorded via `observe(name, value)` or
`files.write(...)`. Each row links to a per-artifact detail page with an
inline viewer and a download button.

Reach it from the **Files** entry under DATA STORES in the sidebar.

## Filters

Filter widgets render above the table:

- **MIME** — dropdown of the MIME types (or file extensions) actually present,
  `(any)` by default.
- **Name contains** — case-insensitive substring match on the filename.
- **Since / Until** — created-at date window.
- **Session** — set only by deep-links from a run (`/results/{run_id}` →
  Files), shown as a banner with a Clear affordance. There is no session
  picker; operators scope by date / name / MIME, not by session UUID.

## Table

![Files — artifact list](../../_assets/operator-ui/files/table.png)

One row per artifact:

| Column | Meaning |
|--------|---------|
| Filename | The artifact's name (the `name` passed to `observe` / `files.write`, plus the format's extension). |
| MIME | The recorded MIME type, or the file extension when the sidecar MIME is absent. |
| Size | Human-readable on-disk size. |
| Created | When the artifact was written. |

Clicking a row opens `/files/{date}/{session_id}/{filename}`.

## Detail page — viewer + download

The detail page shows a metadata card (MIME, size, last-modified) and an
**inline viewer that dispatches on the file extension** (the cheapest reliable
signal — a sidecar MIME can be missing). Supported viewers:

- **Image** (`.png`, `.jpg`, …) — rendered inline.
- **JSON** — pretty-printed.
- **JSONL** — one row per line in a table.
- **CSV** — parsed into a table.
- **NPZ** — a `Waveform` chart.
- **NPY** — array stats.
- Anything else, or files over the viewer size cap — a **hex / download**
  fallback.

The **Download** button (and `?download=1` on the static route) forces a
`Content-Disposition` save rather than inline rendering. Bytes are served
through the FileStore, so this works whether the blob backend is local disk
or a remote object store.

## Bookmarkable URL state

The list mirrors its filters into the URL (`?mime=`, `?name=`, `?since=`,
`?until=`, `?session_id=`), so a filtered view is shareable. The detail page's
URL is the artifact's full key — `/files/{date}/{session_id}/{filename}`.

## Underlying data

Rows come from the **FileStore catalog** (the `litmus_files` MCP tool and
`GET /api/files/catalog` expose the same list), never from a directory scan —
the catalog is the index, the blobs live in the backend.

## See also

- [How-to: capture an artifact](../../how-to/data/capture-an-artifact.md) — `observe` / `files.write` / `files.stream`
- [Concepts: the three verbs](../../concepts/data/three-verbs.md) — how `observe` routes a blob to the FileStore
- [Channels](channels/list.md) — the time-series sibling store
