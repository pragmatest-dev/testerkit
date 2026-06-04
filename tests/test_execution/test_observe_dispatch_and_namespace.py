"""Item 6 (dispatch refinement) + item 16 (``namespace=`` kwarg) — C3a.

Item 6 — close the dispatch gaps where ``classify_value`` was sending
channel-shaped values to FileStore:

- ``Waveform`` was ``blob`` (no ``tolist``) → FileStore as ``.npz``.
  Post-item-6: verb-layer unpack routes to ChannelStore as a typed
  array write (``wf.Y`` + ``sample_interval=wf.dt``). ``t0`` /
  ``Waveform.attributes`` have no row-level home in today's schema
  (open per design doc §15) — dropped with a ``RuntimeWarning`` when
  non-default.

- ``list[str]`` was ``blob`` (first element didn't match the int/float
  predicate) → FileStore as ``.pkl`` via pickle fallback. The C2
  typed-str-array schema support was unreachable. Post-item-6:
  ``classify_value`` loosens to accept any bool/int/float/str leaf in
  arrays.

Item 16 — ``namespace=`` kwarg on ``observe`` (and ``verify``). Pure
prefix sugar: the effective channel_id / file name / event name /
observations key becomes ``"{namespace}.{key}"``. Nothing automatic;
opt-in convenience for grouping cross-test concerns under one bucket.

Per CLAUDE.md test conventions: ``resolve_data_dir()`` (canonical) +
uuid4 session_ids for per-test isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from litmus.data.channels.store import ChannelStore
from litmus.data.data_dir import resolve_data_dir
from litmus.data.files import _reset_for_tests, get_filestore
from litmus.data.models import Waveform
from litmus.data.ref import classify_value
from litmus.execution.harness import Context, TestHarness


@pytest.fixture(autouse=True)
def _reset_filestore_singleton() -> None:
    """Per-test FileStore singleton resolution."""
    _reset_for_tests()


@pytest.fixture
def context_with_channel_store(tmp_path: Path):
    """A Context with session_id + a real wired channel store.

    The ChannelStore lives under ``tmp_path`` and is per-test
    isolated (uuid4 session_id, flush_threshold=1000, no daemon
    server). Matches existing test_channel_store.py conventions.
    """
    session_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=1000)
    store.open()
    harness = TestHarness(session_id=session_id, channel_store=store)
    ctx = Context(harness=harness, channel_store=store)
    yield ctx, store
    store.close()


# --------------------------------------------------------------------- #
# classify_value: list[str] and list[bool] reach ChannelStore            #
# --------------------------------------------------------------------- #


class TestClassifyValueLoosened:
    def test_string_list_classified_as_numeric_array(self) -> None:
        """Pre-item-6: ``blob``. Post-item-6: ``numeric_array`` → ChannelStore."""
        assert classify_value(["IDLE", "RUNNING", "DONE"]) == "numeric_array"

    def test_bool_list_classified_as_numeric_array(self) -> None:
        assert classify_value([True, False, True]) == "numeric_array"

    def test_mixed_first_element_str_still_numeric_array(self) -> None:
        """Heterogeneous lists: classification picks up on first element."""
        # First is str → numeric_array (typed-str-leaf path).
        assert classify_value(["a", "b"]) == "numeric_array"

    def test_empty_list_stays_blob(self) -> None:
        """Empty list has no first element; can't classify the leaf — stays blob."""
        assert classify_value([]) == "blob"

    def test_numeric_array_unchanged(self) -> None:
        """Existing numeric_array path doesn't regress."""
        assert classify_value([1.0, 2.0, 3.0]) == "numeric_array"
        assert classify_value([1, 2, 3]) == "numeric_array"

    def test_bytes_still_blob(self) -> None:
        """Raw bytes is still a blob (not iterable of leaves)."""
        assert classify_value(b"raw bytes") == "blob"


# --------------------------------------------------------------------- #
# observe(Waveform) routes to ChannelStore (item 6)                      #
# --------------------------------------------------------------------- #


class TestObserveWaveformRoutesToChannelStore:
    def test_simple_waveform_writes_channel_array(self, context_with_channel_store) -> None:
        ctx, store = context_with_channel_store
        wf = Waveform(dt=1e-6, Y=[1.0, 2.0, 3.0])

        ctx.observe("scope.cap", wf)

        uri = ctx._observations["scope.cap"]
        assert uri.startswith("channel://")
        # The channel actually got the Y array as the row's value
        result = store.query("scope.cap")
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]
        assert result.column("sample_interval")[0].as_py() == 1e-6

    def test_waveform_with_t0_round_trips_to_sampled_at(self, context_with_channel_store) -> None:
        """Waveform.t0 → ChannelStore row's ``sampled_at`` column (no data loss)."""
        from datetime import UTC, datetime  # noqa: PLC0415

        ctx, store = context_with_channel_store
        t0 = datetime(2026, 6, 3, 12, 34, 56, tzinfo=UTC)
        wf = Waveform(t0=t0, dt=1e-6, Y=[1.0, 2.0])

        ctx.observe("scope.cap", wf)

        result = store.query("scope.cap")
        assert result.column("sampled_at")[0].as_py() == t0

    def test_waveform_with_attributes_round_trips_to_channel_descriptor(
        self, context_with_channel_store
    ) -> None:
        """Waveform.attributes → ChannelDescriptor.attributes (no data loss)."""
        ctx, store = context_with_channel_store
        wf = Waveform(dt=1e-6, Y=[1.0], attributes={"units": "V", "channel": "ch1"})

        ctx.observe("scope.cap", wf)

        descriptor = store._registry["scope.cap"]
        assert descriptor.attributes == {"units": "V", "channel": "ch1"}

    def test_waveform_with_default_t0_and_no_attributes(self, context_with_channel_store) -> None:
        """Bare ``Waveform(dt=..., Y=[...])`` writes with sampled_at=None and empty attributes."""
        ctx, store = context_with_channel_store
        wf = Waveform(dt=1e-6, Y=[1.0, 2.0])

        ctx.observe("scope.cap", wf)

        result = store.query("scope.cap")
        assert result.column("sampled_at")[0].as_py() is None
        assert store._registry["scope.cap"].attributes == {}


# --------------------------------------------------------------------- #
# observe(list[str]) and observe(list[bool]) reach ChannelStore          #
# --------------------------------------------------------------------- #


class TestObserveTypedLeafArrays:
    def test_string_array_writes_channel(self, context_with_channel_store) -> None:
        """``list[str]`` lands in ChannelStore via the typed-str-leaf path."""
        ctx, store = context_with_channel_store
        ctx.observe("status.stream", ["IDLE", "RUN", "DONE"])

        uri = ctx._observations["status.stream"]
        assert uri.startswith("channel://")
        result = store.query("status.stream")
        assert result.column("value")[0].as_py() == ["IDLE", "RUN", "DONE"]

    def test_bool_array_writes_channel(self, context_with_channel_store) -> None:
        ctx, store = context_with_channel_store
        ctx.observe("digital.bits", [True, False, True, True])

        uri = ctx._observations["digital.bits"]
        assert uri.startswith("channel://")
        result = store.query("digital.bits")
        assert result.column("value")[0].as_py() == [True, False, True, True]


# --------------------------------------------------------------------- #
# observe namespace= prefix (item 16)                                    #
# --------------------------------------------------------------------- #


class TestObserveNamespace:
    def test_namespace_prefixes_scalar_observation(self, context_with_channel_store) -> None:
        """Scalar with namespace lands under the prefixed key."""
        ctx, _ = context_with_channel_store
        ctx.observe("voltage", 3.31, namespace="psu_under_test")

        # Effective key in observations is "{namespace}.{name}"
        assert "psu_under_test.voltage" in ctx._observations
        assert ctx._observations["psu_under_test.voltage"] == 3.31
        # The bare key isn't there
        assert "voltage" not in ctx._observations

    def test_namespace_prefixes_channel_observation(self, context_with_channel_store) -> None:
        """Array with namespace lands as a channel under the prefixed channel_id."""
        ctx, store = context_with_channel_store
        ctx.observe("samples", [1.0, 2.0, 3.0], namespace="dmm_a")

        assert "dmm_a.samples" in ctx._observations
        uri = ctx._observations["dmm_a.samples"]
        # URI references the namespaced channel_id
        assert "dmm_a.samples" in uri
        result = store.query("dmm_a.samples")
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]

    def test_namespace_none_is_no_prefix(self, context_with_channel_store) -> None:
        """namespace=None or omitted → key stays as-is."""
        ctx, _ = context_with_channel_store
        ctx.observe("voltage", 3.31)  # no namespace kwarg
        assert "voltage" in ctx._observations

        ctx.observe("current", 0.5, namespace=None)
        assert "current" in ctx._observations

    def test_namespace_empty_string_is_no_prefix(self, context_with_channel_store) -> None:
        """Empty namespace string → bare key (falsy, treated as no namespace)."""
        ctx, _ = context_with_channel_store
        ctx.observe("voltage", 3.31, namespace="")
        assert "voltage" in ctx._observations
        assert "." not in next(iter(ctx._observations))

    def test_namespace_separator_is_dot(self, context_with_channel_store) -> None:
        """Convention: dot separator (matches role-prefix convention in observers)."""
        ctx, _ = context_with_channel_store
        ctx.observe("voltage", 3.31, namespace="psu")
        assert "psu.voltage" in ctx._observations

    def test_namespace_two_observations_distinct_channels(self, context_with_channel_store) -> None:
        """Two ``observe("voltage", v, namespace=X|Y)`` produce two distinct channels."""
        ctx, store = context_with_channel_store
        ctx.observe("voltage", 3.31, namespace="psu_a")
        ctx.observe("voltage", 5.02, namespace="psu_b")

        assert "psu_a.voltage" in ctx._observations
        assert "psu_b.voltage" in ctx._observations
        # Both bare keys absent
        assert "voltage" not in ctx._observations


# --------------------------------------------------------------------- #
# observe(blob) with namespace prefixes the FileStore artifact name      #
# --------------------------------------------------------------------- #


class TestObserveBlobWithNamespace:
    def test_namespace_prefixes_filestore_artifact(self, context_with_channel_store) -> None:
        ctx, _ = context_with_channel_store
        ctx.observe("screenshot", b"\x89PNG\r\n\x1a\n", namespace="scope_a")

        uri = ctx._observations["scope_a.screenshot"]
        assert uri.startswith("file://")
        # The artifact name in FileStore reflects the namespaced name
        path = get_filestore()._resolve_uri(uri)
        assert path is not None
        assert "scope_a.screenshot" in path.name


# --------------------------------------------------------------------- #
# Numpy array via observe (parametrized regression guard)                #
# --------------------------------------------------------------------- #


class TestObserveNumpyArrays:
    def test_int_ndarray_observation_routes_to_channelstore(
        self, context_with_channel_store
    ) -> None:
        """C2 + item 6: typed int ndarray reaches ChannelStore."""
        ctx, store = context_with_channel_store
        arr = np.array([1, 2, 3], dtype=np.int64)
        ctx.observe("counter", arr)

        uri = ctx._observations["counter"]
        assert uri.startswith("channel://")
        result = store.query("counter")
        assert result.column("value")[0].as_py() == [1, 2, 3]


# --------------------------------------------------------------------- #
# verify accepts namespace= kwarg (routing change deferred)              #
# --------------------------------------------------------------------- #


class TestVerifyNamespaceKwargAccepted:
    def test_verify_protocol_signature_includes_namespace(self) -> None:
        """The ``VerifyFn`` Protocol's signature accepts namespace=
        for type-checker support.

        Routing-side polymorphic dispatch on verify (non-scalar values)
        is deferred to a follow-up PR per the C3a scope decision; this
        PR only locks the signature so the kwarg is callable.
        """
        from litmus.execution.verify import VerifyFn

        # Protocol __call__ has the namespace parameter in its
        # signature (the type system check). Inspect via the
        # __call__ annotations.
        annotations = VerifyFn.__call__.__annotations__
        assert "namespace" in annotations


# --------------------------------------------------------------------- #
# Convenience: _resolve_uri helper for inspection                        #
# --------------------------------------------------------------------- #


def _expected_file_dir(session_id: str):
    """Reproduce FileStore's on-disk layout for cross-checks."""
    today = datetime.now(UTC).date().isoformat()
    return resolve_data_dir() / "files" / today / session_id
