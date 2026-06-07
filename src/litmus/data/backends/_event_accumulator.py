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
    validate_observation_kinds,
)
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    Observation,
    RunEnded,
    RunStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
)


def _safe_str(value: Any) -> str | None:
    """Return ``str(value)`` or ``None`` if *value* is falsy."""
    return str(value) if value else None


def _step_key(event: Any) -> tuple[str, int]:
    """Stable accumulator key for a StepStarted / StepEnded event.

    Uses ``step_path`` (the canonical hierarchical id) when set;
    falls back to ``step_name`` otherwise so direct-API callers
    (and tests) that emit events without populating step_path
    still get distinct keys per step. Vector index distinguishes
    sweep variants and class-container iterations.
    """
    path = event.step_path or event.step_name or ""
    return (path, event.vector_index)


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
        # Item 4 + item 9: ``observe()`` events accumulate here so the
        # auto-promotion rule can synthesize DONE rows for vectors with
        # 0 measurements + ≥1 observations at materialization time.
        self._observation_events: list[Any] = []
        # Step events keyed by (step_path, vector_index) so each sweep
        # variant — and each class-container iteration — gets its own
        # entry. ``step_index`` is unique per logical-step within its
        # parent bucket but COLLIDES across parents (a class container
        # at root step_index=0 and its first method at class-bucket
        # step_index=0 would clobber each other under a (step_index,
        # vector_index) key). step_path is unique end-to-end per step.
        self._step_starts: dict[tuple[str, int], Any] = {}
        self._step_ends: dict[tuple[str, int], Any] = {}
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
            self._step_starts[_step_key(event)] = event
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
                    "parent_path": entry.get("parent_path"),
                    "vector_index": entry.get("vector_index", 0),
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
                    "dynamic_attrs": {
                        **{f"in_{k}": _safe_str(v) for k, v in (entry.get("inputs") or {}).items()},
                        **{
                            f"out_{k}": _safe_str(v)
                            for k, v in (entry.get("outputs") or {}).items()
                        },
                    },
                }
            )
        return rows

    def snapshot_measurement_rows(self) -> list[dict[str, Any]]:
        """Return measurement rows matching the parquet measurements shape.

        Includes promoted DONE rows for verify-less vectors (same as the
        materialized path) and packs in_*/out_*/custom_* into dynamic_attrs.
        """
        if not self._run_started:
            return []
        ended_at = self._run_ended.occurred_at if self._run_ended else None
        outcome = self._run_ended.outcome if self._run_ended else None
        built = [self._build_row(e) for e in self._measurement_events]
        built.extend(self._build_promoted_rows())
        rows: list[dict[str, Any]] = []
        for row in built:
            row["run_ended_at"] = ended_at
            row["run_outcome"] = outcome
            row["dynamic_attrs"] = {
                k: _safe_str(v) for k, v in row.items() if k.startswith(("in_", "out_", "custom_"))
            }
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

    def _step_start_field(self, step_path: str, vector_index: int, attr: str) -> Any:
        """Get a field from the cached StepStarted event, or None."""
        start = self._step_starts.get((step_path, vector_index))
        return getattr(start, attr, None) if start else None

    def _validate_observation_kinds(self) -> None:
        """Item 10: walk every accumulated Observation and validate kinds.

        Builds a per-run registry of ``name -> kind`` from the
        accumulated observation events; raises ``ValueError`` if any
        observation's kind disagrees with the registered kind for its
        name. Called by :func:`_build_promoted_rows` before synthesis
        so the materializer fails loudly rather than producing a
        column with mixed types.

        The same validation also lives in
        :meth:`ParquetBackend._build_measurement_rows` for the
        pre-built TestRun path. Two paths, one rule.
        """
        registry: dict[str, str] = {}
        for ev in self._observation_events:
            path = ev.step_path or ev.step_name or ""
            validate_observation_kinds(
                registry,
                {ev.name: ev.value},
                where=f"vector {ev.vector_index} of step path {path!r}",
            )

    def _build_promoted_rows(self) -> list[dict[str, Any]]:
        """Item 9: synthesize DONE measurement rows for verify-less vectors.

        Walks the per-(step_path, vector_index) tally of measurement
        vs observation events. For each vector with 0 measurements
        and ≥1 observations, emits one row per observation:
        ``measurement_name = obs.name``, ``measurement_value =
        None``, ``measurement_outcome = "done"``. The observation
        value itself rides as ``out_<name>`` via the row's
        ``outputs`` field — same projection as a verify row's
        observations.

        Counterpart of the offline-path promotion in
        :meth:`ParquetBackend._build_measurement_rows`. The same
        manifestation rule applies to both materialization paths.
        """
        # Tally measurements per vector key — verify-less vectors are
        # the ones whose key isn't in this set.
        measured_keys = {
            (e.step_path or e.step_name or "", e.vector_index) for e in self._measurement_events
        }

        # Group observations by their vector key for synthesis.
        by_key: dict[tuple[str, int], list[Any]] = {}
        for ev in self._observation_events:
            key = (ev.step_path or ev.step_name or "", ev.vector_index)
            by_key.setdefault(key, []).append(ev)

        rows: list[dict[str, Any]] = []
        for key, obs_events in by_key.items():
            if key in measured_keys:
                # Vector had ≥1 verify — observations ride on those rows
                # as out_*; no DONE promotion (matches the §7 rule).
                continue
            for obs in obs_events:
                if obs.name.startswith("_"):
                    continue
                rows.append(self._build_promoted_row(obs))
        return rows

    def _build_promoted_row(self, obs: Any) -> dict[str, Any]:
        """Synthesize one DONE row from a single Observation event.

        Stamps the run/step context onto the row the same way
        :meth:`_build_row` does for a verify row, but with
        measurement-side fields filled in from observation defaults
        (``value=None``, ``outcome="done"``) and the observation
        value carried as ``out_<obs.name>`` in the row's ``outputs``.
        """
        path = obs.step_path or obs.step_name or ""
        vec = obs.vector_index
        start = self._step_starts.get((path, vec))
        end = self._step_ends.get((path, vec))
        node_id = start.node_id if start else None
        parent_path = (start.parent_path if start else (end.parent_path if end else "")) or ""
        # Inputs come from StepStarted (set at vector entry); outputs
        # come from StepEnded (the post-execution vector snapshot).
        # Fall back to the single observation event when neither has
        # arrived yet (in-flight projection).
        inputs = dict(start.inputs) if start and getattr(start, "inputs", None) else {}
        outputs: dict[str, Any] = dict(end.outputs) if end and getattr(end, "outputs", None) else {}
        # Always make sure this observation lands in outputs even if
        # the StepEnded snapshot hasn't merged it yet.
        outputs.setdefault(obs.name, obs.value)
        row = MeasurementRow(
            record_type="measurement",
            **run_context_from_run_started(self._run_started, obs, include_env=True),
            step_name=obs.step_name,
            step_index=obs.step_index,
            step_path=obs.step_path,
            parent_path=parent_path,
            step_started_at=start.occurred_at if start else None,
            step_ended_at=end.occurred_at if end else None,
            step_node_id=node_id,
            step_module=self._step_start_field(path, vec, "module"),
            step_file=self._step_start_field(path, vec, "file"),
            step_class=self._step_start_field(path, vec, "class_name"),
            step_function=self._step_start_field(path, vec, "function"),
            step_markers=self._markers_by_node.get(node_id) if node_id else None,
            step_outcome=end.outcome if end else None,
            step_vector_count=(self._planned_vector_count.get(node_id or "", 1) if node_id else 1),
            vector_index=vec,
            vector_retry=obs.retry,
            measurement_name=obs.name,
            measurement_timestamp=None,
            measurement_value=None,
            measurement_units=None,
            measurement_outcome="done",
            limit_low=None,
            limit_high=None,
            limit_nominal=None,
            limit_comparator=None,
            characteristic_id=None,
            spec_ref=None,
            dut_pin=None,
            fixture_connection=None,
            instrument_name=None,
            instrument_resource=None,
            instrument_channel=None,
            run_outcome=None,
            inputs=inputs,
            outputs=outputs,
            instruments=self._build_instrument_arrays(),
            custom={},
        )
        return row.to_flat_dict()

    def _build_row(self, event: Any) -> dict[str, Any]:
        """Denormalize a MeasurementRecorded event into a flat row dict."""
        idx = event.step_index
        vec = event.vector_index
        path = event.step_path or event.step_name or ""
        start = self._step_starts.get((path, vec))
        end = self._step_ends.get((path, vec))
        node_id = start.node_id if start else None
        parent_path = (start.parent_path if start else (end.parent_path if end else "")) or ""
        row = MeasurementRow(
            record_type="measurement",
            **run_context_from_run_started(self._run_started, event, include_env=True),
            step_name=event.step_name,
            step_index=idx,
            step_path=event.step_path,
            parent_path=parent_path,
            step_started_at=start.occurred_at if start else None,
            step_ended_at=end.occurred_at if end else None,
            step_node_id=node_id,
            step_module=self._step_start_field(path, vec, "module"),
            step_file=self._step_start_field(path, vec, "file"),
            step_class=self._step_start_field(path, vec, "class_name"),
            step_function=self._step_start_field(path, vec, "function"),
            step_markers=self._markers_by_node.get(node_id) if node_id else None,
            step_outcome=end.outcome if end else None,
            step_vector_count=(self._planned_vector_count.get(node_id or "", 1) if node_id else 1),
            vector_index=vec,
            vector_retry=event.retry,
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

        meas_counts: dict[tuple[str, int], int] = {}
        for e in self._measurement_events:
            key = (e.step_path or e.step_name or "", e.vector_index)
            meas_counts[key] = meas_counts.get(key, 0) + 1

        # Sort keys by the producer-assigned (step_index, vector_index) so
        # the resulting manifest preserves execution order regardless of the
        # alphabetical position of step_path. Falls back to the key itself
        # for events that didn't set step_index (zero-default).
        def _sort_key(k: tuple[str, int]) -> tuple[int, int, str, int]:
            ev = self._step_starts.get(k) or self._step_ends.get(k)
            step_index = getattr(ev, "step_index", 0) if ev else 0
            return (step_index, k[1], k[0], k[1])

        all_keys = sorted(set(self._step_starts) | set(self._step_ends), key=_sort_key)
        for key in all_keys:
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
            executed_vectors.add((key[0], key[1]))
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
        key: tuple[str, int],
        start: Any | None,
        end: Any | None,
        meas_count: int,
    ) -> dict[str, Any]:
        """Build one step manifest entry from cached StepStarted/StepEnded."""
        _, vec = key
        # ``step_index`` for the manifest entry comes from the StepStarted
        # event itself — ``step_path`` is the dict key (unique per logical
        # step) and ``step_index`` is the per-bucket index the producer
        # assigned. The two are distinct concepts now that containers and
        # methods can share ``step_index`` across their respective buckets.
        idx = start.step_index if start else (end.step_index if end else 0)
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
