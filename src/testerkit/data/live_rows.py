"""Live channel wire format — bench-side split, strip, bucket, size-guard, diff.

The live channel (docs/41) ships the rows an executing run has already folded to a
server as small Firestore-shaped documents, best-effort. This module is the ONE
definition of that wire format, imported by the bench pusher (``testerkit forward``)
and by the server's push handler, so neither side hand-writes a second copy:

* wire models — :class:`LivePush`, :class:`LiveDoc`, :class:`LiveHeader`,
  :class:`LivePushResponse`, versioned by :data:`LIVE_WIRE_VERSION`;
* the strip / rejoin mapping of run and step context columns (:data:`RUN_CONTEXT`,
  :data:`STEP_CONTEXT`) — defined once here (docs/41 §2.3);
* bucketing (a step doc per attempt, vector chunk docs of 16), the deterministic
  ``row_id``, and the size guard (soft 256 KiB / hard 900 KiB);
* the per-doc content-hash diff (:class:`LiveSyncState`).

Everything here is pure: a ``read_models.RunDetail`` in, documents out (and back).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from testerkit.data.backends._event_accumulator import VECTOR_BUCKET_WIDTH
from testerkit.data.read_models import (
    InputRow,
    MeasurementRow,
    OutputRow,
    RunDetail,
    RunRow,
    StepRow,
    VectorRow,
)

LIVE_WIRE_VERSION = 1

STEP_BUCKET_WIDTH = 128
"""Step-scoped measurement/IO rows per ``s`` chunk doc (docs/41 §2.2)."""

SOFT_DOC_BYTES = 256 * 1024
HARD_DOC_BYTES = 900 * 1024
MAX_WRITES_PER_PUSH = 400
MAX_BODY_BYTES = 4 * 1024 * 1024
"""Server body cap (docs/41 §3.2); the bench keeps each push under it."""

SPLIT_BUCKET_PREFIX = "p"
"""Bucket prefix of a per-vector doc produced by the soft-budget split.

docs/41 §2.4 names these ``v{vector_index}``, which collides with the chunk bucket
``v{vector_index // 16}`` (split ``v3`` = vector 3 vs chunk ``v3`` = vectors 48-63)."""

_STEP_ATTEMPT_KEYS = ("step_path", "step_retry", "vector_outer_index")

# Run-level columns repeated on step / vector / measurement / IO rows -> the header
# ``run`` (RunRow) column that holds the same value (docs/41 §2.3). Only columns a
# row model actually has are stripped from it. ``run_id`` comes from the doc path.
RUN_CONTEXT: dict[str, str] = {
    **{
        c: c
        for c in (
            "file_path",
            "session_id",
            "site_index",
            "site_name",
            "uut_serial_number",
            "uut_part_number",
            "uut_revision",
            "uut_lot_number",
            "station_id",
            "station_name",
            "station_hostname",
            "fixture_id",
            "test_phase",
            "part_id",
            "part_name",
            "part_revision",
            "station_type",
            "station_location",
            "operator_id",
            "operator_name",
            "project_name",
            "git_commit",
            "git_branch",
            "git_remote",
            "python_version",
            "testerkit_version",
            "env_fingerprint",
        )
    },
    "run_outcome": "outcome",
    "run_started_at": "started_at",
    "run_ended_at": "ended_at",
}

# Step-level columns repeated on vector / measurement / IO rows -> the step doc's
# ``step`` map column (docs/41 §2.3). The attempt key columns come from the chunk key.
STEP_CONTEXT: dict[str, dict[str, str]] = {
    "vectors": {"step_index": "step_index", "step_name": "step_name"},
    "measurements": {
        "step_index": "step_index",
        "step_name": "step_name",
        "step_outcome": "outcome",
        "step_started_at": "started_at",
        "step_ended_at": "ended_at",
    },
    "inputs": {"step_index": "step_index"},
    "outputs": {"step_index": "step_index"},
}

RowKind = Literal["vectors", "measurements", "inputs", "outputs"]
_ROW_KINDS: tuple[RowKind, ...] = ("vectors", "measurements", "inputs", "outputs")
_ROW_MODELS: dict[str, type[BaseModel]] = {
    "vectors": VectorRow,
    "measurements": MeasurementRow,
    "inputs": InputRow,
    "outputs": OutputRow,
}

# Header counters: changing them alone never triggers a header write (docs/41 §2.1).
_HEADER_COUNTERS = ("num_steps", "num_measurements")

# Assumed org-id length for the document-name term of the size estimate: the bench
# does not know the org (the server binds it from the token).
_ASSUMED_ORG_ID_LEN = 32


def _stripped_columns(kind: str) -> set[str]:
    fields = _ROW_MODELS[kind].model_fields
    return (
        {c for c in RUN_CONTEXT if c in fields}
        | set(STEP_CONTEXT[kind])
        | set(_STEP_ATTEMPT_KEYS)
        | {"run_id"}
    )


def _step_fields() -> set[str]:
    """Columns of a step doc's ``step`` map: StepRow minus run context, key, run_id."""
    return set(StepRow.model_fields) - set(RUN_CONTEXT) - set(_STEP_ATTEMPT_KEYS) - {"run_id"}


def _kept_columns(kind: str) -> set[str]:
    return set(_ROW_MODELS[kind].model_fields) - _stripped_columns(kind)


# --------------------------------------------------------------------------- #
# Wire models                                                                 #
# --------------------------------------------------------------------------- #


class LiveKey(BaseModel):
    """A step attempt: the ``StepRow`` grain (``step_path, step_retry,
    vector_outer_index``), which matches ``run_object._step_sort_key``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_path: str
    step_retry: int
    vector_outer_index: int | None = None


def row_id_for(kind: Literal["step", "chunk"], key: LiveKey, bucket: str | None = None) -> str:
    """Deterministic doc id: first 24 hex chars of the sha256 of ``step|…`` or
    ``chunk|…|bucket`` (the ``firestore_store._catalog_doc_id`` convention). A pure
    function of the row key, so ids are stable across pushes, restarts and sides."""
    voi = "" if key.vector_outer_index is None else str(key.vector_outer_index)
    parts = [kind, key.step_path, str(key.step_retry), voi]
    if kind == "chunk":
        parts.append(bucket or "")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


Columns = dict[str, list[Any]]


class LiveDoc(BaseModel):
    """One ``rows/{row_id}`` document: a step doc or a chunk doc (docs/41 §2.2).

    ``expires_at`` is not on the wire; the server stamps it on every write."""

    model_config = ConfigDict(extra="forbid")

    v: int = LIVE_WIRE_VERSION
    row_id: str
    kind: Literal["step", "chunk"]
    key: LiveKey
    bucket: str | None = None
    step: dict[str, Any] | None = None
    vectors: Columns | None = None
    measurements: Columns | None = None
    inputs: Columns | None = None
    outputs: Columns | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if self.row_id != row_id_for(self.kind, self.key, self.bucket):
            raise ValueError("row_id does not match its key and bucket")
        maps = {k: getattr(self, k) for k in _ROW_KINDS}
        if self.kind == "step":
            if (
                self.step is None
                or self.bucket is not None
                or any(m is not None for m in maps.values())
            ):
                raise ValueError("a step doc carries `step` only")
            unknown = set(self.step) - _step_fields()
            if unknown:
                raise ValueError(f"unknown step columns: {sorted(unknown)}")
            return self
        if self.step is not None or not self.bucket:
            raise ValueError("a chunk doc carries a bucket and row maps, not `step`")
        for kind, cols in maps.items():
            if cols is None:
                continue
            unknown = set(cols) - _kept_columns(kind)
            if unknown:
                raise ValueError(f"unknown {kind} columns: {sorted(unknown)}")
            if len({len(c) for c in cols.values()}) > 1:
                raise ValueError(f"{kind} columns differ in length")
        return self


class LiveHeader(BaseModel):
    """The header doc content (docs/41 §2.1). ``lease_until`` / ``expires_at`` are
    server-stamped; ``state == "finalized"`` is the server tombstone, never sent."""

    model_config = ConfigDict(extra="forbid")

    run: RunRow
    state: Literal["running", "ended"]
    current_step_path: str | None = None
    truncated: bool = False


class LivePush(BaseModel):
    """Body of ``POST /ingest/live/runs/{run_id}``. No org field: the org comes from
    the token, and extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    v: int = LIVE_WIRE_VERSION
    run_id: str
    seq: int
    header: LiveHeader | None = None
    upserts: list[LiveDoc] = []
    deletes: list[str] = []
    manifest: list[str] | None = None

    @model_validator(mode="after")
    def _check_writes(self) -> Self:
        if len(self.upserts) + len(self.deletes) + (self.header is not None) > MAX_WRITES_PER_PUSH:
            raise ValueError(f"more than {MAX_WRITES_PER_PUSH} writes in one push")
        return self


class LivePushResponse(BaseModel):
    """Push response. The bench ignores fields it does not know."""

    model_config = ConfigDict(extra="ignore")

    watched: bool = False
    finalized: bool = False
    resync: bool = False
    stale: bool = False


# --------------------------------------------------------------------------- #
# Ordering                                                                    #
# --------------------------------------------------------------------------- #


def _n(value: int | None) -> int:
    return -1 if value is None else value


def _step_sort_key(step: StepRow) -> tuple[int, str, int, int]:
    return (
        _n(step.step_index),
        step.step_path or "",
        _n(step.step_retry),
        _n(step.vector_outer_index),
    )


def _vector_sort_key(vector: VectorRow) -> tuple[int, str, int, int, int, int]:
    return (
        *_step_sort_key_of(vector),
        _n(vector.vector_index),
        _n(vector.vector_retry),
    )


def _step_sort_key_of(
    row: VectorRow | MeasurementRow | InputRow | OutputRow,
) -> tuple[int, str, int, int]:
    return (
        _n(row.step_index),
        row.step_path or "",
        _n(row.step_retry),
        _n(row.vector_outer_index),
    )


def _measurement_sort_key(m: MeasurementRow) -> tuple[Any, ...]:
    return (
        *_step_sort_key_of(m),
        _n(m.vector_index),
        _n(m.vector_retry),
        _n(m.ordinal),
        _n(m.index),
        m.measurement_name or "",
    )


def _io_sort_key(r: InputRow | OutputRow) -> tuple[Any, ...]:
    return (
        *_step_sort_key_of(r),
        _n(r.vector_index),
        _n(r.vector_retry),
        _n(r.ordinal),
        _n(r.index),
        r.name or "",
    )


def sort_detail(detail: RunDetail) -> RunDetail:
    """``detail`` with every list in a total order: steps and vectors as
    ``run_object._with_deterministic_order`` (docs/41 §5), measurements and IO rows
    by their step key, then vector key, then position in the carrier's list."""
    return detail.model_copy(
        update={
            "steps": sorted(detail.steps, key=_step_sort_key),
            "vectors": sorted(detail.vectors, key=_vector_sort_key),
            "measurements": sorted(detail.measurements, key=_measurement_sort_key),
            "inputs": sorted(detail.inputs, key=_io_sort_key),
            "outputs": sorted(detail.outputs, key=_io_sort_key),
        }
    )


# --------------------------------------------------------------------------- #
# Size estimate (Google's storage-size formula, no network)                   #
# --------------------------------------------------------------------------- #


def _value_size(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return 8
    if isinstance(value, str):
        return len(value.encode()) + 1
    if isinstance(value, dict):
        return sum(len(str(k).encode()) + 1 + _value_size(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_value_size(v) for v in value)
    return len(str(value).encode()) + 1


def estimate_doc_size(run_id: str, doc: LiveDoc) -> int:
    """Firestore stored size of ``doc``: document name + field names + values + 32 B
    (Google's storage-size formula). The wire values are used as-is (timestamps are
    ISO strings). The org-id length is assumed (:data:`_ASSUMED_ORG_ID_LEN`).

    UNVERIFIED: the document-name term (each path segment's UTF-8 bytes + 1, plus 16)
    is written from memory of Google's storage-size documentation, not checked
    against it; it only matters near the soft/hard caps."""
    segments = ("orgs", "x" * _ASSUMED_ORG_ID_LEN, "live_runs", run_id, "rows", doc.row_id)
    name = sum(len(s.encode()) + 1 for s in segments) + 16
    fields = doc.model_dump(mode="json", exclude={"row_id"}, exclude_none=True)
    fields["expires_at"] = 0  # server-stamped timestamp (8 B)
    return name + _value_size(fields) + 32


# --------------------------------------------------------------------------- #
# Split / strip / bucket / size-guard                                         #
# --------------------------------------------------------------------------- #


class LiveDocSet(BaseModel):
    """Result of :func:`to_live_docs`: the docs by ``row_id`` plus whether any row
    doc was omitted for size (the header's ``truncated`` flag)."""

    model_config = ConfigDict(extra="forbid")

    docs: dict[str, LiveDoc]
    truncated: bool = False


def bucket_for(vector_index: int | None, ordinal: int | None) -> str:
    """Bucket of a chunk row: ``v{vector_index // 16}`` for a vector-scoped row (its
    ``vector_retry`` repeats land in the same bucket), else ``s{ordinal // 128}``."""
    if vector_index is not None:
        return f"v{vector_index // VECTOR_BUCKET_WIDTH}"
    return f"s{(ordinal or 0) // STEP_BUCKET_WIDTH}"


def _key_of(row: StepRow | VectorRow | MeasurementRow | InputRow | OutputRow) -> LiveKey:
    return LiveKey(
        step_path=row.step_path or "",
        step_retry=row.step_retry or 0,
        vector_outer_index=row.vector_outer_index,
    )


def _row_columns(kind: str, rows: Sequence[BaseModel]) -> Columns:
    """Column-major map of ``rows`` restricted to the kept (non-stripped) columns."""
    kept = [c for c in _ROW_MODELS[kind].model_fields if c in _kept_columns(kind)]
    dumped = [r.model_dump(mode="json") for r in rows]
    return {c: [d[c] for d in dumped] for c in kept}


def _make_chunk(
    run_id: str, key: LiveKey, bucket: str, rows: dict[str, list[BaseModel]]
) -> tuple[LiveDoc, int]:
    doc = LiveDoc.model_validate(
        {
            "row_id": row_id_for("chunk", key, bucket),
            "kind": "chunk",
            "key": key,
            "bucket": bucket,
            **{k: _row_columns(k, v) for k, v in rows.items() if v},
        }
    )
    return doc, estimate_doc_size(run_id, doc)


def _vector_index_of(row: BaseModel) -> int | None:
    return getattr(row, "vector_index", None)


def to_live_docs(detail: RunDetail) -> LiveDocSet:
    """Split ``detail`` into live docs: one step doc per step attempt, chunk docs of
    16 sweep points per ``(attempt, bucket)`` (docs/41 §2.2), context stripped
    (§2.3), size-guarded (§2.4): a chunk over the soft budget splits per vector, and a
    doc over the hard budget is omitted (``truncated``)."""
    run_id = detail.run.run_id
    detail = sort_detail(detail)
    step_fields = _step_fields()
    docs: dict[str, LiveDoc] = {}
    truncated = False

    def admit(doc: LiveDoc, size: int) -> None:
        nonlocal truncated
        if size > HARD_DOC_BYTES:
            truncated = True
        else:
            docs[doc.row_id] = doc

    for step in detail.steps:
        dumped = step.model_dump(mode="json")
        key = _key_of(step)
        doc = LiveDoc(
            row_id=row_id_for("step", key),
            kind="step",
            key=key,
            step={c: dumped[c] for c in StepRow.model_fields if c in step_fields},
        )
        admit(doc, estimate_doc_size(run_id, doc))

    groups: dict[tuple[LiveKey, str], dict[str, list[BaseModel]]] = {}
    collections: dict[str, Sequence[BaseModel]] = {
        "vectors": detail.vectors,
        "measurements": detail.measurements,
        "inputs": detail.inputs,
        "outputs": detail.outputs,
    }
    for kind, rows in collections.items():
        for row in rows:
            bucket = bucket_for(_vector_index_of(row), getattr(row, "ordinal", None))
            groups.setdefault((_key_of(row), bucket), {}).setdefault(kind, []).append(row)  # type: ignore[arg-type]

    for (key, bucket), rows in groups.items():
        doc, size = _make_chunk(run_id, key, bucket, rows)
        if size <= SOFT_DOC_BYTES or not bucket.startswith("v"):
            admit(doc, size)
            continue
        per_vector: dict[int, dict[str, list[BaseModel]]] = {}
        for kind, kind_rows in rows.items():
            for row in kind_rows:
                vi = _vector_index_of(row)
                assert vi is not None  # a `v` bucket holds vector-scoped rows only
                per_vector.setdefault(vi, {}).setdefault(kind, []).append(row)
        for vi, vrows in per_vector.items():
            admit(*_make_chunk(run_id, key, f"{SPLIT_BUCKET_PREFIX}{vi}", vrows))
    return LiveDocSet(docs=docs, truncated=truncated)


def build_header(detail: RunDetail, *, truncated: bool = False) -> LiveHeader:
    """The header for ``detail`` (see :func:`header_for`)."""
    return header_for(detail.run, detail.steps, truncated=truncated)


def header_for(run: RunRow, steps: Iterable[StepRow], *, truncated: bool = False) -> LiveHeader:
    """The header for ``run``: ``state`` is ``ended`` once the run has an
    ``ended_at``; ``current_step_path`` is the most recently started unfinished step."""
    running = [s for s in steps if s.started_at is not None and s.ended_at is None]
    running.sort(key=lambda s: s.started_at)  # type: ignore[arg-type,return-value]
    current = None if run.ended_at is not None or not running else running[-1].step_path
    return LiveHeader(
        run=run,
        state="ended" if run.ended_at is not None else "running",
        current_step_path=current,
        truncated=truncated,
    )


# --------------------------------------------------------------------------- #
# Rejoin (the inverse of strip; the Python twin of the web's liveDocsToDetail) #
# --------------------------------------------------------------------------- #


def _columns_to_rows(cols: Columns) -> list[dict[str, Any]]:
    if not cols:
        return []
    n = len(next(iter(cols.values())))
    return [{c: vals[i] for c, vals in cols.items()} for i in range(n)]


def rejoin(run: RunRow, docs: Iterable[LiveDoc]) -> RunDetail:
    """Rebuild the P3 ``RunDetail`` from the header ``run`` and the row docs: columns
    back to rows, run and step context re-added, everything in :func:`sort_detail`
    order. A row whose step doc is missing (omitted) keeps its step context ``None``."""
    docs = list(docs)
    run_ctx = {col: getattr(run, src) for col, src in RUN_CONTEXT.items()}
    steps: dict[LiveKey, dict[str, Any]] = {}
    for doc in docs:
        if doc.kind == "step" and doc.step is not None:
            steps[doc.key] = doc.step

    def context(kind: str, key: LiveKey) -> dict[str, Any]:
        out: dict[str, Any] = {"run_id": run.run_id, **key.model_dump()}
        fields = _ROW_MODELS[kind].model_fields
        out.update({c: v for c, v in run_ctx.items() if c in fields})
        step = steps.get(key, {})
        out.update({col: step.get(src) for col, src in STEP_CONTEXT[kind].items()})
        return out

    step_rows = [
        StepRow.model_validate(
            {
                "run_id": run.run_id,
                **doc.key.model_dump(),
                **{c: v for c, v in run_ctx.items() if c in StepRow.model_fields},
                **(doc.step or {}),
            }
        )
        for doc in docs
        if doc.kind == "step"
    ]
    rows: dict[str, list[Any]] = {k: [] for k in _ROW_KINDS}
    for doc in docs:
        if doc.kind != "chunk":
            continue
        for kind in _ROW_KINDS:
            cols = getattr(doc, kind)
            if cols:
                base = context(kind, doc.key)
                model = _ROW_MODELS[kind]
                rows[kind].extend(
                    model.model_validate({**base, **row}) for row in _columns_to_rows(cols)
                )
    return sort_detail(
        RunDetail(
            run=run,
            steps=step_rows,
            vectors=rows["vectors"],
            measurements=rows["measurements"],
            inputs=rows["inputs"],
            outputs=rows["outputs"],
        )
    )


# --------------------------------------------------------------------------- #
# Hash diff (per-run pusher state)                                            #
# --------------------------------------------------------------------------- #


def content_hash(model: BaseModel) -> str:
    """``blake2b`` of the model's canonical JSON."""
    canonical = json.dumps(
        model.model_dump(mode="json", exclude_none=True), sort_keys=True, separators=(",", ":")
    )
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


def header_hash(header: LiveHeader) -> str:
    """Hash of the header's non-counter content: ``num_steps`` / ``num_measurements``
    and ``current_step_path`` alone do not trigger a header write (docs/41 §2.1)."""
    return content_hash(
        header.model_copy(
            update={
                "run": header.run.model_copy(update=dict.fromkeys(_HEADER_COUNTERS)),
                "current_step_path": None,
            }
        )
    )


REFRESH_AFTER_S = 24 * 3600.0
"""An unchanged doc is re-sent after this long so its ``expires_at`` stays ahead of
the TTL on runs longer than 48 h (docs/41 §3.1, §6.3)."""


class LiveSyncState(BaseModel):
    """What the bench believes the server holds for ONE run (docs/41 §3.1).

    ``sent`` maps ``row_id`` to content hash; it only changes in :meth:`commit`,
    which the caller invokes after a 2xx response, so a failed push leaves its docs
    dirty and the next push resends them."""

    model_config = ConfigDict(extra="forbid")

    sent: dict[str, str] = {}
    sent_at: dict[str, float] = {}
    header_sent: str | None = None
    seq: int = 0
    need_manifest: bool = True

    def request_resync(self) -> None:
        """The server asked for a resync: the next first push carries the manifest."""
        self.need_manifest = True

    def _next_seq(self, now_ns: int) -> int:
        self.seq = max(self.seq + 1, now_ns)
        return self.seq

    def build_pushes(
        self,
        run_id: str,
        detail: RunDetail,
        *,
        now: float,
        now_ns: int,
        force_header: bool = False,
    ) -> list[LivePush]:
        """Diff ``detail`` against what was sent and return the pushes to make:
        changed/new docs as ``upserts``, vanished docs as ``deletes``, the header when
        its non-counter hash changed (or ``force_header``, the lease), and the full
        ``manifest`` on the first push when one is owed. More than
        :data:`MAX_WRITES_PER_PUSH` writes (or a body near the cap) go out as
        sequential parts; the header and manifest ride the first part. Empty when
        there is nothing to send."""
        docset = to_live_docs(detail)
        return self.build_pushes_from(
            run_id,
            build_header(detail, truncated=docset.truncated),
            docset.docs,
            {row_id: content_hash(doc) for row_id, doc in docset.docs.items()},
            now=now,
            now_ns=now_ns,
            force_header=force_header,
        )

    def build_pushes_from(
        self,
        run_id: str,
        header: LiveHeader,
        docs: dict[str, LiveDoc],
        hashes: dict[str, str],
        *,
        now: float,
        now_ns: int,
        force_header: bool = False,
    ) -> list[LivePush]:
        """:meth:`build_pushes` over docs already split, with their content hashes
        already computed (the incremental projection caches both per doc, so a push
        costs O(docs) dict lookups, not O(rows))."""
        send_header = force_header or header_hash(header) != self.header_sent

        upserts: list[LiveDoc] = []
        for row_id, doc in docs.items():
            stale = now - self.sent_at.get(row_id, now) >= REFRESH_AFTER_S
            if self.sent.get(row_id) != hashes[row_id] or stale:
                upserts.append(doc)
        deletes = sorted(set(self.sent) - set(docs))
        manifest = sorted(docs) if self.need_manifest else None
        if not (upserts or deletes or send_header or manifest is not None):
            return []

        pushes: list[LivePush] = []
        writes: list[LiveDoc | str] = [*upserts, *deletes]
        first = True
        while writes or first:
            part: list[LiveDoc | str] = []
            budget = MAX_WRITES_PER_PUSH - (1 if first and send_header else 0)
            body = 0
            while writes and len(part) < budget:
                item = writes[0]
                size = len(item.model_dump_json()) if isinstance(item, LiveDoc) else len(item) + 4
                if part and body + size > MAX_BODY_BYTES:
                    break
                body += size
                part.append(writes.pop(0))
            pushes.append(
                LivePush(
                    run_id=run_id,
                    seq=self._next_seq(now_ns),
                    header=header if first and send_header else None,
                    upserts=[w for w in part if isinstance(w, LiveDoc)],
                    deletes=[w for w in part if isinstance(w, str)],
                    manifest=manifest if first else None,
                )
            )
            first = False
        return pushes

    def commit(self, push: LivePush, *, now: float) -> None:
        """Record a 2xx-acknowledged ``push`` as sent."""
        for doc in push.upserts:
            self.sent[doc.row_id] = content_hash(doc)
            self.sent_at[doc.row_id] = now
        for row_id in push.deletes:
            self.sent.pop(row_id, None)
            self.sent_at.pop(row_id, None)
        if push.header is not None:
            self.header_sent = header_hash(push.header)
        if push.manifest is not None:
            self.need_manifest = False
