"""Incremental projection of an executing run to live docs (docs/41 §3.1, M6).

Re-projecting the whole run on every push costs O(run) (~1.4 s for a 5,000-vector
sweep). This module projects only what changed since the last push:

* the accumulator reports which *partitions* are dirty — a step path's step-scope part
  (step rows, step-scope measurements) or one 16-point bucket of its sweep;
* each dirty partition is projected by the SAME row builders the at-rest projection
  uses (``_build_unified_rows_from_acc`` over ``EventAccumulator.partition``), and
  the SAME ``read_models.run_detail`` SQL — one call for all dirty partitions;
* the result is cached per partition as P3 rows, split to live docs with their content
  hashes, so the diff against what was sent is O(docs) dict lookups.

Three values are run-wide and cannot come out of a per-partition SQL run; they are
fixed up here and pinned to the whole-run projection by the parity tests
(``tests/test_data/test_live_projection.py``):

* ``index`` on measurement / IO rows — the run-wide per-name dense rank of
  ``(step_index, step_path, vector_index)`` (``measurement_projection.
  _occurrence_index_expr``);
* a step's ``measurement_count`` — its own plus its vectors' (across all buckets);
* the run's ``num_steps`` / ``num_measurements``.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from testerkit.data.backends._event_accumulator import VECTOR_BUCKET_WIDTH, EventAccumulator
from testerkit.data.backends.parquet import (
    _build_run_row_from_acc,
    _build_step_row_from_acc,
    _build_unified_rows_from_acc,
)
from testerkit.data.live_rows import LiveDoc, LiveHeader, content_hash, header_for, to_live_docs
from testerkit.data.read_models import (
    InputRow,
    MeasurementRow,
    OutputRow,
    RunDetail,
    RunDetailParts,
    RunRow,
    StepRow,
    VectorRow,
    run_detail,
    run_detail_parts,
)
from testerkit.data.schemas import _build_write_schema, table_from_rows

PartKey = tuple[str, int | None]
GHOST: PartKey = ("", -2)
"""The never-ran (planned but not executed) step rows — not tied to one step path."""

_Rank = tuple[int, str, int]
_StepKey = tuple[str, int, int | None]


def _ended_params(acc: EventAccumulator) -> tuple[Any, str | None]:
    """``(run_ended_at, run_outcome)`` for the builders: both ``None`` while the run is
    open; once ended, its ``RunEnded`` time and outcome (``aborted`` when the event
    carries none, as the materializer does)."""
    ended_at = acc.run_ended_at
    return ended_at, ((acc.run_outcome or "aborted") if ended_at is not None else None)


def _table(rows: list[dict[str, Any]]) -> Any:
    return table_from_rows(rows, _build_write_schema(rows))


def project_run(acc: EventAccumulator) -> RunDetail | None:
    """Project the WHOLE run to the P3 ``run_detail`` shapes — the same calls that
    produce the run Parquet and the server's ``/detail``, with the in-flight builder
    mode (docs/41 §3.1). ``None`` before ``RunStarted``. O(run): the reference the
    incremental :class:`LiveRunProjection` is tested against."""
    ended_at, outcome = _ended_params(acc)
    rows = _build_unified_rows_from_acc(acc, ended_at, outcome)
    return run_detail(_table(rows)) if rows else None


class _PartRows(BaseModel):
    """One partition's cached P3 rows."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    steps: list[StepRow] = Field(default_factory=list)
    vectors: list[VectorRow] = Field(default_factory=list)
    measurements: list[MeasurementRow] = Field(default_factory=list)
    inputs: list[InputRow] = Field(default_factory=list)
    outputs: list[OutputRow] = Field(default_factory=list)


def _step_key(row: StepRow | VectorRow) -> _StepKey:
    return (row.step_path or "", row.step_retry or 0, row.vector_outer_index)


def _part_of(path: str | None, vector_index: int | None) -> PartKey:
    return (path or "", None if vector_index is None else vector_index // VECTOR_BUCKET_WIDTH)


class LiveRunProjection:
    """Per-run incremental projection: :meth:`refresh` folds the accumulator's changes
    in, then :attr:`header`, :attr:`docs` and :attr:`hashes` describe the run now."""

    def __init__(self) -> None:
        self._rows: dict[PartKey, _PartRows] = {}
        self._docs: dict[PartKey, dict[str, LiveDoc]] = {}
        self._hashes: dict[PartKey, dict[str, str]] = {}
        self._truncated: set[PartKey] = set()
        self._vec_counts: dict[_StepKey, dict[int, int]] = {}
        self._ranks: dict[tuple[str, str | None], list[_Rank]] = {}
        self._pos_count: dict[tuple[tuple[str, str | None], _Rank], int] = {}
        self._ghost: list[dict[str, Any]] = []
        self._run: RunRow | None = None
        self.header: LiveHeader | None = None
        self.docs: dict[str, LiveDoc] = {}
        self.hashes: dict[str, str] = {}

    # -- projection --------------------------------------------------------

    def refresh(self, acc: EventAccumulator) -> bool:
        """Re-project what changed in ``acc`` since the last refresh. ``True`` when
        anything did (the header / docs may differ)."""
        parts, ghosts_dirty, run_dirty = acc.take_dirty_parts()
        ghost = acc.ghost_entries() if ghosts_dirty else self._ghost
        ghost_changed = ghost != self._ghost
        if not (parts or ghost_changed or run_dirty):
            return False
        ended_at, outcome = _ended_params(acc)
        run_row = _build_run_row_from_acc(acc, run_ended_at=ended_at, run_outcome=outcome)
        redo: set[PartKey] = set(parts)
        reorder = False

        need_run = run_dirty or self._run is None
        if parts or need_run:
            # Each query has a fixed planning cost: run only those that can have rows.
            want = {"vectors", "measurements", "inputs", "outputs"} if parts else set()
            if any(bucket is None for _, bucket in parts):
                want.add("steps")
            if need_run:
                want.add("runs")
            rows: list[dict[str, Any]] = [run_row] if run_row and need_run else []
            for path, bucket in sorted(parts, key=lambda p: (p[0], -1 if p[1] is None else p[1])):
                kind = "step" if bucket is None else "vector"
                sub = acc.partition(path, bucket)
                rows += [
                    r
                    for r in _build_unified_rows_from_acc(sub, ended_at, outcome)
                    if r["record_type"] == kind
                ]
            replaced, reorder = self._absorb(
                run_detail_parts(_table(rows), only=frozenset(want)), parts
            )
            redo |= replaced
            # a bucket's vectors change its step's measurement_count: rebuild that step doc
            redo |= {(path, None) for path, bucket in parts if bucket is not None}
            if any(bucket is not None for _, bucket in parts):
                redo.add(GHOST)  # the SQL joins a never-ran row of the same key to those counts
        if ghost_changed:
            rows = [
                r
                for entry in ghost
                if (
                    r := _build_step_row_from_acc(
                        acc, entry, run_ended_at=ended_at, run_outcome=outcome
                    )
                )
            ]
            self._rows[GHOST] = _PartRows(
                steps=run_detail_parts(_table(rows), only=frozenset({"steps"})).steps
                if rows
                else []
            )
            self._ghost = ghost
            redo.add(GHOST)
        self._assign_index(set(self._rows) if reorder else redo)
        self._rebuild(set(self._rows) if reorder else redo)
        return True

    def _absorb(self, detail: RunDetailParts, parts: set[PartKey]) -> tuple[set[PartKey], bool]:
        """Replace the dirty partitions' cached rows with ``detail``'s. Returns the
        replaced partitions and whether a run-wide rank shifted (see ``_rank``)."""
        if detail.runs:
            self._run = detail.runs[0]
        new = {part: _PartRows() for part in parts}
        for part in parts:
            if part[1] is not None:  # a re-projected bucket replaces its vector counts
                for key, by_bucket in self._vec_counts.items():
                    if key[0] == part[0]:
                        by_bucket.pop(part[1], None)
        call_sum: dict[_StepKey, int] = {}
        for vec in detail.vectors:
            part = _part_of(vec.step_path, vec.vector_index)
            new.setdefault(part, _PartRows()).vectors.append(vec)
            bucket = part[1]
            assert bucket is not None
            count = vec.measurement_count or 0
            by_bucket = self._vec_counts.setdefault(_step_key(vec), {})
            by_bucket[bucket] = by_bucket.get(bucket, 0) + count
            call_sum[_step_key(vec)] = call_sum.get(_step_key(vec), 0) + count
        for step in detail.steps:  # keep only the step's OWN count; doc build adds vectors'
            own = (step.measurement_count or 0) - call_sum.get(_step_key(step), 0)
            new.setdefault(_part_of(step.step_path, None), _PartRows()).steps.append(
                step.model_copy(update={"measurement_count": own})
            )
        for meas in detail.measurements:
            new.setdefault(
                _part_of(meas.step_path, meas.vector_index), _PartRows()
            ).measurements.append(meas)
        for inp in detail.inputs:
            new.setdefault(_part_of(inp.step_path, inp.vector_index), _PartRows()).inputs.append(
                inp
            )
        for out in detail.outputs:
            new.setdefault(_part_of(out.step_path, out.vector_index), _PartRows()).outputs.append(
                out
            )
        reorder = False
        for part, rows in new.items():
            reorder |= self._rank(rows, add=True)  # add first: unchanged positions stay put
            if part in self._rows:
                reorder |= self._rank(self._rows[part], add=False)
        self._rows.update(new)
        return set(new), reorder

    # -- run-wide `index` --------------------------------------------------

    @staticmethod
    def _rank_items(rows: _PartRows) -> list[tuple[tuple[str, str | None], _Rank, Any]]:
        def pos(r: MeasurementRow | InputRow | OutputRow) -> _Rank:
            vi = r.vector_index
            step_index = -1 if r.step_index is None else r.step_index
            return (step_index, r.step_path or "", -1 if vi is None else vi)

        return (
            [(("m", r.measurement_name), pos(r), r) for r in rows.measurements]
            + [(("i", r.name), pos(r), r) for r in rows.inputs]
            + [(("o", r.name), pos(r), r) for r in rows.outputs]
        )

    def _rank(self, rows: _PartRows, *, add: bool) -> bool:
        """Add (or remove) ``rows``' positions to the per-name sorted position lists.
        ``True`` when that shifted the rank of a position already present (an
        out-of-order arrival or a vanished position), so cached ranks are stale."""
        shifted = False
        for key, pos, _ in self._rank_items(rows):
            ranks = self._ranks.setdefault(key, [])
            count = self._pos_count.get((key, pos), 0) + (1 if add else -1)
            i = bisect_left(ranks, pos)
            if add and count == 1:
                shifted |= i != len(ranks)
                ranks.insert(i, pos)
            elif not add and count == 0:
                shifted |= i != len(ranks) - 1
                del ranks[i]
            if count:
                self._pos_count[(key, pos)] = count
            else:
                self._pos_count.pop((key, pos), None)
        return shifted

    def _assign_index(self, parts: set[PartKey]) -> None:
        """Set the run-wide per-name occurrence ``index`` (the dense rank of the row's
        position among that name's positions) on the given partitions' rows."""
        for part in parts:
            if part in self._rows:
                for key, pos, row in self._rank_items(self._rows[part]):
                    row.index = bisect_left(self._ranks[key], pos)

    # -- docs ---------------------------------------------------------------

    def _rebuild(self, parts: set[PartKey]) -> None:
        assert self._run is not None
        for part in parts:
            rows = self._rows.get(part)
            if rows is None:
                continue
            steps = [
                s.model_copy(
                    update={
                        "measurement_count": (s.measurement_count or 0)
                        + sum(self._vec_counts.get(_step_key(s), {}).values())
                    }
                )
                for s in rows.steps
            ]
            docset = to_live_docs(
                RunDetail(
                    run=self._run,
                    steps=steps,
                    vectors=rows.vectors,
                    measurements=rows.measurements,
                    inputs=rows.inputs,
                    outputs=rows.outputs,
                )
            )
            self._docs[part] = docset.docs
            self._hashes[part] = {r: content_hash(d) for r, d in docset.docs.items()}
            (self._truncated.add if docset.truncated else self._truncated.discard)(part)
        order = sorted(
            (p for p in self._docs if p != GHOST),
            key=lambda p: (p[0], -1 if p[1] is None else p[1]),
        )
        if GHOST in self._docs:
            order.append(GHOST)  # last, like a never-ran row sorting after the ran ones
        self.docs = {r: d for p in order for r, d in self._docs[p].items()}
        self.hashes = {r: h for p in order for r, h in self._hashes[p].items()}
        rows_all = list(self._rows.values())
        run = self._run.model_copy(
            update={
                "num_steps": sum(len(r.steps) for r in rows_all),
                "num_measurements": sum(len(r.measurements) for r in rows_all),
            }
        )
        self.header = header_for(
            run, (s for r in rows_all for s in r.steps), truncated=bool(self._truncated)
        )
