"""Pure in-memory event projection — no I/O, no parquet dependencies.

:class:`EventAccumulator` is the single canonical state machine that
projects the litmus event stream into row-shaped state. Both the test
runner's :class:`~litmus.data.backends.parquet.ParquetSubscriber` (I/O
destination: disk) and the runs daemon's live overlay (I/O destination:
in-memory UNION view) use this class so their projections never drift.

Lives in its own module so the daemon can import it without pulling in
the full parquet/subscribers/exporters stack.
"""

from __future__ import annotations

from typing import Any

from litmus.data.backends._row_helpers import (
    RunParquetRow,
    _append_not_started,
    _to_datetime,
    run_context_from_run_started,
    step_entry_dict,
    vector_entry_dict,
)
from litmus.data.events import (
    InstrumentConnected,
    InstrumentReserved,
    MeasurementRecorded,
    Observation,
    RunEnded,
    RunStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
    VectorEnded,
    VectorStarted,
)


def _safe_str(value: Any) -> str | None:
    """Return ``str(value)`` or ``None`` if *value* is falsy."""
    return str(value) if value else None


def _pack_dynamic_attrs(inputs: dict[str, Any], outputs: dict[str, Any]) -> dict[str, str | None]:
    """Build the dynamic_attrs MAP from inputs/outputs lane dicts."""
    return {
        **{f"in_{k}": _safe_str(v) for k, v in inputs.items()},
        **{f"out_{k}": _safe_str(v) for k, v in outputs.items()},
    }


def _step_key(event: Any) -> tuple[str, int, int]:
    """Stable accumulator key for a StepStarted / StepEnded event.

    Uses ``step_path`` (the canonical hierarchical id) when set;
    falls back to ``step_name`` otherwise so direct-API callers
    (and tests) that emit events without populating step_path
    still get distinct keys per step. ``step_retry`` (the outer item
    attempt) makes each rerun its own execution row instead of
    overwriting the prior attempt (the de-fuse). ``vector_index`` is
    the ENCLOSING iteration this step ran under — null/0 at top level
    (all variants collapse to one logical step entry), 0..N for methods
    nested under a swept class (each enclosing iteration = distinct row).
    """
    path = event.step_path or event.step_name or ""
    return (path, getattr(event, "retry", 0) or 0, event.vector_index)


def _vector_key(event: Any) -> tuple[str, int, int]:
    """Stable accumulator key for a VectorStarted / VectorEnded event.

    Keyed on ``(step_path, vector_index, retry)`` — an in-body loop shares
    one ``step_path`` across iterations, so ``vector_index`` distinguishes
    iterations and ``retry`` distinguishes re-executions of the same vector.
    """
    path = event.step_path or event.step_name or ""
    return (path, event.vector_index, getattr(event, "retry", 0) or 0)


def _end_overrides_start(start: Any, end: Any, attr: str) -> dict[str, Any]:
    """Resolve an inputs-side lane: End overrides Start, Start is the in-flight fallback.

    A finished block (End event present) carries the post-``configure()`` snapshot
    and wins; while in-flight (no End yet) the overlay reads Start.
    """
    if end is not None and getattr(end, attr, None):
        return dict(getattr(end, attr))
    if start is not None and getattr(start, attr, None):
        return dict(getattr(start, attr))
    return {}


def _measurement_event_struct(event: Any) -> dict[str, Any]:
    """Encode a MeasurementRecorded event into the nested measurement struct.

    Field order/names match ``_row_helpers.build_measurement_struct`` (and
    ``schemas._MEASUREMENT_STRUCT``) so the streaming write and the offline
    write produce identical nested structs.
    """
    return {
        "name": event.measurement_name,
        "value": event.value,
        "unit": event.unit,
        "outcome": event.outcome,
        "timestamp": event.measurement_timestamp,
        "limit_low": event.limit_low,
        "limit_high": event.limit_high,
        "limit_nominal": event.limit_nominal,
        "limit_comparator": event.limit_comparator,
        "characteristic_id": event.characteristic_id,
        "spec_ref": event.spec_ref,
        "uut_pin": event.uut_pin,
        "fixture_connection": event.fixture_connection,
        "instrument_name": event.instrument_name,
        "instrument_resource": event.instrument_resource,
        "instrument_channel": event.instrument_channel,
    }


class EventAccumulator:
    """Pure projection of run events into row state — no I/O.

    The single canonical projection function from the litmus event
    stream into the parquet row shape. One per in-flight run.

    Accumulates ``RunStarted`` / ``InstrumentConnected`` /
    ``StepsDiscovered`` / ``StepStarted`` / ``MeasurementRecorded`` /
    ``StepEnded`` events into in-memory state. ``RunEnded`` is
    handled by subclasses (it's the trigger for the parquet
    write) — the base class doesn't act on it.

    Snapshot methods (``snapshot_run_row``, ``snapshot_step_rows``,
    ``snapshot_measurement_rows``) materialize the current state
    as the same dict shape ``ParquetSubscriber`` writes to disk,
    but without writing anything. Used by the runs daemon to
    surface in-flight runs in queries via an in-memory overlay.

    The "one applier, multiple destinations" pattern from
    event-sourcing / materialized-view design (Prometheus head
    block, Materialize, RisingWave, CQRS read models): both the
    test runner's parquet writer (``ParquetSubscriber``) and the
    runs daemon's live overlay use this same projection so the
    finalized parquet and the in-flight overlay can never drift.

    Snapshot return conventions:

    * ``snapshot_run_row()`` returns ``None`` when no ``RunStarted``
      has been seen (there is no run to project). This is intentionally
      ``Optional[dict]`` — a singular row either exists or it doesn't.
    * ``snapshot_step_rows()`` and ``snapshot_measurement_rows()``
      return ``[]`` for the same condition (plural methods follow
      the empty-sequence convention rather than returning ``None``).
    """

    def __init__(self) -> None:
        self._run_started: Any = None  # RunStarted event (run context)
        self._instruments: list[Any] = []  # InstrumentConnected events
        self._reservations: list[Any] = []  # InstrumentReserved events
        self._measurement_events: list[Any] = []  # MeasurementRecorded events
        # ``observe()`` events accumulate here so a vector's observations
        # can ride on its step/vector record's outputs lanes.
        self._observation_events: list[Any] = []
        # Step events keyed by (step_path, step_retry, vector_index) so each
        # sweep variant — each class-container iteration — AND each rerun
        # (step_retry) gets its own entry. ``step_index`` is unique per
        # logical-step within its parent bucket but COLLIDES across parents
        # (a class container at root step_index=0 and its first method at
        # class-bucket step_index=0 would clobber each other under a
        # (step_index, vector_index) key). step_path is unique end-to-end
        # per step; step_retry de-fuses reruns (no overwrite).
        self._step_starts: dict[tuple[str, int, int], Any] = {}
        self._step_ends: dict[tuple[str, int, int], Any] = {}
        # In-body loop vectors (Mode 2) keyed by (step_path, vector_index,
        # retry). Present ONLY when VectorStarted/VectorEnded were emitted;
        # their presence is the Mode-2 signal that produces ``vector`` rows.
        self._vector_starts: dict[tuple[str, int, int], Any] = {}
        self._vector_ends: dict[tuple[str, int, int], Any] = {}
        self._run_ended: Any = None  # RunEnded event (None for in-flight)
        self._collected_items: list[dict[str, str | int | None]] = []
        # node_id → markers, populated when StepsDiscovered arrives so
        # ``_build_row`` can stamp step_markers on every measurement row
        # without rebuilding the lookup per measurement.
        self._markers_by_node: dict[str, str | None] = {}

    def on_event(self, event: Any) -> None:
        """Accumulate one event into in-memory state. No I/O."""
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, InstrumentConnected):
            self._instruments.append(event)
        elif isinstance(event, InstrumentReserved):
            self._reservations.append(event)
        elif isinstance(event, StepsDiscovered):
            self._collected_items = event.items
            markers: dict[str, str | None] = {}
            for ci in event.items:
                nid = ci.get("node_id")
                if isinstance(nid, str) and nid:
                    m = ci.get("markers")
                    markers[nid] = m if isinstance(m, str) or m is None else str(m)
            self._markers_by_node = markers
        elif isinstance(event, StepStarted):
            self._step_starts[_step_key(event)] = event
        elif isinstance(event, VectorStarted):
            self._vector_starts[_vector_key(event)] = event
        elif isinstance(event, VectorEnded):
            self._vector_ends[_vector_key(event)] = event
        elif isinstance(event, MeasurementRecorded):
            self._measurement_events.append(event)
        elif isinstance(event, Observation):
            self._observation_events.append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[_step_key(event)] = event
        elif isinstance(event, RunEnded):
            self._run_ended = event

    # ------------------------------------------------------------------
    # Snapshot — materialize current state as row dicts (no I/O)
    # ------------------------------------------------------------------

    def snapshot_run_row(self) -> dict[str, Any] | None:
        """Return a single dict matching the runs daemon's ``runs`` row shape.

        ``None`` if no ``RunStarted`` has been seen yet (nothing to
        project). For in-flight runs ``ended_at`` and ``outcome`` are
        ``None``; those fields populate when ``RunEnded`` arrives.
        """
        s = self._run_started
        if not s:
            return None
        ended_at = self._run_ended.occurred_at if self._run_ended else None
        outcome = self._run_ended.outcome if self._run_ended else None
        return {
            "run_id": _safe_str(s.run_id),
            "session_id": _safe_str(s.session_id),
            "slot_id": s.slot_id,
            "uut_serial_number": s.uut_serial_number,
            "uut_part_number": s.uut_part_number,
            "uut_lot_number": s.uut_lot_number,
            "station_id": s.station_id,
            "station_name": s.station_name,
            "station_hostname": s.station_hostname,
            "fixture_id": s.fixture_id,
            "outcome": outcome,
            "started_at": s.occurred_at,
            "ended_at": ended_at,
            "num_measurements": sum(
                1 for e in self._measurement_events if e.measurement_name is not None
            ),
            "num_steps": len(set(self._step_starts) | set(self._step_ends)),
            "test_phase": s.test_phase,
            "part_id": s.part_id,
            "operator_id": s.operator_id,
            "project_name": s.project_name,
            "file_path": None,
        }

    def snapshot_step_rows(self) -> list[dict[str, Any]]:
        """Return step rows matching the runs daemon's ``steps`` row shape."""
        s = self._run_started
        if not s:
            return []
        ended_at = self._run_ended.occurred_at if self._run_ended else None
        outcome = self._run_ended.outcome if self._run_ended else None
        rows: list[dict[str, Any]] = []
        for entry in self._build_step_results_from_events():
            started_at = _to_datetime(entry.get("started_at"))
            entry_ended_at = _to_datetime(entry.get("ended_at"))
            duration_s = (
                (entry_ended_at - started_at).total_seconds()
                if started_at and entry_ended_at
                else None
            )
            rows.append(
                {
                    "run_id": _safe_str(s.run_id),
                    "step_index": entry.get("index"),
                    "session_id": _safe_str(s.session_id),
                    "slot_id": s.slot_id,
                    "step_name": entry.get("name"),
                    "step_path": entry.get("step_path"),
                    "vector_index": entry.get("vector_index", 0),
                    "outcome": entry.get("outcome"),
                    "started_at": started_at,
                    "ended_at": entry_ended_at,
                    "duration_s": duration_s,
                    "step_retry": entry.get("step_retry", 0),
                    "measurement_count": entry.get("measurement_count", 0),
                    "markers": entry.get("markers"),
                    "uut_serial_number": s.uut_serial_number,
                    "station_id": s.station_id,
                    "file_path": None,
                    "run_outcome": outcome,
                    "run_ended_at": ended_at,
                    "dynamic_attrs": _pack_dynamic_attrs(
                        entry.get("inputs") or {},
                        entry.get("outputs") or {},
                    ),
                }
            )
        for entry in self._build_vector_results_from_events():
            rows.append(
                {
                    "run_id": _safe_str(s.run_id),
                    "step_index": entry.get("index"),
                    "session_id": _safe_str(s.session_id),
                    "slot_id": s.slot_id,
                    "step_name": entry.get("name"),
                    "step_path": entry.get("step_path"),
                    "vector_index": entry.get("vector_index"),
                    # Daemon aggregation FILTERs step-level columns to the step record.
                    "outcome": None,
                    "started_at": None,
                    "ended_at": None,
                    "duration_s": None,
                    "step_retry": entry.get("step_retry", 0),
                    "measurement_count": len(entry.get("measurements") or []),
                    "markers": None,
                    "uut_serial_number": s.uut_serial_number,
                    "station_id": s.station_id,
                    "file_path": None,
                    "run_outcome": outcome,
                    "run_ended_at": ended_at,
                    "dynamic_attrs": _pack_dynamic_attrs(
                        entry.get("inputs") or {},
                        entry.get("outputs") or {},
                    ),
                }
            )
        return rows

    def snapshot_measurement_rows(self) -> list[dict[str, Any]]:
        """Return flat measurement-fact rows matching the daemon's UNNEST."""
        if not self._run_started:
            return []
        ended_at = self._run_ended.occurred_at if self._run_ended else None
        outcome = self._run_ended.outcome if self._run_ended else None
        vectors_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
        for entry in self._build_vector_results_from_events():
            key = (
                entry.get("step_path") or "",
                entry.get("vector_index", 0),
                entry.get("retry", 0),
            )
            vectors_by_key[key] = entry
        # AT-REST vector_index (may be None) must match the step entry's key.
        steps_by_key: dict[tuple[str, int, int | None], dict[str, Any]] = {}
        for entry in self._build_step_results_from_events():
            key = (
                entry.get("step_path") or "",
                entry.get("step_retry", 0) or 0,
                entry.get("vector_index"),
            )
            steps_by_key[key] = entry
        looped = self._looped_keys()
        rows: list[dict[str, Any]] = []
        for event in self._measurement_events:
            row = self._build_row(event)
            row["run_ended_at"] = ended_at
            row["run_outcome"] = outcome
            path = event.step_path or event.step_name or ""
            if (path, event.vector_index) in looped:
                entry = vectors_by_key.get(
                    (path, event.vector_index, event.retry or 0)
                ) or vectors_by_key.get((path, event.vector_index, 0))
                in_lanes = (entry.get("inputs") if entry else None) or {}
                out_lanes = (entry.get("outputs") if entry else None) or {}
                row["dynamic_attrs"] = _pack_dynamic_attrs(in_lanes, out_lanes)
                row["vector_retry"] = entry.get("retry", 0) if entry else 0
                row["vector_outcome"] = entry.get("outcome") if entry else None
                row["step_outcome"] = None
                if entry is not None:
                    row["step_started_at"] = _to_datetime(entry.get("step_started_at"))
                    row["step_ended_at"] = _to_datetime(entry.get("step_ended_at"))
            else:
                # lookup_vi reconstructs the step entry's AT-REST key (None for top-level steps).
                lookup_vi = event.vector_index if self._parent_emitted_vectors(path) else None
                step_entry = steps_by_key.get(
                    (path, getattr(event, "step_retry", 0) or 0, lookup_vi)
                )
                in_lanes = (step_entry.get("inputs") if step_entry else None) or {}
                out_lanes = (step_entry.get("outputs") if step_entry else None) or {}
                row["dynamic_attrs"] = _pack_dynamic_attrs(in_lanes, out_lanes)
                row["vector_index"] = None
                row["vector_retry"] = None
                row["vector_outcome"] = None
                row["step_outcome"] = step_entry.get("outcome") if step_entry else None
                if step_entry is not None:
                    row["step_started_at"] = _to_datetime(step_entry.get("started_at"))
                    row["step_ended_at"] = _to_datetime(step_entry.get("ended_at"))
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Pure projection helpers — used by both snapshot and parquet write
    # ------------------------------------------------------------------

    def _build_instrument_records(self) -> list[dict[str, Any]]:
        """Build instrument records from cached InstrumentConnected events."""
        return [
            {
                "name": inst.role,
                "id": inst.instrument_id,
                "driver": inst.driver,
                "resource": inst.resource,
                "protocol": inst.protocol,
                "manufacturer": inst.manufacturer,
                "model": inst.model,
                "serial_number": inst.serial,
                "firmware": inst.firmware,
                "cal_due": inst.cal_due,
                "cal_last": inst.cal_last,
                "cal_certificate": inst.cal_certificate,
                "cal_lab": inst.cal_lab,
                "mocked": inst.mocked,
            }
            for inst in self._instruments
        ]

    def _reserved_instrument_lookups(
        self,
    ) -> tuple[dict[tuple[int, int], set[str]], dict[str, dict[str, Any]]]:
        reserved_by_step: dict[tuple[int, int], set[str]] = {}
        structs_by_role: dict[str, dict[str, Any]] = {}
        for ev in self._reservations:
            if ev.step_index is not None:
                reserved_by_step.setdefault(
                    (ev.step_index, ev.step_retry if ev.step_retry is not None else 0),
                    set(),
                ).add(ev.role)
            structs_by_role.setdefault(
                ev.role,
                {
                    "name": ev.role,
                    "id": ev.instrument_id,
                    "driver": None,
                    "resource": ev.resource,
                    "protocol": None,
                    "manufacturer": None,
                    "model": None,
                    "serial_number": None,
                    "firmware": None,
                    "cal_due": None,
                    "cal_last": None,
                    "cal_certificate": None,
                    "cal_lab": None,
                    "mocked": False,
                },
            )
        for rec in self._build_instrument_records():
            structs_by_role[rec["name"]] = rec
        return reserved_by_step, structs_by_role

    @staticmethod
    def _min_retry_match(
        cache: dict[tuple[str, int, int], Any], step_path: str, vector_index: int
    ) -> Any:
        """Lowest-retry cached step event for a (step_path, vector_index).

        Retry-invariant identity lookup: the de-fuse keys steps by
        ``(step_path, step_retry, vector_index)``, but a measurement event's
        ``retry`` is the inner vector retry, not ``step_retry``. The identity
        fields a measurement fact reads off the step (node_id, parent,
        module/file/class/function) are the same across reruns, so any matching
        attempt serves; prefer the lowest retry.
        """
        retries = [r for (p, r, v) in cache if p == step_path and v == vector_index]
        return cache.get((step_path, min(retries), vector_index)) if retries else None

    @staticmethod
    def _step_event_at(
        cache: dict[tuple[str, int, int], Any],
        step_path: str,
        step_retry: int,
        vector_index: int,
    ) -> Any:
        """Cached step event for a vector's OWN ``(step_path, step_retry)``.

        An in-body iteration vector reads its enclosing step's identity and
        timing; the de-fuse keys steps by ``(step_path, step_retry,
        vector_index)``, so a rerun has a distinct StepStarted/StepEnded per
        attempt. Resolving by the vector's own ``step_retry`` makes attempt 1's
        iteration vectors read attempt 1's step span — not attempt 0's, which
        the retry-invariant :meth:`_min_retry_match` would return. The enclosing
        leaf step's ``vector_index`` is 0 for the common Mode-2 step, so the
        ``step_retry``-exact lookup falls through to it; the final
        retry-invariant match preserves behaviour for parametrized Mode-2 steps
        whose StepStarted sits at a nonzero index.
        """
        return (
            cache.get((step_path, step_retry, vector_index))
            or cache.get((step_path, step_retry, 0))
            or EventAccumulator._min_retry_match(cache, step_path, vector_index)
            or EventAccumulator._min_retry_match(cache, step_path, 0)
        )

    def _step_start_for(self, step_path: str, vector_index: int) -> Any:
        return self._min_retry_match(self._step_starts, step_path, vector_index)

    def _step_end_for(self, step_path: str, vector_index: int) -> Any:
        return self._min_retry_match(self._step_ends, step_path, vector_index)

    def _looped_keys(self) -> set[tuple[str, int]]:
        """``(step_path, vector_index)`` pairs that ran an in-body vector loop."""
        return {(k[0], k[1]) for k in (set(self._vector_starts) | set(self._vector_ends))}

    def _step_start_field(self, step_path: str, vector_index: int, attr: str) -> Any:
        """Get a field from the cached StepStarted event, or None."""
        start = self._step_start_for(step_path, vector_index)
        return getattr(start, attr, None) if start else None

    def _partition_measurements(
        self,
    ) -> dict[tuple[str, int, int], list[dict[str, Any]]]:
        """Group measurement structs by enclosing vector, full-identity keyed.

        Returns ``by_vector`` keyed ``(step_path, vector_index, vector_retry)``
        for measurements that landed inside an active vector (Mode-2 in-body
        or Mode-1/class-outer outer vector). Measurements recorded with no own
        active vector (step-scope) carry the enclosing iteration index on the
        event but are NOT in ``_vector_starts`` for their step_path — the
        accumulator assigns them to the step row instead (see
        ``_build_step_results_from_events``).
        """
        by_vector: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        looped = self._looped_keys()
        for e in self._measurement_events:
            path = e.step_path or e.step_name or ""
            struct = _measurement_event_struct(e)
            if (path, e.vector_index) in looped:
                by_vector.setdefault((path, e.vector_index, e.retry or 0), []).append(struct)
        return by_vector

    def _build_vector_results_from_events(self) -> list[dict[str, Any]]:
        """Build vector manifest entries from VectorStarted/VectorEnded.

        One entry per emitted vector — every sweep point emits these events:
        function ``@parametrize``, class-outer ``litmus_sweeps``, and in-body
        ``vectors`` / ``run_vector`` alike. A non-swept step emits none, so it
        contributes no vector rows (its data rides the ``step`` record).

        One entry per ``(step_path, vector_index, retry)`` execution. The
        enclosing leaf step's identity (node_id / file / class / function /
        timing) is sourced from its StepStarted when present.
        """
        by_iteration = self._partition_measurements()
        reserved_by_step, structs_by_role = self._reserved_instrument_lookups()
        entries: list[dict[str, Any]] = []
        keys = sorted(
            set(self._vector_starts) | set(self._vector_ends),
            key=lambda k: (k[1], k[2], k[0]),
        )
        # vector_retry = the 0-based occurrence ordinal of (step_path,
        # vector_index) across the whole run — a step rerun AND an in-body retry
        # both re-execute the point and both count (cause is irrelevant). It is
        # sourced at emit (RunScope.next_vector_occurrence stamps it onto
        # VectorStarted.retry) and rides the event as ``key[2]`` here, so a step
        # rerun's vectors are DISTINCT keys (not fused) and the inflight overlay
        # and materialized parquet — both reading this one builder — agree.
        for key in keys:
            path, vec, retry = key
            start = self._vector_starts.get(key)
            end = self._vector_ends.get(key)
            ref = start or end
            step_retry_v = getattr(ref, "step_retry", 0) or 0 if ref else 0
            step_start = self._step_event_at(self._step_starts, path, step_retry_v, vec)
            step_end = self._step_event_at(self._step_ends, path, step_retry_v, vec)
            node_id = getattr(ref, "node_id", None) or (step_start.node_id if step_start else None)
            inputs = _end_overrides_start(start, end, "inputs")
            outputs = dict(end.outputs) if end and getattr(end, "outputs", None) else {}
            input_units = _end_overrides_start(start, end, "input_units")
            output_units = (
                dict(end.output_units) if end and getattr(end, "output_units", None) else {}
            )
            output_pins = dict(end.output_pins) if end and getattr(end, "output_pins", None) else {}
            step_idx_v = ref.step_index if ref else 0
            v_roles = reserved_by_step.get((step_idx_v, step_retry_v), set())
            v_instruments = [structs_by_role[r] for r in v_roles if r in structs_by_role]
            entries.append(
                vector_entry_dict(
                    index=step_idx_v,
                    name=ref.step_name if ref else "",
                    node_id=node_id,
                    file=step_start.file if step_start else None,
                    function=step_start.function if step_start else None,
                    class_name=step_start.class_name if step_start else None,
                    module=step_start.module if step_start else None,
                    step_path=ref.step_path if ref else path,
                    markers=self._markers_by_node.get(node_id) if node_id else None,
                    step_started_at=step_start.occurred_at if step_start else None,
                    step_ended_at=step_end.occurred_at if step_end else None,
                    vector_index=vec,
                    retry=retry,
                    step_retry=step_retry_v,
                    outcome=end.outcome if end else None,
                    started_at=start.occurred_at if start else None,
                    ended_at=end.occurred_at if end else None,
                    inputs=inputs,
                    outputs=outputs,
                    input_units=input_units,
                    output_units=output_units,
                    output_pins=output_pins,
                    measurements=by_iteration.get(key, []),
                    instrument_records=v_instruments,
                )
            )
        return entries

    def _build_row(self, event: Any) -> dict[str, Any]:
        """Denormalize a MeasurementRecorded event into a flat row dict."""
        idx = event.step_index
        vec = event.vector_index
        path = event.step_path or event.step_name or ""
        start = self._step_start_for(path, vec)
        end = self._step_end_for(path, vec)
        node_id = start.node_id if start else None
        row = RunParquetRow(
            record_type="vector",
            **run_context_from_run_started(self._run_started, event, include_env=True),
            step_name=event.step_name,
            step_index=idx,
            step_path=event.step_path or event.step_name,
            step_started_at=start.occurred_at if start else None,
            step_ended_at=end.occurred_at if end else None,
            step_node_id=node_id,
            step_module=self._step_start_field(path, vec, "module"),
            step_file=self._step_start_field(path, vec, "file"),
            step_class=self._step_start_field(path, vec, "class_name"),
            step_function=self._step_start_field(path, vec, "function"),
            step_markers=self._markers_by_node.get(node_id) if node_id else None,
            step_outcome=end.outcome if end else None,
            vector_index=vec,
            vector_retry=event.retry,
            step_retry=getattr(event, "step_retry", 0) or 0,
            measurement_name=event.measurement_name,
            measurement_timestamp=event.measurement_timestamp,
            measurement_value=event.value,
            measurement_unit=event.unit,
            measurement_outcome=event.outcome,
            limit_low=event.limit_low,
            limit_high=event.limit_high,
            limit_nominal=event.limit_nominal,
            limit_comparator=event.limit_comparator,
            characteristic_id=event.characteristic_id,
            spec_ref=event.spec_ref,
            uut_pin=event.uut_pin,
            fixture_connection=event.fixture_connection,
            instrument_name=event.instrument_name,
            instrument_resource=event.instrument_resource,
            instrument_channel=event.instrument_channel,
            run_outcome=None,
            # v2: a measurement references (does not copy) its vector — the
            # in/out conditions live on the (scope or in-body) vector record;
            # the EAV join resolves them by the shared vector key.
            inputs={},
            outputs={},
            instruments=self._build_instrument_records(),
        )
        flat = row.to_flat_dict()
        flat["record_type"] = "measurement"
        flat.pop("measurements", None)
        return flat

    def _build_step_results_from_events(self) -> list[dict[str, Any]]:
        """Build step manifest from cached StepStarted/StepEnded events.

        Each entry corresponds to one ``(step_path, step_retry, vector_index)``
        execution. A swept step running 4 vectors produces 4 manifest entries
        with the same ``step_path`` but ``vector_index`` 0..3; a rerun adds a
        fresh entry at ``step_retry`` N+1 (the de-fuse — reruns are distinct
        rows, never fused).

        Partially-run sweeps (some but not all planned vectors ran)
        also produce manifest entries for the unrun vectors with
        ``outcome=None`` — surfaced via ``_append_not_started`` from the
        collected items that never appeared in the executed events.
        """
        manifest: list[dict[str, Any]] = []
        executed_node_ids: set[str] = set()
        executed_vectors: set[tuple[str, int]] = set()

        looped = self._looped_keys()
        # Step-scope measurements only; swept steps' vector measurements count on vector rows.
        step_scope_meas: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for e in self._measurement_events:
            path = e.step_path or e.step_name or ""
            key = (path, getattr(e, "step_retry", 0) or 0, e.vector_index)
            if (path, e.vector_index) not in looped:
                step_scope_meas.setdefault(key, []).append(_measurement_event_struct(e))

        # Observations per vector key — merged into the step entry's outputs
        # so the step record carries the vector's observations on its lanes.
        obs_by_key: dict[tuple[str, int], dict[str, Any]] = {}
        obs_units_by_key: dict[tuple[str, int], dict[str, str]] = {}
        obs_pins_by_key: dict[tuple[str, int], dict[str, str]] = {}
        for ev in self._observation_events:
            if ev.name.startswith("_"):
                continue
            okey = (ev.step_path or ev.step_name or "", ev.vector_index)
            obs_by_key.setdefault(okey, {}).setdefault(ev.name, ev.value)
            if getattr(ev, "unit", None):
                obs_units_by_key.setdefault(okey, {}).setdefault(ev.name, ev.unit)
            if getattr(ev, "uut_pin", None) is not None:
                obs_pins_by_key.setdefault(okey, {}).setdefault(ev.name, ev.uut_pin)

        # Sort keys by the producer-assigned (step_index, vector_index, retry)
        # so the resulting manifest preserves execution order regardless of the
        # alphabetical position of step_path. Falls back to the key itself
        # for events that didn't set step_index (zero-default).
        def _sort_key(k: tuple[str, int, int]) -> tuple[int, int, int, str]:
            ev = self._step_starts.get(k) or self._step_ends.get(k)
            step_index = getattr(ev, "step_index", 0) if ev else 0
            # k = (path, step_retry, vector_index)
            return (step_index, k[2], k[1], k[0])

        reserved_by_step, structs_by_role = self._reserved_instrument_lookups()
        all_keys = sorted(set(self._step_starts) | set(self._step_ends), key=_sort_key)
        emitted_step_keys: set[tuple[str, int, int]] = set()
        for key in all_keys:
            path, step_retry, vec = key
            start = self._step_starts.get(key)
            end = self._step_ends.get(key)
            node_id = start.node_id if start else None
            if node_id:
                executed_node_ids.add(node_id)
            # ``executed_vectors`` is keyed by (step_path, vector_index) so
            # _append_not_started can correctly identify which CIs already ran
            # — pytest parametrize variants share one logical step_path but
            # have distinct node_ids, so keying by node_id misses cross-CI
            # matches.
            executed_vectors.add((path, vec))
            emitted_step_keys.add(key)
            step_meas = step_scope_meas.get((path, step_retry, vec), [])
            entry = self._build_step_entry(
                key,
                start,
                end,
                len(step_meas),
                obs_by_key.get((path, vec), {}),
                obs_units_by_key.get((path, vec), {}),
                obs_pins_by_key.get((path, vec), {}),
                step_measurements=step_meas,
            )
            s_idx = entry.get("index", 0)
            s_retry = entry.get("step_retry", 0) or 0
            s_roles = reserved_by_step.get((s_idx, s_retry), set())
            entry["instrument_records"] = [
                structs_by_role[r] for r in s_roles if r in structs_by_role
            ]
            manifest.append(entry)

        # Orphan: no StepStarted/StepEnded; synthesize minimal so measurements are never dropped.
        for mkey, structs in step_scope_meas.items():
            if mkey in emitted_step_keys:
                continue
            path, step_retry, vec = mkey
            m_event = next(
                (
                    e
                    for e in self._measurement_events
                    if (e.step_path or e.step_name or "") == path
                    and e.vector_index == vec
                    and (getattr(e, "step_retry", 0) or 0) == step_retry
                ),
                None,
            )
            executed_vectors.add((path, vec))
            manifest.append(
                step_entry_dict(
                    index=m_event.step_index if m_event else 0,
                    name=(m_event.step_name if m_event else "") or "",
                    node_id=None,
                    file=None,
                    function=None,
                    class_name=None,
                    module=None,
                    step_path=path,
                    description=None,
                    markers=None,
                    outcome=None,
                    started_at=None,
                    ended_at=None,
                    vector_index=vec if self._parent_emitted_vectors(path) else None,
                    measurements=structs,
                    measurement_count=len(structs),
                    step_retry=step_retry,
                )
            )

        _append_not_started(
            manifest,
            self._collected_items,
            executed_node_ids,
            executed_vectors=executed_vectors,
        )
        return manifest

    def _parent_emitted_vectors(self, step_path: str) -> bool:
        """True when the parent step emitted at least one VectorStarted event.

        Used for null-vs-0 reconstruction: step rows whose parent DID loop
        keep their enclosing vector_index at rest; top-level or non-swept
        children get NULL so a plain GROUP BY (step_path, vector_index) never
        conflates the step row with its own vector rows.
        """
        if "/" not in step_path:
            return False
        parent_path = step_path.rsplit("/", 1)[0]
        return any(k[0] == parent_path for k in self._vector_starts)

    def _build_step_entry(
        self,
        key: tuple[str, int, int],
        start: Any | None,
        end: Any | None,
        meas_count: int,
        observations: dict[str, Any],
        observation_units: dict[str, str] | None = None,
        observation_pins: dict[str, str] | None = None,
        step_measurements: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build one step manifest entry from cached StepStarted/StepEnded."""
        path, step_retry, vec = key
        # ``step_index`` for the manifest entry comes from the StepStarted
        # event itself — ``step_path`` is the dict key (unique per logical
        # step) and ``step_index`` is the per-bucket index the producer
        # assigned. The two are distinct concepts now that containers and
        # methods can share ``step_index`` across their respective buckets.
        idx = start.step_index if start else (end.step_index if end else 0)
        node_id = start.node_id if start else None
        inputs = _end_overrides_start(start, end, "inputs")
        outputs = dict(end.outputs) if end and getattr(end, "outputs", None) else {}
        input_units = _end_overrides_start(start, end, "input_units")
        output_units = dict(end.output_units) if end and getattr(end, "output_units", None) else {}
        output_pins = dict(end.output_pins) if end and getattr(end, "output_pins", None) else {}
        # Merge accumulated observations for this vector so the in-flight
        # step row carries the same out_* the materialized step aggregates
        # (StepEnded.outputs already holds them once it arrives merged; this
        # covers the pre-merge / direct-event projection path).
        for obs_name, obs_value in observations.items():
            outputs.setdefault(obs_name, obs_value)
        for obs_name, obs_unit in (observation_units or {}).items():
            output_units.setdefault(obs_name, obs_unit)
        for obs_name, obs_pin in (observation_pins or {}).items():
            output_pins.setdefault(obs_name, obs_pin)
        # NULL keeps the step row out of (step_path, vector_index) GROUP BY in _bulk_insert_steps.
        at_rest_vi: int | None = vec if self._parent_emitted_vectors(path) else None
        return step_entry_dict(
            index=idx,
            name=start.step_name if start else (end.step_name if end else ""),
            node_id=node_id,
            file=start.file if start else None,
            function=start.function if start else None,
            class_name=start.class_name if start else None,
            module=start.module if start else None,
            step_path=(
                (start.step_path if start else end.step_path if end else "")
                or (start.step_name if start else end.step_name if end else "")
            ),
            description=start.description if start else None,
            markers=self._markers_by_node.get(node_id) if node_id else None,
            outcome=end.outcome if end else None,
            started_at=start.occurred_at if start else None,
            ended_at=end.occurred_at if end else None,
            vector_index=at_rest_vi,
            inputs=inputs,
            outputs=outputs,
            input_units=input_units,
            output_units=output_units,
            output_pins=output_pins,
            measurements=step_measurements or [],
            measurement_count=meas_count,
            step_retry=step_retry,
            instrument_records=[],
        )
