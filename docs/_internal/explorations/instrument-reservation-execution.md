# Instrument reservation (#11 + #26) — execution diary

**Branch:** `feat/0.3.0-instruments`. **Status:** plan locked 2026-06-29, executing.
Design contract + progress log for the step-lease re-grain, reservation events, and the
#26 at-rest instrument-set "used" half. Encodes decisions settled in discussion; the
research/problem statement lives in [`instrument-reservation.md`](instrument-reservation.md)
and [`instrument-access-model.md`](instrument-access-model.md). Internal doc — file:line
citations allowed here, never in `docs/`.

---

## Scope — LOCKED

**In:** #11 (step-lease re-grain + reservation events) and #26 (the reservation "used" half
of the step/vector at-rest instrument set; the fixture-param "available" half already shipped
in 0.3.0). Built on the **existing two-path architecture** (file-lock + orchestrator server)
— no coordinator.

**Out (deferred, do NOT build):**
- **#18** station-scoped join-on-connect coordinator (fencing + discovery) — its own branch.
- **#12** read-only observe — depends on #18.
- **Take-control policy / UI affordance** (who/when can preempt) — §9 `[open]`, lives in #18.
- **Grain config knob** (command/step/run as configuration) — step is hardcoded for pytest;
  interactive clients choose grain via explicit `reserve`/`release`. §3.2 `[open]` deferred to
  **#29** (run-vs-step the motivating case: run-hold = cross-step continuity / set-once
  instruments like a thermal chamber; config scope per-instrument/role vs profile vs global
  still open).
- **#17** reservation *temporal* spans on run parquet — HARD: keep out of the C5
  `instruments` `list<struct>` (per-acquisition spans shatter its RLE). Separate fact later.

## Decisions — LOCKED

1. **Grain = the step**, hardcoded for the pytest path (the §3.1 boundary: one execution of
   the step body; in-body loops/redo run under one held lease; re-dispatch gets a fresh lease).
2. **Connection stays session-scoped; only the reservation cycles per step.** Driver load +
   `*IDN?` + verify is expensive and stays in `self._active` (`pool.py`). The file lock /
   server lease is what acquires-and-releases per step.
3. **Reservation is one explicit primitive** (`reserve(role)` / a release call, or a context
   manager). The pytest plugin auto-wraps it at the step boundary; an interactive client calls
   it explicitly and thereby chooses its own grain (per-action ≈ command, or held span ≈ step).
4. **Re-entrant locks**, keyed by holder identity: a holder re-acquiring its own resource (the
   step lease, then an inner per-command acquire) must not self-deadlock.
5. **Split timeout from heartbeat.** `timeout` = how long to wait for a *live* holder
   (sentinel `-1` = forever); a separate liveness/heartbeat watchdog force-releases a *dead*
   holder. Today `server.py` conflates them in `_HEARTBEAT_TIMEOUT=15.0`.
6. **Server emits the lease** (`instrument.reserved`/`released` per client, per step) — not
   just an internal lock — so the event bus stays a faithful projection of the shared path.
7. **New events are additive** → event-catalog MINOR bump 1.0→1.1 (no adapter; rides the C3
   per-store versioning seam shipped in 0.3.0).
8. **#26 used-half sources the at-rest set.** *Which* instruments a step reserved refines the
   step/vector `instruments` set (the fixture-param "available" set is the current source).
9. **API shape = VISA/IVI-aligned (prior-art-backed, 2026-06-29 survey).** `connect` (open
   driver, session-scoped, lockless) is separate from `reserve` (acquire exclusive,
   step-scoped, optional). Decisions:
   - **A — defaults differ by entry point (LOCKED 2026-06-29).** pytest fixture =
     **connect-only** + per-step auto-wrap reserve (author manages nothing; pure VISA
     open-defaults-unlocked, safe because the plugin reserves each step). Interactive
     `instrument(role)` = **reserve-by-default** (protective — a human will footgun and #18's
     mandatory fencing isn't here yet); `reserve=False` for connect-only with self-managed
     reservations. Both expose the full `reserve`/`release_reservation`/`with reservation()`
     grain API. (`instrument(role, reserve=...)` mirrors PyVISA `open(access_mode=...)`.)
     Verb is `connect`/`disconnect` (not open/close — that's the underlying driver's verb;
     connect/disconnect is the Litmus session layer, also consistent with `litmus.connect(station)`).
   - **B — reserve is refcounted re-entrant** (= VISA `viLock` lock-count; the lone direct
     precedent — DAQmx `-50103`/Ophyd `RedundantStaging` error instead, TestStand deadlocks).
     Non-negotiable: recursion is the only model that doesn't deadlock on nested steps.
   - Primary explicit surface = context manager `with conn.reservation(role, timeout=...):`
     (= PyVISA `lock_context()`); explicit `reserve`/`release_reservation` pair for the UI
     take/release case. `timeout=0`/`-1` = VISA `VI_TMO_IMMEDIATE`/`INFINITE`.
   - Future #12 observe = the **lease-free** watch path (subscribe to channels, no reserve) —
     the sanctioned way a UI watches a running test; "watch" is NEVER attach-without-reserve
     (= VISA `VI_SHARED_LOCK` + Ophyd read-default).
10. **Reserve-around-the-step, NOT encapsulated (LOCKED 2026-06-29).** The lease acquire
    (which may block) happens BEFORE the step's execution clock / StepStarted; step duration
    measures execution only. Contention is captured as `waited_ms` on `InstrumentReserved`
    (authority-stamped); hold = `Released − Reserved`. A reservation timeout errors the step
    at **setup** (resource-unavailable), distinct from a DUT/execution failure. Ordering:
    [context + needed instruments] → InstrumentReserved(waited_ms) → StepStarted/clock → body
    → StepEnded → InstrumentReleased. Phase 4 reorders the current hook accordingly.
11. **#26 used-set = the RESERVE signal (Option A, LOCKED 2026-06-30).** The at-rest
    per-step "used" set is sourced from what was actually reserved, not from the
    fixture-declared "available" set (the old stamp) and not from `connect`. `reserve` is the
    universal anchor across every real use path: fixture auto-wrap (per-step reserve), ad-hoc
    `context.instrument(role)` (reserve-by-default), and explicit `with reservation()`. `connect`
    is rejected as the anchor — too broad (fires for #12 observe + session-scoped availability)
    and too coarse (session grain, can't say *which step*). **The one seam, decided:** the
    deliberate `reserve=False` opt-out (and #12 observe) emits Connected-not-Reserved and is
    therefore **outside** the used-set — opting out of the lease opts out of the used-set, by the
    same deliberate act. Honest consequence accepted: a `reserve=False` instrument can still
    carry per-measurement instrument refs, so the reserved/used set ("exclusively taken") and the
    measurement refs ("where data flowed") may disagree only for an explicitly-opted-out
    instrument — two distinct lenses. Rejected: B (per-instrument `reserved` flag = C5 schema
    add), C (used only in events). Wiring is Phase 4b.
12. **Reservation timeout/grain config deferred to #29 (LOCKED 2026-06-30).** Auto-reserve means
    the test author can't pass `timeout=` at the call, so the value must be configurable — but
    NOT in this branch. Phase 4 auto-wrap ships with a hardcoded `timeout=0` (fail-fast,
    consistent with the `instrument()`/`reserve()` default). The configurable surface is a typed
    `reservation:` block carrying **both** grain (run vs step, #29) and timeout, resolved through
    the existing inline<sidecar<profile cascade plus a per-instrument default in station YAML
    (the resource's contention property — thermal chamber patient, dedicated DMM fail-fast).
    Grain + timeout are one concept ("reservation policy") and land together in #29, not dribbled
    in half-now/half-later. Phase 4 scope = the per-step *mechanism* only.

## North-star acceptance scenario (2026-06-29)

The end-to-end demo the cluster builds toward: a pytest run **and** an interactive UI
(`litmus serve`), same project, sharing one instrument.
1. UI watches the run **live** via channels (exists today).
2. While tests run, UI **observes** the instrument/test signals **lease-free** (channel
   subscribe, no reserve) — **#12**.
3. When **no test is running**, UI **controls** the instrument directly (reserve-by-default +
   drive) — **#11** interactive path; the control output **publishes to the SAME channels the
   test used, but with `run_id=None`** (bench/no-run data on one logical signal).

Validates #11 (reserve/fence: control when free, blocked when held), #12 (observe), and
channels (run + no-run samples on one logical channel).

**Scope insight — this scenario does NOT need #18.** Control happens when the test isn't
running, and single-session hardware enforces the shape for free: while the pytest process
holds the instrument open, the UI process can't open a second session, so "watch during" MUST
be channel-observe (#12), and "control after" is the dedicated-path reserve once the run
releases. #18 (mid-run shared control) is not exercised here. Cheap validation target:
**#11 + #12 + one channels detail**, no coordinator.

**To confirm for #12/demo (NOT #11; verify against source then):** (a) interactive control
emits to channels with `run_id=None` (observer/channel-store wiring); (b) same-channel
continuity across the test session and the UI session — channel identity scoping (if keyed
`(session_id, channel_id)`, "same channel" needs station-scoped identity or a cross-session
query; ties `project_followup_channel_isolation_per_slot`).

## Guardrails — authority vs projection, no unreserved access (2026-06-29)

- **Events are projection, NOT state management.** The authority of "held now?" and every
  grant/deny is the **flock** (dedicated) + the **in-memory lease/refcount table** (server),
  answered synchronously there. `instrument.reserved`/`released` events are emitted *by* that
  authority as a faithful projection for **discovery** (non-holders) and **timing/history**
  (utilization, #17). **No code path may read the event bus to arbitrate a lock** — no drift,
  the standing rule. The scenario refusal (below) comes from the lease table, not the bus.
- **Reservation events are emitted by the POOL (client side), both paths (2026-06-29
  clarification of §7 "server must emit").** The dedicated path has no server, and the
  server process lacks the client's session/run/event-log context — so the pool emits
  `instrument.reserved`/`released` after the authority grants (file lock on dedicated; server
  lease on shared). "Server must emit the lease" = every server lease-grant produces a
  reserved event, emitted by the granted client/pool. One consistent model; captures
  interactive no-run reservations (`run_id=None`).
- **`connect` and `reserve` emit INDEPENDENTLY — two events, never a fused one.** `connect`
  emits `InstrumentConnected` (always, on connect); `reserve` emits `InstrumentReserved` (only
  on reserve). Auto-reserve (`instrument(role, reserve=True)` / the `acquire` composite) emits
  BOTH, ordered Connected→Reserved (`connect.py:296-299`); connect-only (`reserve=False`; the
  pytest fixture's session connect) emits Connected ONLY. **`connect` must NEVER imply
  reserved** — a fused event couldn't represent connect-without-reserve, so the two-event
  separation is what makes reserve-optional representable. Verified: `pool.connect`→
  `_emit_connected`→`InstrumentConnected`; `pool.reserve`→`InstrumentReserved`.
- **#26 at-rest used-set is EVENT-SOURCED — the reservation events are the source, the
  accumulator derives (decided 2026-06-30, supersedes the earlier "in-process, not event
  replay" wording, which was wrong for the at-rest path).** The parquet is built from the
  event stream: the accumulator reads `instrument_records` off the step/vector start events
  (`_event_accumulator.py:511,817`) and the run-grain inventory off `InstrumentConnected`
  (`:155,:365,:668`). There is **no in-process side-channel to the parquet** — everything
  reaches a row through an event. So the reserved instruments MUST ride the event stream,
  keyed to the execution, or the accumulator cannot place them. (The "don't replay the bus to
  *arbitrate a lock*" rule still holds — that's about authority, not about deriving at-rest
  rows. Authority = flock + lease table; at-rest derivation = events. Both true, different
  concerns.)
  - **The design (I), LOCKED — STEP-grained.** `InstrumentReserved` / `InstrumentReleased`
    carry the step-execution key — `run_id` (already stamped) + `step_index` + `step_retry`.
    **NO `vector_index`**: a reservation is per step (decision 10 — one lease per step,
    vectors run under it), and **vectors inherit the step's instrument set** — they are not
    reserved per-vector. The accumulator groups reservation events by `(run_id, step_index,
    step_retry)`, unions the instruments onto the step, and the step's vectors inherit that
    set. This is **timing-independent**: a reserve that fires *before* `StepStarted`
    (auto-wrap) and one *during the body* (ad-hoc `context.instrument()`) both self-stamp the
    same step key, so the accumulator attaches both to the right step regardless of order —
    the goal: correct instruments on the correct step from reservations before OR after it.
  - **`reserve` MUST emit for mocks (required by (I), not optional).** Today `reserve`
    early-returns for mocked / resource-less roles BEFORE the emit (`pool.py:162-164`), so a
    mock run (the whole suite + most dev/CI/demo) would emit zero reservation events and the
    used-set would be empty exactly on the common case. Since the event is the source, a mock
    reserve must emit `InstrumentReserved` (`waited_ms=0`) — truthful (the mock *was*
    reserved) and necessary. The lock is still skipped for mocks; only the emit moves.
  - **Step identity at emit time:** in-body reserves read the open step from
    `get_current_step()`; the pre-step auto-wrap reserve takes the upcoming
    `step_index`/`step_retry` the wrapper already has before `start_step`
    (`hooks.py:1379,1400-1403`) and passes them into `reserve()`. Caller supplies the key →
    the pool stays a pure stamp+emit layer (no `instruments → execution` import).
  - **Released is symmetric** — same execution key, so a step's holds bracket cleanly as
    `Reserved(key)` … `Released(key)`.
- **No unreserved operation.** Every operation runs under a lease — an explicit step/run
  lease the client holds, or an implicit per-RPC (command-grain) lease the server takes for
  one call. Operating without an explicit reserve succeeds **only when uncontended**; while a
  client holds an **exclusive** reservation, others' operations are refused/blocked
  (`ResourceInUse` naming the holder, or wait per timeout) — VISA `VI_EXCLUSIVE_LOCK` (cached
  attribute reads still allowed; bus operations not). There is **no back door** where "not
  reserving" grants access to a held instrument — that would let an out-of-band client mutate
  state between a test's steps (the §4.3 corruption). Watch a running test via observe (#12,
  channels, lease-free); take control via reserve-with-handoff at a step boundary (#18).
  Enforcing member-vs-out-of-band fencing is **#18** (deferred); in the #11-only branch the
  per-run server isn't discoverable out-of-band, so the scenario can't cleanly occur yet.

## Social contract — last line, not first (2026-06-29)

Cooperative locking always has a "be a good citizen" layer (VISA lives on it; DAQmx's
"unreserve when idle" is good-citizen advice), and pre-#18 ours is cooperative/advisory — a
determined out-of-band process can bypass it, served instruments aren't fenced (§4.3). But
guidance must **reinforce mechanism, not substitute for it**: the good path is the default
(auto-wrap reserves per step; interactive `instrument()` reserve-by-default; observe is the
obvious watch path; `with` can't leak), so docs cover the model + opt-out cases, never carry
the protection. Docs must state the cooperative-until-#18 limitation honestly (don't sell
isolation we don't enforce yet). → **#31** (control model + good-citizen guidance);
#11's user-facing slice is part of #11's definition of done.

**Why cooperative is acceptable pre-#18:** the value is concentrated at the front door —
`connect(station)` is where you get configured drivers, channels, events, sync, and
traceability — so the participating path is also the easiest/richest path; the incentive
gradient points toward going through it, not bypassing. Connecting gets you *available*
(connect); good-citizen guidance turns that into *reserved* when driving shared hardware.
Residual the carrot misses (→ #18): raw non-Litmus scripts that never connect, and the §4.3
served-path hole. Bonus: because everyone already wants the front door, #18's "connecting =
joining the coordinator" is a free, invisible upgrade.

## Self-healing / fool-proof reservation — REQUIRED (2026-06-29)

A forgotten release must NEVER permanently wedge an instrument; the LabVIEW governing lesson
is **a reservation must not outlive the process that owns it**. Layered defense:
- **Common path manages no locks.** connect-only default + per-step auto-wrap (`try/finally`,
  releases on exception/abort) means a normal pytest author never calls reserve/release.
  Manual reserve is opt-in (interactive/UI). Context manager balances the rest.
- **Scope-end sweep** (spec into Phase 4): step-end releases the step's reservations;
  session-end (`release_all`) releases anything still held. A forgotten explicit reserve is
  reclaimed at the enclosing boundary.
- **Process death** frees the `flock` automatically (Phase 1, done) — dedicated path can't
  leak across process lifecycle. **Client death / hung-but-alive on the server lease** →
  heartbeat reclaim (Phase 2, decision #5).
- **Live-but-hung file-lock holder** = the one un-auto-breakable case (`flock` can't tell hung
  from busy). Honest handling: operator-visible `ResourceInUse` (holder pid/station/since) +
  a **supervised operator force-release** — never a silent auto-break. → **#30** (list +
  force-release CLI/API, the safe "reset VISA sessions" equivalent). Complements **#23**.

## Verified seam map (current source, 2026-06-29)

- `instruments/locks.py:71` `acquire_resource(resource, meta, timeout=0)` → `FileLock`;
  `:102` `release_resource`; `:107` `lock_holder`. No re-entrancy; no heartbeat watchdog.
- `instruments/pool.py:104` `acquire_resource(...)` stored at `self._locks[role]`
  (session-long); driver in `self._active[role]`; released in `release`/`release_all`.
- `connect.py:250` `StationConnection.instrument(role, timeout=0)` → `pool.acquire`;
  `:296` `release(role)` → `pool.release` (disconnect + unlock — name already taken).
- `instruments/server.py:68` `self._locks[resource]=threading.Lock()`; `:182-216`
  per-RPC acquire/release in `_handle_client`; `:28` `_HEARTBEAT_TIMEOUT=15.0` (conflated);
  `concurrent=True` roles skip (`:62`).
- `data/events.py:395` `InstrumentConnected` (the field-carrying EventBase pattern);
  `:899` `INSTRUMENT_EVENTS = {InstrumentSet, InstrumentConfigure}`; `:943` the event union.
- `pytest_plugin/hooks.py:1346` `pytest_runtest_call`: `start_step`(`:1383`) →
  `yield`(`:1396`) → `end_step`(`:1401`) — the step-body boundary for the auto-wrap.
- `execution/run_scope.py:655` `set_step_instruments(roles)` (fixture-param "available"
  half); `:651` `step_instrument_records`; `:686,:756` `start_step(instrument_roles=...)`.
- `instruments/observer.py` `InstrumentEventBuilder` — the emit path for instrument events.

## Phase plan (agent decomposition)

Sequential — each phase consumes the prior's seam; I verify every agent's output to
file:line before the next phase. Tests land with each phase; full suite + ruff + pyright green.

- **Phase 1 — substrate (Agent A).** `locks.py`: holder-keyed re-entrant acquire; split
  heartbeat watchdog from acquire timeout (`-1` = wait forever for a live holder). Self-
  contained; unit-tested in isolation.
- **Phase 2 — reservation primitive (Agent B, depends on 1).** `pool.py` + `connect.py` +
  `server.py`: decouple connection from reservation; `reserve`/`release_reservation` on the
  dedicated path (cycle the lock, keep the driver); RESERVE/RELEASE lease verbs on the server
  (per-RPC lock demoted to wire guard); timeout/heartbeat split on both paths.
- **Phase 3 — events (Agent C, depends on 2).** `events.py` `InstrumentReserved`/`Released`
  + registry + union + catalog 1.0→1.1; emit at lease boundaries via `InstrumentEventBuilder`
  on both paths.
- **Phase 4 — pytest auto-wrap + #26 (Agent D, depends on 2+3).** `hooks.py`: acquire the
  step lease at `start_step`, release at `end_step`. `run_scope.py`: source the step/vector
  at-rest `instruments` "used" set from the reservation signal, refining the fixture-param set.

## Event noise — assessed, not a blocker

Per step execution: `2` step + `2N` reserve/release + `2M` vector + `2MP` measurement. The
`2N` is **step-grain — it does NOT multiply by M or P** (one held lease spans all vectors/
measurements of an in-body step). In-body sweeps → reservation is ~5–6% of the log. The
only case it climbs is pytest **parametrize** (re-dispatch = a genuine separate lease per
case, §3.1) → ~⅓ in the few-measurement case — but each release is *real* contention, the
signal #11/#26 exist to capture, not noise. Controls already in the design:
- **Step grain** (not per-vector/command) is itself the anti-noise choice (§3.2).
- **`concurrent=True`** instruments (switches/muxes — highest churn) emit zero lease events.
- **UI filters by `event_type`** — reservation is a toggle-able layer (presentation, not
  emission). Never drop the signal to quiet the view.

`[open]` (parked, not this branch): suppress *uncontended solo* reservations (single-process
file-lock, dedicated instrument — nobody can contend). Trades utilization honesty (doc wants
no-run/bench usage captured) for a quieter log. Decide alongside #18's coverage questions.

## Progress log

- 2026-06-29: plan locked; seam map verified against merged 0.3.0 source. Noise assessed
  (step-grain bounds it; `concurrent` opt-out; UI filtering) — not a blocker. Grain config
  knob (run vs step) deferred to **#29**.
- 2026-06-29: **Phase 1 DONE** (`locks.py`). Holder-keyed re-entrant `acquire_resource`
  (refcount registry guarded by a process-global `threading.Lock`); `timeout=-1` = wait for a
  live holder; refcounted `release_resource` with a `key[0] == resource` guard; OS flock
  handles dead-holder recovery (no watchdog at this layer). 5 new tests; callers untouched
  (additive). ruff/pyright(0/0/0)/pytest(11) green, independently re-verified. Uncommitted.
- 2026-06-29: prior-art survey (VISA/IVI, DAQmx, Ophyd, TestStand, resource pools) →
  decisions A (attach-only default) + B (refcounted re-entrant) LOCKED; self-healing
  requirement added; **#30** (operator force-release) created. Phase 2 ready to dispatch.
- 2026-06-29: deep model walk → LOCKED: interactive `instrument()` reserve-by-default
  (fixture stays attach-only); reserve-around-the-step (decision 10); #12 = lease-free observe
  path. Created **#31** (control-model/good-citizen docs); recorded social-contract +
  station-identity-scope (#18 §9.7) findings. Dispatching Phase 2a.
- 2026-06-29: **Phase 2a DONE** (`pool.py` + `connect.py`). Split `attach` (connect-only, no
  lock) / `reserve` (refcounted, no-op for remote+mocked) / `release_reservation`; `acquire`
  now a back-compat composite with cleanup-on-failure; `release` drains all refcounts.
  Facade: `instrument(role, *, reserve=True, timeout=0)` (reserve-by-default), explicit
  `reserve`/`release_reservation`, `@contextmanager reservation()`. ruff/pyright(0/0/0) green;
  432 instrument+connect tests pass (independently re-verified); full suite 2307 per agent.
  Uncommitted. Next: Phase 2b (server lease).
- 2026-06-29: **Phase 2b DONE** (`server.py` + `pool.py`). Per-client refcounted lease
  (`_leases[resource]=(refcount, conn_id)`) is the arbitration grain; `_RESERVE`/`_RELEASE`
  verbs; per-RPC lock demoted to a wire guard that respects the lease (different holder →
  refused; unleased → today's behavior, no regression); reserve-wait split from
  `_DEAD_CLIENT_TIMEOUT`; force-acquire race fixed via `_meta_lock`; leases released on
  disconnect (self-healing). Client side: pool remote branch + `RemoteInstrumentProxy` send
  the verbs. ruff/format/pyright(0/0/0); 415 instrument + 89 slot/multi tests pass
  (independently re-verified, incl. reading the lease logic; the agent's pyright-clean claim
  confirmed — earlier IDE error diagnostics were stale mid-edit snapshots). Next: Phase 3
  (reservation events).
- 2026-06-29: **Phase 3 DONE** (`events.py` + `event_log.py` + `pool.py`). New
  `InstrumentReserved` (role, instrument_id, resource, `waited_ms`) + `InstrumentReleased`;
  registered in `INSTRUMENT_EVENTS` + the `Event` union; `EVENT_LOG_SCHEMA_VERSION` 1.0→1.1.
  Pool emits reserved (`waited_ms` timed via `time.monotonic`) / released on both paths;
  `run_id=None` on interactive; no emit for mocked/resource-less/no-proxy. ruff/format/
  pyright(0/0/0); 1021 instrument+data tests pass (independently re-verified; the "unused
  import" diagnostic was a stale mid-edit snapshot). The pre-commit FULL suite caught two
  registration gaps the subset run missed — new BaseModels must be in the ontology
  (`ontology/litmus.yaml`) and assigned a `_EVENT_CATEGORIES` group (regenerate
  `event-types.md`); both added. Lesson: new event/model types need ontology + reference-docs
  registration, only the full gate catches it. `InstrumentConnected` already IS the "attach"
  event (no separate Attached event needed; the split sharpened it to mean attached/available
  vs Reserved=exclusively-held). Next: Phase 4 (pytest auto-wrap + #26).
- 2026-06-29: **Verb rename DONE** — `attach`→`connect`, `release`→`disconnect`/
  `disconnect_all` across `pool.py`/`connect.py`/plugin/UI/tests + docs (`reserve`/
  `release_reservation` and the events untouched). connect/disconnect is the Litmus session
  layer, distinct from the driver's open/close: a survey (PyMeasure/QCoDeS/Lantz/InstrumentKit/
  PyVISA/OpenTAP/OpenHTF) found open/close dominant AT THE DRIVER LAYER — which is exactly why
  Litmus uses `connect` (avoids collision; matches `litmus.connect(station)`). The proxy is a
  `connect` that does NOT open a driver (borrows the one server-held session). ruff/format/
  pyright(0/0/0); 445 tests pass; stray-name grep clean. Library prior-art for #18
  (RPyC/Pyro5/`multiprocessing.managers`/Tango; verify QCoDeS-removed-remote) parked in
  instrument-reservation.md §9.8. Next: Phase 4.
