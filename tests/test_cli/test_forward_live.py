"""The live pusher in ``testerkit forward`` (docs/41 §3.4, §8 T3): cadence with a fake
clock, drop-on-failure, eviction, the HTTP call, and the ``--live/--no-live`` flag.
The server side does not exist yet, so every push goes to a fake."""

from __future__ import annotations

import email.message
import io
import json
import threading
import time
import urllib.error
from typing import Any

import pytest
from click.testing import CliRunner

from testerkit.cli import forward_cmd
from testerkit.cli.forward_cmd import LivePusher, _post_live
from testerkit.cli.root import main
from testerkit.data.events import RunMaterialized
from testerkit.data.live_rows import LivePush, LivePushResponse
from tests.test_data.live_streams import (
    RUN_ID,
    SESSION_ID,
    as_dicts,
    representative_events,
    run_started,
    swept_step_events,
)

RID = str(RUN_ID)


class FakeClock:
    """Monotonic clock the test advances by hand; also the ``perf`` clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeServer:
    """Records pushes; answers from ``responses`` (default: 200, unwatched)."""

    def __init__(self) -> None:
        self.pushes: list[LivePush] = []
        self.response = LivePushResponse()
        self.fail = False
        self.attempts = 0

    def __call__(self, push: LivePush) -> LivePushResponse:
        self.attempts += 1
        if self.fail:
            raise urllib.error.URLError("boom")
        self.pushes.append(push)
        return self.response


def _pusher(clock: FakeClock, server: FakeServer, **kw: Any) -> LivePusher:
    # A frozen ``perf`` clock: projection cost 0, so the 5 % CPU self-limit stays out of
    # the way of the cadence tests (it has its own test).
    return LivePusher(server, clock=clock, perf=lambda: 0.0, wall_ns=lambda: 1, **kw)


def _feed(pusher: LivePusher, events: list[Any]) -> None:
    for evt in as_dicts(events):
        pusher.on_event(evt)


def _sweep(n: int, *, t: float = 1, finish: bool = False) -> list[Any]:
    return swept_step_events("sweep", 0, vectors=n, t=t, finish=finish)


def test_first_change_pushes_immediately_then_throttles_to_5s_unwatched() -> None:
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(2)])
    pusher.tick()
    assert len(server.pushes) == 1  # leading edge: no added delay
    assert server.pushes[0].header is not None and server.pushes[0].manifest is not None

    clock.t = 0.5
    _feed(pusher, _sweep(3)[-3:])  # more events for the same run
    pusher.tick()
    assert len(server.pushes) == 1  # inside T = 5 s
    clock.t = 4.9
    pusher.tick()
    assert len(server.pushes) == 1
    clock.t = 5.0
    pusher.tick()
    assert len(server.pushes) == 2  # at last_push + T


def test_watched_response_tightens_cadence_to_1s() -> None:
    clock, server = FakeClock(), FakeServer()
    server.response = LivePushResponse(watched=True)
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    assert len(server.pushes) == 1
    clock.t = 0.5
    _feed(pusher, _sweep(2)[-3:])
    pusher.tick()
    assert len(server.pushes) == 1
    clock.t = 1.0
    pusher.tick()
    assert len(server.pushes) == 2
    server.response = LivePushResponse(watched=False)  # viewer left
    clock.t = 1.5
    _feed(pusher, _sweep(3)[-3:])
    clock.t = 2.0
    pusher.tick()  # this push is answered "unwatched"
    assert len(server.pushes) == 3
    clock.t = 3.0
    _feed(pusher, _sweep(4)[-3:])
    pusher.tick()
    assert len(server.pushes) == 3  # back to T = 5 s


def test_lease_push_after_30s_silence_is_header_only() -> None:
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    clock.t = 29.9
    pusher.tick()
    assert len(server.pushes) == 1
    clock.t = 30.0
    pusher.tick()
    lease = server.pushes[1]
    assert lease.header is not None and not lease.upserts and not lease.deletes
    assert lease.header.state == "running"
    clock.t = 59.9
    pusher.tick()
    assert len(server.pushes) == 2  # the lease restarts the 30 s silence clock


def test_header_heartbeat_every_30s_while_rows_keep_changing() -> None:
    """docs/41 §2.1: the header goes out as a 30 s lease heartbeat even when row
    pushes never leave 30 s of silence — otherwise the server's 90 s lease lapses
    mid-run (the web shows "stalled") and ``current_step_path`` freezes."""
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    header_times = [0.0]
    for i in range(2, 26):  # a new sweep point every 5 s for 2 minutes
        clock.t = 5.0 * (i - 1)
        _feed(pusher, _sweep(i)[-3:])
        before = len(server.pushes)
        pusher.tick()
        if any(p.header is not None for p in server.pushes[before:]):
            header_times.append(clock.t)
    gaps = [b - a for a, b in zip(header_times, header_times[1:], strict=False)]
    assert header_times[-1] >= 90.0
    assert max(gaps) <= 30.0


def test_overdue_lease_against_a_failing_server_keeps_the_throttle() -> None:
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    server.fail = True
    for step in range(300, 600):  # ticks every 0.1 s from t = 30 s to 60 s
        clock.t = step / 10
        pusher.tick()
    assert server.attempts - 1 <= 30 / 5 + 1  # about one try per T = 5 s, not per tick


def test_failed_push_is_dropped_and_the_next_one_carries_current_state() -> None:
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(2)])
    server.fail = True
    pusher.tick()  # must not raise
    assert server.pushes == []
    server.fail = False
    clock.t = 1.0
    pusher.tick()
    assert server.pushes == []  # dropped, not retried inside T
    clock.t = 5.0
    pusher.tick()
    assert len(server.pushes) == 1
    push = server.pushes[0]
    assert push.manifest is not None and len(push.upserts) == len(push.manifest)


def test_finalized_response_stops_pushing_that_run() -> None:
    clock, server = FakeClock(), FakeServer()
    server.response = LivePushResponse(finalized=True)
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    assert len(server.pushes) == 1
    for t in (5.0, 40.0, 100.0):
        clock.t = t
        _feed(pusher, _sweep(2)[-3:])
        pusher.tick()
    assert len(server.pushes) == 1


def test_run_materialized_evicts_and_stops_pushing() -> None:
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    _feed(
        pusher,
        [
            RunMaterialized(
                session_id=SESSION_ID, run_id=RUN_ID, materializer="parquet", destination="x"
            )
        ],
    )
    clock.t = 100.0
    pusher.tick()
    assert len(server.pushes) == 1  # not even a lease


def test_resync_response_makes_the_next_push_carry_the_manifest() -> None:
    clock, server = FakeClock(), FakeServer()
    server.response = LivePushResponse(resync=True)
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    server.response = LivePushResponse()
    clock.t = 5.0
    _feed(pusher, _sweep(2)[-3:])
    pusher.tick()
    assert server.pushes[1].manifest is not None
    clock.t = 10.0
    _feed(pusher, _sweep(3)[-3:])
    pusher.tick()
    assert server.pushes[2].manifest is None


def test_stale_response_is_not_committed() -> None:
    clock, server = FakeClock(), FakeServer()
    server.response = LivePushResponse(stale=True)
    pusher = _pusher(clock, server)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    server.response = LivePushResponse()
    clock.t = 5.0
    pusher.tick()
    assert len(server.pushes) == 2
    assert {d.row_id for d in server.pushes[1].upserts} == {
        d.row_id for d in server.pushes[0].upserts
    }


def test_expensive_projection_widens_the_interval_to_20x() -> None:
    """Self-limit (docs/41 §3.4): projection + diff over 5 % of T => T = 20 x cost.
    The first pass of a run is a one-off catch-up and is not counted."""
    clock, server = FakeClock(), FakeServer()
    perf_calls = iter(x * 0.5 for x in range(1000))  # every measured pass costs 0.5 s
    pusher = LivePusher(server, clock=clock, perf=lambda: next(perf_calls), wall_ns=lambda: 1)
    _feed(pusher, [run_started(), *_sweep(1)])
    pusher.tick()
    assert len(server.pushes) == 1
    clock.t = 5.0  # first pass cost is ignored: T is still 5 s
    _feed(pusher, _sweep(2)[-3:])
    pusher.tick()
    assert len(server.pushes) == 2  # ...and this pass is measured: 0.5 s => T = 10 s
    clock.t = 14.9
    _feed(pusher, _sweep(3)[-3:])
    pusher.tick()
    assert len(server.pushes) == 2
    clock.t = 15.0
    pusher.tick()
    assert len(server.pushes) == 3


def test_run_ended_pushes_the_final_state_immediately() -> None:
    """RunEnded bypasses the throttle: the ended header goes out on the same tick."""
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, representative_events(ended=False))
    pusher.tick()
    assert server.pushes[0].header is not None and server.pushes[0].header.state == "running"
    _feed(pusher, representative_events(ended=True)[-1:])
    clock.t = 0.1  # well inside T = 5 s
    pusher.tick()
    header = server.pushes[1].header
    assert header is not None and header.state == "ended" and header.run.outcome == "failed"


def test_materialized_flushes_the_ended_state_then_evicts() -> None:
    """RunEnded and run.materialized arrive back to back (the local materialization
    takes milliseconds): the ended state still goes out once before pushing stops."""
    clock, server = FakeClock(), FakeServer()
    pusher = _pusher(clock, server)
    _feed(pusher, representative_events(ended=False))
    pusher.tick()
    _feed(pusher, representative_events(ended=True)[-1:])
    _feed(
        pusher,
        [
            RunMaterialized(
                session_id=SESSION_ID, run_id=RUN_ID, materializer="parquet", destination="x"
            )
        ],
    )
    clock.t = 0.1
    pusher.tick()
    assert len(server.pushes) == 2
    ended = server.pushes[1].header
    assert ended is not None and ended.state == "ended"
    clock.t = 100.0
    pusher.tick()
    assert len(server.pushes) == 2  # evicted: no lease either


class _FakeEventStore:
    def __init__(self) -> None:
        self.replay: str | None = None
        self.callback: Any = None
        self.unsubscribed = False

    def on_event(self, callback: Any, *, replay: str = "matching", **_: Any) -> Any:
        self.callback, self.replay = callback, replay

        def unsubscribe() -> None:
            self.unsubscribed = True

        return unsubscribe


def test_thread_attaches_with_unmaterialized_replay_and_pushes() -> None:
    server, store = FakeServer(), _FakeEventStore()
    pusher = LivePusher(server)
    pusher.start(lambda: store)  # type: ignore[arg-type,return-value]
    try:
        deadline = time.monotonic() + 5
        while store.callback is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert store.replay == "unmaterialized_runs"
        for evt in as_dicts([run_started(), *_sweep(1)]):
            store.callback(evt)
        while not server.pushes and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.pushes and server.pushes[0].run_id == RID
    finally:
        pusher.stop()
    assert store.unsubscribed


def test_attach_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forward_cmd, "_LIVE_ATTACH_RETRY_S", 0.01)
    attempts: list[int] = []

    def factory() -> Any:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("no events daemon")
        return _FakeEventStore()

    pusher = LivePusher(FakeServer())
    pusher.start(factory)
    try:
        deadline = time.monotonic() + 5
        while len(attempts) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(attempts) >= 3
    finally:
        pusher.stop()


# ---------------------------------------------------------------------------
# The HTTP call
# ---------------------------------------------------------------------------


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def test_post_live_targets_the_run_route_with_the_station_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        seen.update(
            url=req.full_url,
            method=req.get_method(),
            headers=dict(req.header_items()),
            body=req.data,
        )
        return _Resp(json.dumps({"watched": True, "extra_field": 1}).encode())

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", fake_urlopen)
    push = LivePush(run_id=RID, seq=7)
    resp = _post_live("https://srv.example/", "tok", push, timeout=3.0)
    assert resp.watched is True
    assert seen["url"] == f"https://srv.example/ingest/live/runs/{RID}"
    assert seen["method"] == "POST"
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["headers"]["Content-type"] == "application/json"
    assert json.loads(seen["body"])["seq"] == 7


def test_post_live_treats_409_as_a_finalized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float) -> _Resp:
        raise urllib.error.HTTPError(
            req.full_url,
            409,
            "conflict",
            email.message.Message(),
            io.BytesIO(b'{"finalized": true}'),
        )

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", fake_urlopen)
    assert _post_live("https://srv", "t", LivePush(run_id=RID, seq=1), timeout=1).finalized

    def server_error(req: Any, timeout: float) -> _Resp:
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(forward_cmd.urllib.request, "urlopen", server_error)
    with pytest.raises(urllib.error.HTTPError):
        _post_live("https://srv", "t", LivePush(run_id=RID, seq=1), timeout=1)


def test_forward_has_live_flag_default_on() -> None:
    result = CliRunner().invoke(main, ["forward", "--help"])
    assert result.exit_code == 0
    assert "--live / --no-live" in result.output
    param = next(p for p in forward_cmd.forward.params if p.name == "live")
    assert param.default is True
    assert threading.active_count() >= 1
