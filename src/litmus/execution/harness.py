"""Test harness for vector-based test execution.

The TestHarness owns vectors, handles loop iteration, retry logic, prompting,
and measurement logging. It can be used directly (without pytest) or via
the pytest plugin fixtures.
"""

from __future__ import annotations

import importlib
import logging
import time
import warnings
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.events import Observation
from litmus.data.models import (
    Measurement,
    Outcome,
    TestStep,
    TestVector,
    Waveform,
    _utcnow,
    escalate_outcome,
)
from litmus.data.ref import Latchable, classify_value, is_ref
from litmus.execution._state import (
    get_active_characteristic,
    get_active_limits,
    get_active_part_context,
    get_active_station_config,
    get_active_test_characteristics,
    get_current_code_identity,
    get_current_logger,
    get_current_step,
    get_current_vector,
    no_active_resource_error,
    push_current_context,
    push_current_step,
    push_current_vector,
    reset_current_context,
    reset_current_step,
    reset_current_vector,
    resolve_session_id,
)
from litmus.execution.vectors import Vector, expand_vectors
from litmus.execution.verify import _perform_measure, _perform_verify
from litmus.models.test_config import Limit, MeasurementLimitConfig, PromptConfig, RetryConfig
from litmus.prompts import ask

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from litmus.data.models import TestRun
    from litmus.execution.logger import TestRunLogger
    from litmus.models.part import Part
    from litmus.models.station import StationConfig
    from litmus.parts.context import PartContext


class LimitsView(Mapping[str, MeasurementLimitConfig]):
    """Dict-like view over the merged ``litmus_limits`` for the active test.

    Wraps the ``_active_limits_var`` dict and exposes the standard
    :class:`Mapping` surface (``__getitem__`` / ``__iter__`` /
    ``__len__`` / ``__contains__`` / ``keys`` / ``values`` / ``items``)
    plus a :meth:`for_characteristic` filter for per-char scoping.

    The view is a snapshot: accessors return live references into the
    underlying dict. Tests should treat it as read-only.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, MeasurementLimitConfig]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> MeasurementLimitConfig:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def for_characteristic(self, char_id: str) -> dict[str, MeasurementLimitConfig]:
        """Return entries whose ``characteristic:`` field equals ``char_id``.

        **Result depends on iteration state.** Two cases:

        * Entries with an explicit ``characteristic: <id>`` field are
          always included when ``id == char_id``.
        * Entries with **no** ``characteristic:`` field are included
          only when the active-char ContextVar matches ``char_id``.
          That ContextVar is pushed by ``ConnectionIterator`` during
          ``for connection in ctx.connections.for_characteristic(...)``
          (and during plain iteration over a single-char default).
          Outside any iteration block, the ContextVar is ``None`` and
          field-less entries are omitted from the result.

        Practical consequence: calling
        ``ctx.limits.for_characteristic("rail_3v3")`` from inside a
        ``ctx.connections.for_characteristic("rail_3v3")`` loop returns
        every limit applicable to that char (explicit + inherited);
        calling it from the test body's top level returns only
        explicit matches. Test code that wants the unconditional
        explicit-only view should filter ``ctx.limits`` directly:
        ``{k: v for k, v in ctx.limits.items() if v.characteristic == "rail_3v3"}``.
        """
        active = get_active_characteristic()
        return {
            label: cfg
            for label, cfg in self._data.items()
            if cfg.characteristic == char_id or (cfg.characteristic is None and active == char_id)
        }


class Context:
    """Hierarchical context with scoped inheritance.

    Data set at parent level is inherited by children. Children can override
    parent values locally. This enables run → step → vector scoping:

    - **Run level**: Data visible to all steps and vectors
    - **Step level**: Data visible to all vectors in that step
    - **Vector level**: Data visible only to that vector

    The context provides semantic methods for data capture:
    - configure(): Record configuration/stimulus values (→ in_*)
    - observe(): Record measured context/observations (→ out_*)

    Example usage:
        def test_output_voltage(psu, dmm, temp_probe, context):
            # Log environmental observation
            context.observe("temp_probe.temperature", temp_probe.read())
            context.observe("temp_probe.humidity", temp_probe.read_humidity())

            # Configure stimulus (if not already from vector params)
            context.configure("psu.actual_voltage", psu.read_voltage())

            # THE measurement
            return dmm.measure_dc_voltage()

    Naming convention:
    - Spec conditions use bare names: temperature, load (match spec condition keys)
    - Implementation details use fixture prefix: psu.voltage, dmm.sample_count
    """

    def __init__(
        self,
        parent: Context | None = None,
        prev: Context | None = None,
        harness: TestHarness | None = None,
        channel_store: Any | None = None,
        session_id: UUID | None = None,
    ):
        """Initialize context with optional parent for inheritance.

        Args:
            parent: Parent context to inherit values from. If None, this is a root context.
            prev: Previous sibling context (for change detection across vectors).
            harness: TestHarness reference for accessing limits and other harness features.
            channel_store: Optional ChannelStore for direct writes of numeric data.
            session_id: Session this context belongs to. Required for blob observations
                (routed through FileStore.write which is session-scoped). Production
                paths pull from the active session; some test paths leave None.
        """
        self._parent = parent
        self._prev = prev
        self._harness = harness
        self._channel_store = channel_store
        # session_id: single resolution rule lives in
        # ``litmus.execution._state.resolve_session_id`` so harness /
        # files.py / future call sites all share one precedence order
        # (explicit > harness > parent > active ContextVar).
        #
        # ``fallback_to_active`` is OFF here (the default). A freshly
        # constructed Context with no parent/harness/explicit session_id
        # MUST NOT silently inherit the ambient ContextVar — bare-Context
        # unit tests should resolve to None, not to whatever session the
        # surrounding pytest run happens to have wired. The opt-in path
        # is in ``litmus.files.py:_resolve_session_id``, where
        # ``fallback_to_active=True`` is correct because ``files.write``
        # is a user-facing module-level surface and reading from the
        # active ContextVar is the documented contract there.
        resolved = resolve_session_id(session_id, harness=harness, parent=parent)
        self._session_id: UUID | None = resolved  # type: ignore[assignment]
        # Surface a debug log if the Context is fully unwired (no
        # session_id, no harness, no parent) — the only blob path
        # available is then raising RuntimeError at first observe(blob).
        # Production paths always populate session_id; this trips
        # only in bare-Context unit tests.
        if self._session_id is None and harness is None and parent is None:
            _log.debug(
                "Context constructed with session_id=None and no harness/parent. "
                "Blob observations (PIL.Image / bytes / Pydantic / ...) will raise "
                "at runtime. Pass session_id= explicitly to enable the blob path."
            )
        self._params: dict[str, Any] = {}
        self._observations: dict[str, Any] = {}
        # ``connections`` iterates :class:`FixtureConnection` objects the
        # test declares via ``litmus_characteristics`` / ``litmus_connections``
        # markers. Populated by the pytest-native plugin's
        # ``_litmus_resolve_connections`` autouse fixture; ``None`` when
        # the test declares no spec/connections markers. Test body:
        # ``for _ in ctx.connections: ...`` — iterating pushes the
        # active :class:`FixtureConnection` into ``_active_connection_var``
        # so driver fixtures route and the logger stamps traceability.
        self.connections: Any = None

    def child(self) -> Context:
        """Create a child context that inherits from this one.

        Inherits ``harness``, ``channel_store``, and ``session_id``
        from the parent. Does NOT capture the current vector —
        observations called on the child use the active vector from
        ``get_current_vector()`` (the ContextVar) at observe-time.

        **Caveat for nested-context patterns**: if a child Context
        outlives a vector iteration (e.g., created inside a self-loop
        body and used after ``__next__`` rolls to the next vector),
        observations on the child mirror to whichever vector is
        currently on the ContextVar — which may be a different vector
        than the one the child was created in. For most patterns
        (child created and used within the same vector scope) this
        is fine. Cross-iteration child contexts are rare and the
        caller is responsible for ensuring vector scope matches.

        Returns:
            New Context with this context as parent.
        """
        return Context(parent=self, harness=self._harness, channel_store=self._channel_store)

    # -------------------------------------------------------------------------
    # Semantic API (preferred)
    # -------------------------------------------------------------------------

    def configure(self, key: str, value: Any) -> None:
        """Record a configuration/stimulus value (→ in_* column).

        Use for commanded values, setpoints, and settings.

        Args:
            key: Parameter name (e.g., "psu.voltage", "temperature").
            value: The commanded value.
        """
        self._params[key] = value

    def observe(self, key: str, value: Any, *, namespace: str | None = None) -> None:
        """Record an observation/measurement context (→ out_* column).

        Per §3 + §4 of the design doc, ``observe`` is a polymorphic
        intent verb — the author writes one call and the framework
        picks the store by value shape:

        - **scalar** (int/float/bool/str/None) → inline in
          ``_observations``; lands in the parquet ``out_*`` column
          directly.
        - **Waveform** → ChannelStore (item 6 verb-layer unpack:
          writes ``wf.Y`` as the array payload with
          ``sample_interval=wf.dt``); ``out_<name>`` carries the
          ``channel://`` URI. **Caveat**: ``t0`` and
          ``Waveform.attributes`` have no row-level home in today's
          schema (open per design doc §15) — they're dropped with a
          ``RuntimeWarning`` when non-default. Use
          ``filestore.write(name, wf)`` if you need them preserved.
        - **numeric_array** (list/tuple/ndarray of bool/int/float/str
          leaves, plus dict struct shapes) → ChannelStore;
          ``out_<name>`` carries the ``channel://`` URI.
        - **blob** (bytes/Path/PIL.Image/Pydantic/anything else) →
          FileStore via :func:`get_filestore().put`; ``out_<name>``
          carries the ``file://`` URI.
        - **URI string** (``channel://...`` / ``file://...``) → stamped
          as-is (no re-write).
        - **Sink handle** (``_ChannelSink`` from
          :func:`litmus.channels.stream`, ``_BaseSink`` from
          :func:`litmus.files.stream`, or any object satisfying the
          :class:`~litmus.data.ref.Latchable` Protocol) → ``.uri``
          stamped as-is (no re-write — the sink already wrote its
          data).

        Args:
            key: Observation name (e.g., "temperature", "scope.waveform").
            value: The observed value (any of the four shapes above).
            namespace: Item 16 — optional prefix sugar. When set, the
                effective name becomes ``"{namespace}.{key}"``
                (e.g., ``observe("voltage", v, namespace="psu_under_test")``
                → effective name ``"psu_under_test.voltage"``). Pure
                opt-in; nothing automatic.

                **Scope (same on observe / verify / stream):** the
                prefix applies uniformly to (1) the effective key
                used for store resolution, (2) the channel_id
                (ChannelStore writes) or file artifact name (FileStore
                writes), and (3) the event payload's ``name``. It
                does NOT affect vector parameters, limit lookup keys,
                run-context fields, or any other namespace.
        """
        # Item 16: namespace= prefix sugar. Applies uniformly to the
        # observations dict key, the channel_id (when written to
        # ChannelStore), the file artifact name (when written to
        # FileStore), and the Observation event's ``name``.
        full_key = f"{namespace}.{key}" if namespace else key

        if value is not None:
            # Reference latching (design doc §4): if the caller hands
            # us something that's already in a store — a URI string or
            # a handle exposing ``.uri`` — stamp the URI without
            # re-writing. Checked BEFORE shape dispatch so a ``str``
            # URI doesn't fall through to the scalar-stash path and a
            # sink handle doesn't get pickled as a blob.
            if is_ref(value):
                self._stamp_observation(full_key, value)
                return
            if isinstance(value, Latchable):
                # Closed-sink check: a Latchable's ``.uri`` is stable
                # before / during / after the sink's write window, so
                # nothing prevents ``observe(name, sink)`` after the
                # sink has been ``.close()``d. The URI is technically
                # valid (file is on disk) but the sequencing is odd —
                # latching a closed sink usually means the test code
                # is shaped wrong (observe should usually come during
                # the write window, before close). Warn instead of
                # raising so a working pattern still works; lets
                # operators discover the issue without breaking flows.
                if getattr(value, "_closed", False):
                    warnings.warn(
                        f"observe({full_key!r}, sink): sink is already "
                        f"closed. The URI ({value.uri!r}) is still valid, "
                        "but latching a closed sink usually means observe() "
                        "was called after the ``with`` block exited — "
                        "consider moving observe() inside the with-block.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                self._stamp_observation(full_key, value.uri)
                return

            # Item 6: Waveform routes to ChannelStore via verb-layer
            # unpack. classify_value reports Waveform as ``blob`` (no
            # ``tolist``); the design doc §4 says channel-shaped, so we
            # route directly. Y → value; dt → sample_interval; t0 →
            # sampled_at; attributes → channel descriptor attributes.
            if isinstance(value, Waveform):
                if self._channel_store is not None:
                    uri = self._channel_store.write(
                        full_key,
                        value.Y,
                        sample_interval=value.dt,
                        sampled_at=value.t0,
                        attributes=value.attributes or None,
                        source="observe",
                        run_id=self._current_run_id(),
                    )
                    self._stamp_observation(full_key, uri)
                    return
                # No channel store wired (bare Context test): fall through
                # to the FileStore path; Waveform becomes a .npz blob.

            vtype = classify_value(value)
            if vtype in ("numeric_array", "channel"):
                if self._channel_store is None:
                    # Exhaustiveness: array/channel values without a
                    # ChannelStore would land in ``_observations`` as a
                    # raw list/ndarray and break parquet serialization
                    # at row-build time. Fail loud at the call site
                    # instead of writing garbage to ``out_*``.
                    raise RuntimeError(
                        f"observe({full_key!r}): value classified as "
                        f"{vtype!r} but no ChannelStore is wired on this "
                        "Context. Construct Context with a ``channel_store=``, "
                        "or run inside a pytest session / ``connect(...)`` "
                        "block that wires the ChannelStore ContextVar."
                    )
                uri = self._channel_store.write(
                    full_key, value, source="observe", run_id=self._current_run_id()
                )
                self._stamp_observation(full_key, uri)
                return
            if vtype == "blob":
                # Item 3a — fixes half of the image-drop. Route blobs through
                # FileStore.write; stash the resulting URI in observations.
                # Pre-3a: blobs were silently stashed as raw values, never
                # written to disk except via the at-RunEnded materializer
                # _ref path (and the latter only when run materialization
                # actually ran — blobs were lost on crash).
                if self._session_id is None:
                    raise no_active_resource_error(
                        f"session_id (observing blob {full_key!r} of type {type(value).__name__})",
                        explicit_arg="session_id",
                    )
                # Lazy: data.files chain pulls PIL / serializers; only
                # paid when this blob path actually runs.
                from litmus.data.files import get_filestore  # noqa: PLC0415

                uri = get_filestore().write(
                    name=full_key,
                    value=value,
                    session_id=str(self._session_id),
                )
                self._stamp_observation(full_key, uri)
                return

        self._stamp_observation(full_key, value)

    def _stamp_observation(self, key: str, value: Any) -> None:
        """Persist an observation to ``_observations`` AND mirror to the active vector.

        The mirror is what makes ``out_*`` columns land on the parquet
        ``record_type='measurement'`` row at row-build time.
        ``build_output_columns`` (in ``_row_helpers``) reads from
        ``vector.observations``, not ``Context._observations``; without
        the mirror, every ``out_*`` column was empty when a measurement
        was emitted in the middle of a test body (before vector teardown
        could snapshot from the context).

        **Mirror caveat — observations outside a vector scope are
        parquet-invisible.** This method reads ``get_current_vector()``
        from the ContextVar. When that returns ``None`` (called before
        ``pytest_runtest_call`` opens a step, in a session-scoped
        helper that runs before any test body, or interactively
        without a TestHarness vector pushed), the observation is
        stashed on ``self._observations`` but NEVER lands on a
        ``TestVector.observations`` dict — and so never reaches the
        parquet ``out_*`` columns. Production pytest tests and
        ``TestHarness.run_vector(...)`` are inside a vector scope by
        construction; non-standard test patterns (observations in
        autouse fixtures, helpers called before the step opens) lose
        observations silently. Document this in any test helper that
        wraps observations.
        """
        self._observations[key] = value
        vec = get_current_vector()
        # Defensive ``getattr``: production paths always push TestVector
        # (logger.start_step, harness.run_vector, _VectorIterator) so the
        # mirror lands; the guard tolerates duck-typed test fakes that
        # only expose ``index``/``retry`` for the emit_observation event.
        vec_obs = getattr(vec, "observations", None)
        if isinstance(vec_obs, dict):
            vec_obs[key] = value
        self._emit_observation(key, value)

    def _current_run_id(self) -> UUID | None:
        """Pull the active run_id from the active TestRunLogger ContextVar.

        Returned by :meth:`stream` / :meth:`observe` / :meth:`verify`
        write callers so ChannelStore can stamp the right run context
        on ``ChannelStarted`` / ``ChannelClosed`` lifecycle events.
        Returns ``None`` when called outside a run (interactive
        bringup, daemon-driven writes, bare unit tests).
        """
        logger = get_current_logger()
        return getattr(getattr(logger, "test_run", None), "id", None)

    def _emit_observation(self, key: str, value: Any) -> None:
        """Emit an ``Observation`` event for the value that landed in ``_observations``.

        Item 4 in the v0.2.0 data-architecture lift. Pre-item-4 the
        observe path was silent on the event timeline; subscribers
        couldn't see captures. After item 4, each observe call
        produces exactly one ``Observation`` event with the value
        (or claim URI) plus step/vector context pulled from the
        active ContextVars.

        Skips emit silently when there's no active logger /
        event_log / session — observe() should still work outside
        an event-logged context (e.g., bare unit tests of Context).
        Emits a debug log so the no-emit case is visible to anyone
        debugging "I called observe() but nothing appeared on the
        timeline."
        """
        if self._session_id is None:
            _log.debug(
                "Observation %r not emitted: Context.session_id is None "
                "(value still stashed in Context._observations and mirrored to "
                "active vector). Construct Context inside a session "
                "(pytest fixture or connect()) for event-timeline visibility.",
                key,
            )
            return
        logger = get_current_logger()
        event_log = getattr(logger, "event_log", None) if logger is not None else None
        if event_log is None:
            _log.debug(
                "Observation %r not emitted: no active TestRunLogger / event_log. "
                "Run inside a logger context to land observations on the event timeline.",
                key,
            )
            return

        # Pull step/vector context from active ContextVars; defaults
        # are fine when called outside a step/vector (defensive).
        step = get_current_step()
        vector = get_current_vector()
        run_id = getattr(getattr(logger, "test_run", None), "id", None)

        event_log.emit(
            Observation(
                session_id=self._session_id,
                run_id=run_id,
                step_name=getattr(step, "name", "") if step else "",
                step_index=getattr(step, "step_index", 0) if step else 0,
                step_path=getattr(step, "step_path", "") if step else "",
                vector_index=getattr(vector, "index", 0) if vector else 0,
                retry=getattr(vector, "retry", 0) if vector else 0,
                name=key,
                value=value,
            )
        )

    def stream(
        self,
        name: str,
        sample: Any,
        *,
        namespace: str | None = None,
    ) -> str:
        """Append one sample to a channel — sibling of observe / verify.

        Per §3 of the design doc, ``stream`` is the third sibling
        test-author intent verb. Always routes to ChannelStore (never
        FileStore — that's the operational-verb split that gives
        ``stream`` its commitment: subscribers know channels are
        where to look). Per Position 2: emits ``ChannelStarted`` once
        per (channel, session) on first write via the channel store's
        own machinery; subsequent writes are ChannelStore-only (no
        per-sample event).

        Unlike :meth:`observe`, ``stream`` never stamps ``out_*`` on
        the vector — it's an append-to-stream operation, not a
        "stash this on my current context" operation. Per §3 line
        236: ``stream`` and ``observe`` are strictly orthogonal.
        Author wires the channel to the vector explicitly via
        ``observe(name, channel_handle_or_URI)`` when association is
        wanted.

        Args:
            name: Channel name.
            sample: One sample to append. Same shape rules as
                ``observe``'s array-handling path — scalar, list,
                ndarray, dict (struct). Blobs raise ``ValueError``
                at the ChannelStore gate; use ``filestore.write`` for
                blobs.
            namespace: Optional prefix sugar — effective channel_id
                is ``"{namespace}.{name}"``. Scope rules are shared
                across observe / verify / stream: prefix applies to
                (1) the effective key (here: channel_id), (2) the
                event payload's ``name``. See
                :meth:`observe` docstring for the canonical
                description.

        Returns:
            The ``channel://`` URI for this sample's channel.

        Raises:
            RuntimeError: When no ChannelStore is wired to the Context.
        """
        if self._channel_store is None:
            raise RuntimeError(
                f"Context.stream({name!r}, ...): no ChannelStore wired. "
                "Construct a TestHarness with channel_store explicitly, "
                "or run inside an active Litmus session."
            )
        full_name = f"{namespace}.{name}" if namespace else name
        return self._channel_store.write(
            full_name, sample, source="stream", run_id=self._current_run_id()
        )

    def verify(
        self,
        name: str,
        value: float | int | None,
        limit: Any = None,
        *,
        characteristic: str | None = None,
        namespace: str | None = None,
    ) -> Any:
        """Record + judge a measurement (→ measurement row).

        Polymorphic intent verb symmetric with :meth:`observe`. Per §3
        of the design doc, ``verify`` is one of three sibling
        test-author verbs — exposed both as a method on Context (for
        programmatic / non-pytest use) and as a bare pytest fixture
        (for idiomatic pytest tests). Both shapes route through the
        same underlying ``_perform_verify`` implementation.

        Args:
            name: Measurement name.
            value: The measured value (scalar). Non-scalar dispatch
                is a deferred follow-up — see design doc §4 and the
                C3a scope decision.
            limit: Optional ``Limit`` (or dict) to judge against. When
                omitted, falls through to ``logger.measure`` (DONE
                outcome) when the active profile sets
                ``verify_requires_limit: false``; otherwise raises
                ``MissingLimitError``.
            characteristic: Override the active characteristic for
                limit resolution.
            namespace: Item 16 — optional prefix sugar. When set, the
                effective name becomes ``"{namespace}.{name}"`` for
                limit lookup, measurement row, and event payload.
                Same scope rule across observe / verify / stream —
                see :meth:`observe` docstring for the canonical
                description.

        Returns:
            The recorded :class:`Measurement`.

        Raises:
            LimitFailure: When the value falls outside the limit.
            MissingLimitError: When no limit is configured and the
                active profile requires one.
        """
        return _perform_verify(
            name,
            value,
            limit=limit,
            characteristic=characteristic,
            namespace=namespace,
        )

    def changed(self, key: str) -> bool:
        """Check if an input parameter changed from the previous vector.

        Args:
            key: Parameter name to check.

        Returns:
            True if the value changed or this is the first vector, False otherwise.
        """
        if self._prev is None:
            return True  # First vector - everything is "changed"

        current_value = self.get_param(key)
        prev_value = self._prev.get_param(key)
        return current_value != prev_value

    def last(self, key: str, default: Any = None) -> Any:
        """Return the value of ``key`` recorded on the previous vector.

        Reads from the immediately-preceding ``Context`` (same
        ``(parent_node, method)`` in pytest-native; same parametrize case
        stream in the legacy harness). Looks up the key as a param first,
        then as an observation — matches how callers want to read back
        "what did I set / measure last time?" without caring about which
        store holds it.

        Returns ``default`` when no previous context exists (first vector)
        or the key was never recorded.
        """
        if self._prev is None:
            return default
        if key in self._prev._params:
            return self._prev._params[key]
        if key in self._prev._observations:
            return self._prev._observations[key]
        return default

    # -------------------------------------------------------------------------
    # Bulk operations
    # -------------------------------------------------------------------------

    def configure_all(self, values: dict[str, Any]) -> None:
        """Record multiple configuration values at once.

        Args:
            values: Dict of key-value pairs for in_* columns.
        """
        for key, value in values.items():
            self.configure(key, value)

    def observe_all(self, values: dict[str, Any]) -> None:
        """Record multiple observations at once.

        Args:
            values: Dict of key-value pairs for out_* columns.
        """
        for key, value in values.items():
            self.observe(key, value)

    def set_params(self, values: dict[str, Any]) -> None:
        """Set multiple param values at once (typically from vector params).

        Args:
            values: Dict of parameter values.
        """
        self._params.update(values)

    def set_observations(self, values: dict[str, Any]) -> None:
        """Set multiple observation values at once.

        Args:
            values: Dict of observation values.
        """
        self._observations.update(values)

    # -------------------------------------------------------------------------
    # Read access (with parent chain lookup)
    # -------------------------------------------------------------------------

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a parameter value, checking parent chain.

        Args:
            key: Parameter name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._params:
            return self._params[key]
        if self._parent is not None:
            return self._parent.get_param(key, default)
        return default

    def get_observation(self, key: str, default: Any = None) -> Any:
        """Get an observation value, checking parent chain.

        Args:
            key: Observation name.
            default: Default if not found in this context or any parent.

        Returns:
            The value or default.
        """
        if key in self._observations:
            return self._observations[key]
        if self._parent is not None:
            return self._parent.get_observation(key, default)
        return default

    @property
    def params(self) -> dict[str, Any]:
        """All parameter values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.params)
        result.update(self._params)
        return result

    @property
    def observations(self) -> dict[str, Any]:
        """All observation values, merged with parent chain."""
        result: dict[str, Any] = {}
        if self._parent is not None:
            result.update(self._parent.observations)
        result.update(self._observations)
        return result

    @property
    def characteristics(self) -> tuple[str, ...]:
        """Characteristic IDs in scope for the active test, in declaration order.

        Sourced from the ``litmus_characteristics`` marker (or the union of
        per-limit ``characteristic:`` values when the marker is absent).
        Read-only view; mirrors how :attr:`params` exposes the active
        vector params.
        """
        return tuple(get_active_test_characteristics())

    @property
    def limits(self) -> LimitsView:
        """Dict-like view over the merged ``litmus_limits`` for this test.

        Use ``ctx.limits[label]`` for the raw config (no resolution),
        ``ctx.limits.for_characteristic(char_id)`` for the subset bound
        to one char. Iteration follows insertion order. Resolution to
        a concrete :class:`Limit` still goes through
        :meth:`Context.get_limit` or the ``verify``/``logger`` paths.
        """
        return LimitsView(get_active_limits())

    # -------------------------------------------------------------------------
    # Session-level traceability (read-only ambient roll-up)
    # -------------------------------------------------------------------------

    @property
    def run(self) -> TestRun | None:
        """Active :class:`TestRun` record, or ``None`` outside a run.

        Carries run identity (id, started_at) plus UUT, station, fixture,
        part, profile, operator, and git fields. ``ctx.run.uut.serial``
        is the canonical path to UUT identity — there is intentionally no
        ``ctx.uut`` attribute (the bare ``uut`` fixture is the live UUT
        driver, a different concept).
        """
        logger = get_current_logger()
        return logger.test_run if logger is not None else None

    @property
    def station(self) -> StationConfig | None:
        """Active :class:`StationConfig`, or ``None`` for bringup tier.

        Sourced from the ``station_config`` session fixture, which seeds
        the ContextVar at session start. Note the asymmetry with the
        bare ``instruments`` fixture: ``instruments`` is left as a
        fixture-only entry point because ``ctx.instruments`` would
        collide with that fixture's name — take it as a test argument
        instead.
        """
        return get_active_station_config()

    @property
    def part(self) -> Part | None:
        """Active :class:`Part` definition, or ``None`` when no part is loaded.

        Mirrors the ``part`` session fixture but lets tests reach for it
        via ``ctx.part`` without taking the fixture as an argument. For
        derived limits use ``ctx.get_limit(name)`` or the ``limits`` fixture.
        """
        pc = get_active_part_context()
        return pc.part if pc else None

    # -------------------------------------------------------------------------
    # Limit access
    # -------------------------------------------------------------------------

    def get_limit(self, name: str) -> Limit | None:
        """Get resolved limit for a measurement.

        Resolves limit using the same logic as harness.measure():
        1. Check harness._limits for direct/config limits
        2. Try MeasurementLimitConfig.to_limit() for direct values
        3. Try spec reference via PartContext
        4. Try callable limit evaluation
        5. Fall back to PartContext characteristic lookup

        Args:
            name: Measurement name to get limit for.

        Returns:
            Resolved Limit object, or None if no limit defined.

        Example:
            def test_adaptive(context, dmm, verify):
                limit = context.get_limit("output_voltage")
                if limit:
                    # Take more samples if nominal is tight
                    samples = 10 if limit.tolerance < 0.05 else 5
        """
        if self._harness is None:
            return None
        return self._harness._resolve_limit(name)

    def measure(
        self,
        name: str,
        value: float | int | None,
        limit: Limit | None = None,
        *,
        characteristic: str | None = None,
        namespace: str | None = None,
    ) -> Measurement:
        """Record a measurement without judging it (→ measurement row).

        The record-only sibling of :meth:`verify` — stamps one
        measurement row with :attr:`Outcome.DONE` and never raises on a
        missing limit. Use when a value should be captured but not
        pass/fail judged (characterization, diagnostics, logged
        context). Auto-traceability (``uut_pin`` / ``instrument_*`` /
        ``characteristic_id`` / ``spec_ref``) is pulled from the active
        :class:`PartContext` by measurement name — callers never pass it.

        Like :meth:`verify` / :meth:`observe`, routes through the active
        logger (ContextVar) via the shared ``_perform_measure`` body, so
        the method form and the bare ``measure`` fixture behave
        identically and work in pytest-native tests and programmatic
        paths alike.

        Args:
            name: Measurement name (e.g., "output_voltage").
            value: Measured value (scalar).
            limit: Optional ``Limit`` recorded on the row (so analysis
                sees the active band) but never evaluated.
            characteristic: Override the active characteristic for
                limit/spec resolution.
            namespace: Optional prefix sugar — the effective name
                becomes ``"{namespace}.{name}"``. Same rule across
                observe / verify / measure.

        Returns:
            The recorded :class:`Measurement` (``Outcome.DONE``).

        Example:
            def test_power_supply(context, dmm, psu):
                context.measure("output_voltage", dmm.measure_dc_voltage())
                context.measure("quiescent_current", psu.measure_current())
        """
        return _perform_measure(
            name,
            value,
            limit=limit,
            characteristic=characteristic,
            namespace=namespace,
        )


class TestHarness:
    """Harness for executing tests across expanded vectors.

    The harness manages:
    - Vector expansion from config
    - Iteration over vectors with .changed() tracking
    - Retry logic at the vector level
    - Measurement logging with limit resolution
    - Operator prompts
    - Mock configuration per vector (when using mocks)

    Usage (explicit loop):
        harness = TestHarness(config, logger=logger)
        for vector in harness.vectors:
            if vector.changed("temperature"):
                harness.prompt(f"Set chamber to {vector['temperature']}C")
            with harness.run_vector(vector):
                harness.measure("voltage", dmm.measure_dc_voltage())

    Usage (pytest-native):
        def test_sweep(context, psu, dmm, verify):
            psu.set_voltage(context.get_param("voltage"))
            verify("voltage", float(dmm.measure_dc_voltage()))
    """

    __test__ = False  # Prevent pytest collection (name starts with "Test")

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        logger: TestRunLogger | None = None,
        step_name: str = "test",
        retry: RetryConfig | None = None,
        limits: dict[str, MeasurementLimitConfig | Limit] | None = None,
        part_context: PartContext | None = None,
        instruments: dict[str, Any] | None = None,
        mock_instruments: bool = False,
        channel_store: Any | None = None,
        session_id: UUID | None = None,
    ):
        """Initialize harness.

        Args:
            config: Test configuration dict with 'vectors', 'retry', 'limits' keys.
            logger: TestRunLogger for accumulating results.
            step_name: Name for the test step.
            retry: Retry configuration (overrides config if provided).
            limits: Limit configurations by measurement name (overrides config).
            part_context: PartContext for spec-driven limit derivation and
                         channel traceability.
            instruments: Dictionary of instrument instances for mock configuration.
            mock_instruments: Whether using mock instruments.
            channel_store: Optional ChannelStore for direct writes of numeric data.
            session_id: Session this harness's contexts belong to. Production paths
                (pytest plugin, connect.py, slot_runner) pass the active session;
                test paths can leave None when the blob-observation path isn't
                exercised.
        """
        self._config = config or {}
        self._logger = logger
        self._step_name = step_name
        self._part_context = part_context
        self._instruments = instruments or {}
        self._mock_instruments = mock_instruments
        self._channel_store = channel_store
        self._session_id = session_id
        self._test_level_mock = self._config.get("mocks", {})

        # Parse retry config
        if retry is not None:
            self._retry = retry
        elif self._config.get("retry"):
            retry_data = self._config["retry"]
            if isinstance(retry_data, RetryConfig):
                self._retry = retry_data
            else:
                self._retry = RetryConfig.model_validate(retry_data)
        else:
            self._retry = RetryConfig()

        # Parse limits
        self._limits: dict[str, MeasurementLimitConfig | Limit] = {}
        if limits is not None:
            self._limits = limits
        elif "limits" in self._config:
            for name, limit_config in self._config["limits"].items():
                if isinstance(limit_config, (Limit, MeasurementLimitConfig)):
                    self._limits[name] = limit_config
                else:
                    self._limits[name] = MeasurementLimitConfig.model_validate(limit_config)

        # Expand vectors from config
        vectors_config = self._config.get("vectors", {})
        if isinstance(vectors_config, list):
            # Explicit list of vectors
            self._vectors = expand_vectors(vectors_config)
        elif vectors_config:
            self._vectors = expand_vectors(vectors_config)
        else:
            # No vectors config = single empty vector
            self._vectors = [Vector(_index=0)]

        # Current execution state
        self._current_vector: Vector | None = None
        self._current_step_index: int = -1
        # 0-based retry counter. Set to 0 for the original execution and
        # incremented per retry inside the harness retry loop. Stamped onto
        # the per-vector TestVector + each MeasurementRecorded event.
        self._retry_index: int = 0

        # Hierarchical context: run → step → vector
        self._run_context: Context = Context(harness=self, channel_store=self._channel_store)
        self._step_context: Context | None = None
        self._vector_context: Context | None = None
        self._prev_vector_context: Context | None = None

    @property
    def vectors(self) -> list[Vector]:
        """Expanded vectors for iteration."""
        return self._vectors

    @property
    def current_vector(self) -> Vector | None:
        """Currently executing vector."""
        return self._current_vector

    @property
    def retry_config(self) -> RetryConfig:
        """Retry configuration."""
        return self._retry

    @property
    def context(self) -> Context:
        """Current active context (vector > step > run).

        Returns the most specific active context:
        - During vector execution: vector context (inherits from step and run)
        - During step but outside vector: step context (inherits from run)
        - Outside step: run context

        This is the primary API for setting and reading context values.
        """
        if self._vector_context is not None:
            return self._vector_context
        if self._step_context is not None:
            return self._step_context
        return self._run_context

    @property
    def run_context(self) -> Context:
        """Run-level context, persists across all steps and vectors."""
        return self._run_context

    def prompt(self, message: str, prompt_type: str = "confirm", **kwargs: Any) -> Any:
        """Show an operator prompt.

        Args:
            message: Prompt message (supports {param} formatting from current vector).
            prompt_type: Type of prompt ("confirm", "choice", "input").
            **kwargs: Additional prompt config (choices, timeout_seconds, etc.)

        Returns:
            Prompt result (True for confirm, selected choice, or input value).
        """
        if self._current_vector:
            message = message.format(**self._current_vector.params())

        config = PromptConfig(
            message=message,
            prompt_type=prompt_type,  # type: ignore
            choices=kwargs.get("choices"),
            timeout_seconds=kwargs.get("timeout_seconds"),
        )
        return ask(config)

    def _resolve_limit(self, name: str) -> Limit | None:
        """Resolve limit for a measurement name.

        Resolution order:
        1. Per-vector _limits (if current vector has _limits.{name})
        2. Direct Limit object in self._limits
        3. MeasurementLimitConfig with direct values
        4. MeasurementLimitConfig with spec ref (uses PartContext)
        5. PartContext characteristic lookup (name matches char_id)

        Args:
            name: Measurement name.

        Returns:
            Resolved Limit or None if no limit configured.
        """
        # Check per-vector _limits first
        if self._current_vector:
            vector_limits = self._current_vector.get("_limits", {})
            if name in vector_limits:
                vl = vector_limits[name]
                if isinstance(vl, Limit):
                    return vl
                if isinstance(vl, dict):
                    # Try as MeasurementLimitConfig first, then direct Limit
                    config = MeasurementLimitConfig.model_validate(vl)
                    return self._resolve_limit_config(config)
                if isinstance(vl, MeasurementLimitConfig):
                    return self._resolve_limit_config(vl)

        # Check test-level explicit limits
        if name in self._limits:
            limit_config = self._limits[name]

            # Direct Limit object
            if isinstance(limit_config, Limit):
                return limit_config

            # MeasurementLimitConfig - resolve based on type
            if isinstance(limit_config, MeasurementLimitConfig):
                result = self._resolve_limit_config(limit_config)
                if result is not None:
                    return result

        # Try PartContext direct lookup (measurement name = characteristic ID)
        if self._part_context:
            try:
                conditions = {}
                if self._current_vector:
                    conditions = self._current_vector.params()

                return self._part_context.get_limit(name, **conditions)
            except (KeyError, ValueError):
                pass  # No matching characteristic

        return None

    def _resolve_limit_config(self, config: MeasurementLimitConfig) -> Limit | None:
        """Resolve a MeasurementLimitConfig to a Limit.

        Args:
            config: The limit configuration to resolve.

        Returns:
            Resolved Limit or None.
        """
        # Direct limit values
        direct = config.to_limit()
        if direct is not None:
            # Apply comparator from config if set
            if config.comparator and direct.comparator != config.comparator:
                direct = direct.model_copy(update={"comparator": config.comparator})
            return direct

        # Characteristic-only resolution (no tolerance — fetch the
        # characteristic's spec band straight off the active context).
        if config.characteristic and self._part_context:
            try:
                conditions = {}
                if self._current_vector:
                    conditions = self._current_vector.params()

                guardband = config.guardband_pct or 0.0
                return self._part_context.get_limit(
                    config.characteristic,
                    guardband_pct=guardband,
                    comparator=config.comparator,
                    limit_low=config.low,
                    limit_high=config.high,
                    **conditions,
                )
            except (KeyError, ValueError):
                pass  # Fall through

        # Callable resolution
        if config.callable:
            return self._resolve_callable_limit(config.callable)

        return None

    def _resolve_callable_limit(self, callable_str: str) -> Limit:
        """Resolve a callable limit from a dotted module path or inline Python.

        Args:
            callable_str: Either a dotted module path
                (e.g., "myproject.limits.output_voltage") or inline
                Python code (e.g., "Limit(high=ctx.get_param('vin') * 0.01,
                units='V')")

        Returns:
            Resolved Limit object.
        """
        # Detect inline Python vs module path
        # Inline Python contains: newlines, Limit(, return, if, =, operators
        is_inline = any(
            marker in callable_str
            for marker in ("\n", "Limit(", "return ", "if ", " = ", " + ", " - ", " * ", " / ")
        )

        if not is_inline and "." in callable_str:
            # Module path → import and call
            return self._call_module_function(callable_str)
        else:
            # Inline Python → eval with context
            return self._eval_inline_limit(callable_str)

    def _call_module_function(self, dotted_path: str) -> Limit:
        """Import and call a function by its dotted module path.

        Args:
            dotted_path: e.g., "myproject.limits.output_voltage"

        Returns:
            Limit returned by the function.
        """
        # Split into module and function
        module_path, func_name = dotted_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        # Call with context
        return func(self.context)

    def _eval_inline_limit(self, code: str) -> Limit:
        """Evaluate inline Python code to produce a Limit.

        .. warning::
            Executes arbitrary Python via ``eval``/``exec``.  Only use with
            trusted config files written by the test engineer — never with
            user-supplied or network-sourced strings.

        Args:
            code: Python expression or statements that return a Limit.

        Returns:
            Limit object from evaluation.
        """
        # Build namespace with useful values
        namespace = {
            "Limit": Limit,
            "ctx": self.context,
            **self.context.params,
        }

        # Handle multi-line code (need exec + return capture)
        if "\n" in code or code.strip().startswith("if "):
            # Wrap in function to capture return
            wrapped = "def __limit_fn__():\n"
            for line in code.strip().split("\n"):
                wrapped += f"    {line}\n"
            wrapped += "__result__ = __limit_fn__()"
            exec(wrapped, namespace)
            return namespace["__result__"]
        else:
            # Simple expression
            return eval(code, namespace)

    def measure(
        self,
        name: str,
        value: float | None,
        units: str | None = None,
        limit: Limit | None = None,
        uut_pin: str | None = None,
        instrument_channel: str | None = None,
        fixture_connection: str | None = None,
    ) -> Measurement:
        """Record a measurement for the current vector.

        Args:
            name: Measurement name.
            value: Measured value.
            units: Units (optional, uses limit.units if available).
            limit: Explicit limit (optional, overrides config lookup).
            uut_pin: UUT pin being measured (optional, auto-resolved from spec).
            instrument_channel: Instrument channel used (optional).
            fixture_connection: Named fixture connection used (optional).

        Returns:
            Measurement object with outcome set.
        """
        # Ensure value is float
        if value is not None and not isinstance(value, float):
            value = float(value)

        # Resolve limit
        resolved_limit = limit or self._resolve_limit(name)

        # Resolve channel traceability from PartContext if not provided
        resolved_uut_pin = uut_pin
        resolved_instrument_channel = instrument_channel
        resolved_fixture_connection = fixture_connection

        if self._part_context and not all([uut_pin, instrument_channel, fixture_connection]):
            pin_info = self._part_context.get_pin_info(name)
            if pin_info:
                resolved_uut_pin = resolved_uut_pin or pin_info.get("uut_pin")
                resolved_instrument_channel = resolved_instrument_channel or pin_info.get(
                    "instrument_channel"
                )
                resolved_fixture_connection = resolved_fixture_connection or pin_info.get(
                    "fixture_connection"
                )

        # Create measurement
        measurement = Measurement(
            name=name,
            value=value,
            units=units or (resolved_limit.units if resolved_limit else None),
            limit_low=resolved_limit.low if resolved_limit else None,
            limit_high=resolved_limit.high if resolved_limit else None,
            limit_nominal=resolved_limit.nominal if resolved_limit else None,
            limit_comparator=resolved_limit.comparator if resolved_limit else None,
            characteristic_id=resolved_limit.characteristic_id if resolved_limit else None,
            spec_ref=resolved_limit.spec_ref if resolved_limit else None,
            uut_pin=resolved_uut_pin,
            instrument_channel=resolved_instrument_channel,
            fixture_connection=resolved_fixture_connection,
        )

        # Check limits
        measurement.check_limit()

        # Add to current vector (resolved from contextvar)
        current_tv = get_current_vector()
        if current_tv is not None:
            current_tv.measurements.append(measurement)

        # Stream to event log via logger (logger handles outcome updates).
        # The no-logger branch is for unit-testing the harness lifecycle
        # without an event-log fixture — vector outcome cascades but
        # step/run cascade is skipped (no logger context to read them
        # from). Production harness usage always has a logger.
        if self._logger is not None:
            self._logger.log_measurement(measurement)
        elif current_tv is not None and measurement.outcome is not None:
            current_tv.outcome = escalate_outcome(current_tv.outcome, measurement.outcome)

        return measurement

    def _record_result(
        self, result: float | dict[str, float] | tuple[str, float] | list[float] | None
    ) -> None:
        """Record a result from test function (return or yield).

        Handles:
        - dict: Multiple named measurements
        - tuple (name, value): Single named measurement
        - list/tuple of values: Use limit keys in order
        - single value: Measurement name inferred from spec ref, limit key, or step name
        - None: No measurement

        Name resolution order for single values:
        1. spec ref from limit config (if MeasurementLimitConfig with ref:)
        2. limit key (if exactly one limit)
        3. step name (fallback)
        """
        if result is None:
            return
        elif isinstance(result, dict):
            for name, value in result.items():
                self.measure(name, value)
        elif isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
            # Named tuple: (name, value)
            name, value = result
            self.measure(name, value)
        elif isinstance(result, (list, tuple)):
            # Multiple values without names: use limit keys in order
            limit_keys = list(self._limits.keys())
            for i, val in enumerate(result):
                meas_value = float(val) if val is not None else None
                if i < len(limit_keys):
                    self.measure(limit_keys[i], meas_value)
                else:
                    self.measure(f"{self._step_name}_{i}", meas_value)
        else:
            # Single value: infer name from spec ref → limit key → step name
            name = self._infer_measurement_name()
            self.measure(name, result)

    def _infer_measurement_name(self) -> str:
        """Infer measurement name from limits config.

        Resolution order:
        1. spec ref from limit config (if MeasurementLimitConfig with ref:)
        2. limit key (if exactly one limit)
        3. step name (fallback)
        """
        if len(self._limits) == 1:
            limit_key = next(iter(self._limits.keys()))
            limit_config = self._limits[limit_key]
            # Prefer the characteristic ID when the config is a typed
            # MeasurementLimitConfig — that's the structured FK the
            # measurement should stamp.
            if isinstance(limit_config, MeasurementLimitConfig) and limit_config.characteristic:
                return limit_config.characteristic
            # Otherwise use the limit key itself
            return limit_key
        return self._step_name

    def _reset_mock_state(self) -> None:
        """Reset mock state flags on all instruments.

        This ensures that mocks behave normally when no explicit
        mock values are configured for a vector.
        """
        for inst in self._instruments.values():
            if hasattr(inst, "reset_mock_state"):
                inst.reset_mock_state()

    def _configure_mocks(self, mock_config: dict[str, Any]) -> None:
        """Configure mock instruments with values from config.

        Args:
            mock_config: Dict mapping "instrument.method" to values.
                        Example: {"dmm.measure_voltage": 3.3, "psu.measure_current": 0.5}

        If a value is callable, it will be wrapped to receive the current
        vector context as a keyword argument:

            def dynamic_voltage(cmd, *, context=None):
                load = context.get_param("load_current", 0) if context else 0
                return str(3.3 - load * 0.1)

            _mocks:
              inst.query: !callable dynamic_voltage
        """
        for key, value in mock_config.items():
            if "." not in key:
                continue
            inst_name, measurement = key.split(".", 1)
            if inst_name in self._instruments:
                inst = self._instruments[inst_name]
                if hasattr(inst, "set_mock_value"):
                    # Wrap callables to inject context
                    if callable(value):
                        value = self._wrap_mock_callable(value)
                    inst.set_mock_value(measurement, value)

    def _wrap_mock_callable(self, fn: Callable) -> Callable:
        """Wrap a mock callable to inject the current context.

        Args:
            fn: Original callable that may accept context kwarg

        Returns:
            Wrapped callable that passes current vector context
        """
        harness = self  # Capture reference for closure

        def wrapper(*args, **kwargs):
            # Inject context if not already provided
            if "context" not in kwargs:
                kwargs["context"] = harness._vector_context
            return fn(*args, **kwargs)

        return wrapper

    def _get_mock_config_for_vector(self, vector: Vector) -> dict[str, Any]:
        """Get mock configuration for a vector.

        Resolution order:
        1. Vector-level ``_mocks`` (per-vector config)
        2. Test-level ``mocks`` (constant for all vectors)
        """
        vector_mock = vector.get("_mocks", {})
        if vector_mock:
            return vector_mock
        if self._test_level_mock:
            return self._test_level_mock
        return {}

    @contextmanager
    def run_vector(self, vector: Vector) -> Iterator[TestVector]:
        """Context manager for executing a single vector with retry support.

        Handles:
        - Creating TestVector record
        - Setting up vector context as child of step/run context
        - Configuring mocks for this vector (when mock=True)
        - Retry logic on failure
        - Finalizing vector timing

        Args:
            vector: Vector to execute.

        Yields:
            TestVector object for the execution.

        Example:
            for vector in harness.vectors:
                with harness.run_vector(vector) as tv:
                    harness.context.observe("temp_probe.temp", 24.8)
                    harness.measure("voltage", dmm.measure())
        """
        self._current_vector = vector
        self._retry_index = 0

        # Configure mocks for this vector if using mocks
        if self._mock_instruments and self._instruments:
            # Reset mock state from previous vector
            self._reset_mock_state()
            # Apply mock config for this vector
            mock_config = self._get_mock_config_for_vector(vector)
            if mock_config:
                self._configure_mocks(mock_config)

        # Create vector context as child of step (or run if no step)
        parent_context = self._step_context or self._run_context
        self._vector_context = Context(
            parent=parent_context,
            prev=self._prev_vector_context,
            harness=self,
            channel_store=self._channel_store,
        )

        # Seed the per-vector context with this vector's params; the
        # ``.params`` property below then merges them with parent-chain
        # values, which is why set() and read() look redundant but aren't.
        self._vector_context.set_params(vector.params())

        # Create TestVector record
        test_vector = TestVector(
            index=vector.get("_index", 0),
            params=self._vector_context.params,  # merged with parent chain
            retry=self._retry_index,
            max_retries=self._retry.max_retries,
            started_at=_utcnow(),
        )
        # Add to current step if logging
        current_step = get_current_step()
        if current_step is not None:
            current_step.vectors.append(test_vector)

        # Set contextvars for concurrency-safe resolution. The context
        # var lets observer.read stamp this vector's out_<channel> on
        # first write per (vector, channel) — item 5 / Position 2.
        vector_token = push_current_vector(test_vector)
        context_token = push_current_context(self._vector_context)
        try:
            yield test_vector
        except AssertionError as e:
            test_vector.outcome = Outcome.FAILED
            msg = str(e) or "assertion failed"
            test_vector.error_message = msg
            m = Measurement(name="assert", value=None, outcome=Outcome.FAILED)
            test_vector.measurements.append(m)
            if self._logger is not None:
                self._logger.log_measurement(m)
            raise
        except Exception as e:
            test_vector.outcome = Outcome.ERRORED
            test_vector.error_message = str(e)
            raise
        finally:
            # Snapshot context into TestVector before clearing
            test_vector.params = self._vector_context.params
            test_vector.observations = self._vector_context.observations
            test_vector.ended_at = _utcnow()
            reset_current_context(context_token)
            reset_current_vector(vector_token)
            # Save current context for next vector's change detection
            self._prev_vector_context = self._vector_context
            self._vector_context = None
            self._current_vector = None

    def run_with_retry(
        self,
        vector: Vector,
        test_fn: Callable[[Vector], Any],
    ) -> TestVector:
        """Run a test function for a vector with retry support.

        Args:
            vector: Vector to test.
            test_fn: Test function that takes vector and returns value or yields
                    (name, value) tuples.

        Returns:
            Final TestVector after all attempts.
        """
        last_vector: TestVector | None = None

        # 0-based: retry=0 is the original attempt; retry=N is the Nth retry.
        # Loop runs ``max_retries + 1`` times total (1 original + max_retries retries).
        for retry in range(self._retry.max_retries + 1):
            self._retry_index = retry

            try:
                with self.run_vector(vector) as test_vector:
                    test_vector.retry = retry
                    last_vector = test_vector

                    result = test_fn(vector)

                    # Handle generator (streaming measurements via yield)
                    if hasattr(result, "__iter__") and hasattr(result, "__next__"):
                        for item in result:
                            self._record_result(item)
                    else:
                        self._record_result(result)
            except Exception:
                pass  # run_vector already recorded outcome + error

            assert last_vector is not None
            # Check if passed
            if last_vector.outcome == Outcome.PASSED:
                break

            # Retry delay — only sleep if there's another retry left
            if retry < self._retry.max_retries and self._retry.delay > 0:
                time.sleep(self._retry.delay)

        assert last_vector is not None
        return last_vector

    @contextmanager
    def step(self, name: str | None = None, description: str | None = None) -> Iterator[TestStep]:
        """Context manager for a test step.

        Creates a TestStep and adds it to the logger if available.
        Also creates a step-level context that inherits from the run context.

        Args:
            name: Step name (defaults to harness step_name).
            description: Step description.

        Yields:
            TestStep object.
        """
        identity = get_current_code_identity()

        step = TestStep(
            name=name or self._step_name,
            description=description,
            started_at=_utcnow(),
            node_id=identity.get("node_id"),
            file=identity.get("file"),
            module=identity.get("module"),
            class_name=identity.get("class_name"),
            function=identity.get("function"),
            markers=identity.get("markers"),
        )
        # Create step context as child of run context
        self._step_context = self._run_context.child()

        # Register with logger and emit event via public API
        if self._logger is not None:
            self._current_step_index = self._logger.register_step(step)
            step.instrument_arrays = self._logger.step_instrument_arrays
            self._logger.emit_step_started(step, self._current_step_index)

        # Set contextvar for concurrency-safe resolution
        step_token = push_current_step(step)
        try:
            yield step
        finally:
            step.ended_at = _utcnow()

            # Compute step outcome from vectors
            for tv in step.vectors:
                step.outcome = escalate_outcome(step.outcome, tv.outcome)

            # Emit StepEnded event via public API
            if self._logger is not None:
                self._logger.emit_step_ended(step, self._current_step_index)

            reset_current_step(step_token)
            self._step_context = None

    def run_all(
        self,
        test_fn: Callable[[Vector], Any],
        step_name: str | None = None,
    ) -> TestStep:
        """Run test function across all vectors.

        Convenience method that creates a step, iterates vectors, handles
        retries, and returns the completed step.

        Args:
            test_fn: Test function that takes vector and returns/yields measurements.
            step_name: Name for the test step.

        Returns:
            Completed TestStep with all vectors.
        """
        with self.step(name=step_name) as test_step:
            for vector in self._vectors:
                self.run_with_retry(vector, test_fn)

        return test_step
