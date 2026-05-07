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
    INSTRUMENT_ARRAY_KEYS,
    MeasurementRow,
    _append_not_started,
    _to_datetime,
    run_context_from_run_started,
    step_entry_dict,
)
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
)


def _safe_str(value: Any) -> str | None:
    """Return ``str(value)`` or ``None`` if *value* is falsy."""
    return str(value) if value else None


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
        self._measurement_events: list[Any] = []  # MeasurementRecorded events
        # Step events keyed by (step_index, vector_index) so each sweep
        # variant gets its own entry. A step running 4 sweep vectors
        # produces 4 StepStarted + 4 StepEnded events that share
        # step_index but have vector_index 0..3 — keying by step_index
        # alone would clobber 3 of them and lose per-vector identity.
        self._step_starts: dict[tuple[int, int], Any] = {}
        self._step_ends: dict[tuple[int, int], Any] = {}
        self._run_ended: Any = None  # RunEnded event (None for in-flight)
        self._collected_items: list[dict[str, str | int | None]] = []
        # node_id → markers, populated when StepsDiscovered arrives so
        # ``_build_row`` can stamp step_markers on every measurement row
        # without rebuilding the lookup per measurement.
        self._markers_by_node: dict[str, str | None] = {}
        # node_id → vector_count_planned from StepsDiscovered.  Lets the
        # in-flight step manifest report correct sweep sizes even before
        # all vector executions have arrived (matches the finalized
        # parquet rather than always reading 0).
        self._planned_vector_count: dict[str, int] = {}

    def on_event(self, event: Any) -> None:
        """Accumulate one event into in-memory state. No I/O."""
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, InstrumentConnected):
            self._instruments.append(event)
        elif isinstance(event, StepsDiscovered):
            self._collected_items = event.items
            markers: dict[str, str | None] = {}
            planned: dict[str, int] = {}
            for ci in event.items:
                nid = ci.get("node_id")
                if isinstance(nid, str) and nid:
                    m = ci.get("markers")
                    markers[nid] = m if isinstance(m, str) or m is None else str(m)
                    vc = ci.get("vector_count_planned")
                    if isinstance(vc, int):
                        planned[nid] = vc
            self._markers_by_node = markers
            self._planned_vector_count = planned
        elif isinstance(event, StepStarted):
            self._step_starts[(event.step_index, event.vector_index)] = event
        elif isinstance(event, MeasurementRecorded):
            self._measurement_events.append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[(event.step_index, event.vector_index)] = event
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
            "dut_serial": s.dut_serial,
            "dut_part_number": s.dut_part_number,
            "dut_lot_number": s.dut_lot_number,
            "station_id": s.station_id,
            "station_name": s.station_name,
            "station_hostname": s.station_hostname,
            "fixture_id": s.fixture_id,
            "outcome": outcome,
            "started_at": s.occurred_at,
            "ended_at": ended_at,
            "num_measurements": len(self._measurement_events),
            "num_steps": len(set(self._step_starts) | set(self._step_ends)),
            "test_phase": s.test_phase,
            "product_id": s.product_id,
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
                    "outcome": entry.get("outcome"),
                    "started_at": started_at,
                    "ended_at": entry_ended_at,
                    "duration_s": duration_s,
                    "has_measurements": entry.get("has_measurements", False),
                    "measurement_count": entry.get("measurement_count", 0),
                    "vector_count": entry.get("vector_count", 0),
                    "markers": entry.get("markers"),
                    "dut_serial": s.dut_serial,
                    "station_id": s.station_id,
                    "file_path": None,
                    "run_outcome": outcome,
                    "run_ended_at": ended_at,
                }
            )
        return rows

    def snapshot_measurement_rows(self) -> list[dict[str, Any]]:
        """Return measurement rows matching the parquet measurements shape."""
        if not self._run_started:
            return []
        ended_at = self._run_ended.occurred_at if self._run_ended else None
        outcome = self._run_ended.outcome if self._run_ended else None
        rows: list[dict[str, Any]] = []
        for event in self._measurement_events:
            row = self._build_row(event)
            row["run_ended_at"] = ended_at
            row["run_outcome"] = outcome
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Pure projection helpers — used by both snapshot and parquet write
    # ------------------------------------------------------------------

    def _build_instrument_arrays(self) -> dict[str, list]:
        """Build instrument arrays from cached InstrumentConnected events."""
        arrays: dict[str, list] = {k: [] for k in INSTRUMENT_ARRAY_KEYS}
        for inst in self._instruments:
            arrays["step_instruments_name"].append(inst.role)
            arrays["step_instruments_id"].append(inst.instrument_id)
            arrays["step_instruments_driver"].append(inst.driver)
            arrays["step_instruments_resource"].append(inst.resource)
            arrays["step_instruments_protocol"].append(inst.protocol)
            arrays["step_instruments_manufacturer"].append(inst.manufacturer)
            arrays["step_instruments_model"].append(inst.model)
            arrays["step_instruments_serial"].append(inst.serial)
            arrays["step_instruments_firmware"].append(inst.firmware)
            arrays["step_instruments_cal_due"].append(inst.cal_due)
            arrays["step_instruments_cal_last"].append(inst.cal_last)
            arrays["step_instruments_cal_certificate"].append(inst.cal_certificate)
            arrays["step_instruments_cal_lab"].append(inst.cal_lab)
            arrays["step_instruments_mocked"].append(inst.mocked)
        return arrays

    def _step_start_field(self, step_index: int, vector_index: int, attr: str) -> Any:
        """Get a field from the cached StepStarted event, or None."""
        start = self._step_starts.get((step_index, vector_index))
        return getattr(start, attr, None) if start else None

    def _build_row(self, event: Any) -> dict[str, Any]:
        """Denormalize a MeasurementRecorded event into a flat row dict."""
        idx = event.step_index
        vec = event.vector_index
        start = self._step_starts.get((idx, vec))
        end = self._step_ends.get((idx, vec))
        node_id = start.node_id if start else None
        parent_path = (start.parent_path if start else (end.parent_path if end else "")) or ""
        row = MeasurementRow(
            **run_context_from_run_started(self._run_started, event, include_env=True),
            step_name=event.step_name,
            step_index=idx,
            step_path=event.step_path,
            parent_path=parent_path,
            step_started_at=start.occurred_at if start else None,
            step_ended_at=end.occurred_at if end else None,
            step_node_id=node_id,
            step_module=self._step_start_field(idx, vec, "module"),
            step_file=self._step_start_field(idx, vec, "file"),
            step_class=self._step_start_field(idx, vec, "class_name"),
            step_function=self._step_start_field(idx, vec, "function"),
            step_markers=self._markers_by_node.get(node_id) if node_id else None,
            step_outcome=end.outcome if end else None,
            step_vector_count=(self._planned_vector_count.get(node_id or "", 1) if node_id else 1),
            vector_index=vec,
            vector_attempt=event.attempt,
            measurement_name=event.measurement_name,
            measurement_timestamp=event.measurement_timestamp,
            measurement_value=event.value,
            measurement_units=event.units,
            measurement_outcome=event.outcome,
            limit_low=event.limit_low,
            limit_high=event.limit_high,
            limit_nominal=event.limit_nominal,
            limit_comparator=event.limit_comparator,
            characteristic_id=event.characteristic_id,
            spec_ref=event.spec_ref,
            dut_pin=event.dut_pin,
            fixture_connection=event.fixture_connection,
            instrument_name=event.instrument_name,
            instrument_resource=event.instrument_resource,
            instrument_channel=event.instrument_channel,
            run_outcome=None,
            inputs=dict(event.inputs),
            outputs=dict(event.outputs),
            instruments=self._build_instrument_arrays(),
            custom=dict(event.custom),
        )
        return row.to_flat_dict()

    def _build_step_results_from_events(self) -> list[dict[str, Any]]:
        """Build step manifest from cached StepStarted/StepEnded events.

        Each entry corresponds to one ``(step_index, vector_index)``
        execution. A swept step running 4 vectors produces 4 manifest
        entries with the same ``step_path`` but ``vector_index`` 0..3.

        Partially-run sweeps (some but not all planned vectors ran)
        also produce manifest entries for the unrun vectors with
        ``outcome=None`` — surfaced via ``_append_not_started`` using
        ``vector_count_planned`` from ``StepsDiscovered``.
        """
        manifest: list[dict[str, Any]] = []
        executed_node_ids: set[str] = set()
        executed_vectors: set[tuple[str, int]] = set()

        meas_counts: dict[tuple[int, int], int] = {}
        for e in self._measurement_events:
            key = (e.step_index, e.vector_index)
            meas_counts[key] = meas_counts.get(key, 0) + 1

        all_keys = sorted(set(self._step_starts) | set(self._step_ends))
        for key in all_keys:
            start = self._step_starts.get(key)
            end = self._step_ends.get(key)
            node_id = start.node_id if start else None
            if node_id:
                executed_node_ids.add(node_id)
                executed_vectors.add((node_id, key[1]))
            manifest.append(self._build_step_entry(key, start, end, meas_counts.get(key, 0)))

        _append_not_started(
            manifest,
            self._collected_items,
            executed_node_ids,
            executed_vectors=executed_vectors,
        )
        return manifest

    def _build_step_entry(
        self,
        key: tuple[int, int],
        start: Any | None,
        end: Any | None,
        meas_count: int,
    ) -> dict[str, Any]:
        """Build one step manifest entry from cached StepStarted/StepEnded."""
        idx, vec = key
        node_id = start.node_id if start else None
        # vector_count: prefer the planned count from StepsDiscovered (matches
        # what the finalized parquet records). Falls back to ``1`` for steps
        # that did execute but weren't in the manifest (defensive — keeps the
        # in-flight overlay close to the finalized shape).
        vector_count = self._planned_vector_count.get(node_id or "", 1) if node_id else 1
        # Per-vector parent_path / inputs / outputs come straight off the
        # StepStarted / StepEnded events. parent_path defaults to "" so
        # root steps look identical to today.
        parent_path = (start.parent_path if start else (end.parent_path if end else "")) or ""
        inputs = dict(start.inputs) if start and getattr(start, "inputs", None) else {}
        outputs = dict(end.outputs) if end and getattr(end, "outputs", None) else {}
        return step_entry_dict(
            index=idx,
            name=start.step_name if start else (end.step_name if end else ""),
            node_id=node_id,
            file=start.file if start else None,
            function=start.function if start else None,
            class_name=start.class_name if start else None,
            module=start.module if start else None,
            step_path=start.step_path if start else (end.step_path if end else ""),
            parent_path=parent_path,
            description=start.description if start else None,
            markers=self._markers_by_node.get(node_id) if node_id else None,
            outcome=end.outcome if end else None,
            started_at=start.occurred_at if start else None,
            ended_at=end.occurred_at if end else None,
            vector_index=vec,
            inputs=inputs,
            outputs=outputs,
            has_measurements=meas_count > 0,
            measurement_count=meas_count,
            vector_count=vector_count,
        )
