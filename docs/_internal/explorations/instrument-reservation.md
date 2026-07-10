# Instrument Reservation & Access Model — research + followup

**Status:** research/design followup consolidating the instrument-centric backlog
(**#11** reservation events/locking grain, **#12** read-only observe), with adjacencies
to **#13** (in-body vector redo) and **#17** (reservation time spans → run parquet).
Captured 2026-06-27 from a source-verified walk of the connect/pool/server/slot-runner
paths. **This is its own branch following the at-rest (0.3.0) cluster — 0.3.0 ships
without it** (see *Release sequencing*). Problem statement the instrument tasks execute
against — not yet an approved plan. **[locked]** = settled in discussion; **[open]** =
unresolved fork (do not auto-decide).

Internal doc — file:line citations and private names are allowed here, not in `docs/`.

---

## The model — session as coordination **[locked 2026-06-27]**

The instrument access model is **three planes behind one facade.** A litmus user (UI,
agent, pytest, bench script) only ever touches the facade.

- **Station = the coordination domain.** Instruments belong to a station; the station is
  the only coherent scope for arbitrating them.
- **Connection to the station = admission.** `litmus.connect(station)` is the act that
  makes you a participant. No connection → no session → not a member → you can't hold,
  request, or be granted an instrument. Connection is **mandatory for control** (the
  coordinator can only grant an *enforced* lease to a member it knows). Pure
  **observation** can also ride the open data plane (channels + reservation events via the
  read paths), but the session is the front door for both.
- **Session = your handle + the collection point.** The session is the facade: it already
  fronts events (`.events()`, `.on_event()`), channel data (`.observe()`,
  `.channel_store`), and instrument access (`.instruments`, `.instrument_server_address`).
  A client asks the session; the session routes to the freshest source. The client never
  learns which backing mechanism answered.
- **Coordinator (server) = enforcement + live authority.** It holds the lease/ref-count
  table in memory and *is* the truth for "held now?" on what it serves. Querying it
  directly beats replaying events — **you don't check the event log if you hold a server
  handle.**
- **Event bus = projection + fallback.** Reservation events are *emitted by* the authority
  (server lease / pool lock), so they're a faithful **projection, never a competing
  record** — no drift (the codebase's standing rule). The bus is the view for anyone *not*
  holding a live handle: cross-process discovery, history, and the no-run
  (interactive/bench) superset.

**The keystone.** Today's server is *ephemeral, per-run, env-addressed* — so a second
`connect()` spins up its **own** uncoordinated pool and races the hardware (§4.3). Promote
it to a **station-scoped coordinator that any connection joins** (start if first, attach if
running) and the uncoordinated path disappears: **connecting *is* joining**, the collision
is gone by construction, and observe / request-control / handoff become things a *member*
does. The step-lease boundary (§3.1) doubles as the safe **handoff point** — you can't yank
an instrument mid-step, so control transfers at a lease boundary.

**Starts-simple fit.** A solo bench needs no coordinator — the file lock stays the
zero-infra default. The coordinator is what *sharing* promotes you to. The `connect()` API
is identical either way; the progression is invisible to the client.

---

## Release sequencing — own branch after 0.3.0 **[decided 2026-06-27]**

**0.3.0 ships without this work.** The axes are orthogonal — 0.3.0 is data *at rest*
(schema reshape + versioning); the coordinator is *runtime* execution-model. The one seam
that could couple them is decoupled by design:

- **New event types** (`instrument.reserved` / `instrument.released`) are a clean
  **additive** change under the C3 (#5) versioning contract — a MINOR event-catalog bump
  (1.0 → 1.1), no adapter. Per-event-type versions are already deferred. So 0.3.0 stamps
  the catalog it has; reservation events join later as 1.1. In fact **C3, shipping *in*
  0.3.0, lays the versioning seam this branch's new events ride** — an argument for
  C3-before-instruments, not for coupling.
- **C5's `instruments` column** stores the instrument set (identity + cal) at the **run, step,
  and vector grains** — each row its own effective set, with the step/vector set sourced from
  fixture parameters / **reservations**. So the reservation signal (*which* instruments a step
  leased — the #11 acquire/release) is a **source of the step's at-rest set**: they integrate,
  they are not disjoint. What *is* excluded from the column is the **temporal** reservation span
  (*when* / how-long), by **#17's HARD RLE constraint** — that lands as a separate event-sourced
  fact/column later. (Corrected 2026-06-29: the prior "inventory only / no overlap with the
  at-rest reshape" framing reflected the wrong run-scoped-only model.)
- **The §4.3 out-of-band collision is pre-existing** — 0.3.0 neither introduces nor worsens
  it. A known limitation, not a 0.3.0 regression. (Worth a changelog "known limitation"
  line; not a blocker.)

So: ship 0.3.0 (C3 + the done at-rest work); the instrument coordinator is the next branch.

---

## 1. Why this doc

A design discussion about "lock around the step" exposed that the instrument access model
is more than the locking grain — it's a two-path architecture (file lock vs server) whose
safety, fairness, and arbitration grain all differ, and whose published surface
(`litmus.connect()`) interacts with running automated tests in ways that aren't fully safe
today. Rather than re-derive this every time an instrument task comes up, the verified
state, the unifying model (above), and the open questions live here.

---

## 2. Verified architecture (today)

### 2.1 Two paths, chosen per role

`InstrumentPool.acquire` (`instruments/pool.py:85-89`) routes on env:

- **Shared** — role in `_LITMUS_SHARED_ROLES` **and** `_LITMUS_INSTRUMENT_SERVER` set →
  `_acquire_remote` → `RemoteInstrumentProxy` to a server. **Skips the file lock**
  ("server handles serialization", `pool.py:154`).
- **Dedicated** — else → `acquire_resource` → per-resource cross-process **file lock**
  (`instruments/locks.py:71`).

### 2.2 The file lock (dedicated path)

- Per-resource OS file lock via `filelock`/`fcntl.flock()` (`locks.py`). Auto-releases on
  process death incl. SIGKILL.
- Lock files under `LITMUS_HOME/locks/` — **machine-global, cross-project** (different
  projects share the namespace because they share physical instruments). Not the project
  data dir.
- Keyed per **resource address** (`GPIB::16::INSTR` → `GPIB__16__INSTR.lock`), not per
  role. Two different instruments don't contend.
- Default acquire `timeout=0` → **fail immediately** with `ResourceInUse` naming the holder
  (pid/station/role/since). Threaded through `connect().instrument(timeout=0)`
  (`connect.py:289`) and `pool.acquire`.
- **Held session-long today** — the pool stores it in `self._locks[role]` and releases at
  teardown (`release_all`). Single machine only (flock doesn't span hosts).

### 2.3 The server (shared path)

- `InstrumentServer` (`instruments/server.py`) holds **one** connected driver per role and
  serves RPC over a localhost socket. Clients get a `RemoteInstrumentProxy`.
- Arbitration is an **in-process `threading.Lock` per resource, taken PER RPC CALL**
  (`server.py:182-188`), with a heartbeat-timeout force-acquire for dead clients (a known
  race, `server.py:196-208`). Roles flagged `concurrent=True` (switches) **skip the lock**
  (`server.py:37-38`).
- The lock is a `threading.Lock`, not a file lock, **because the contenders are threads in
  one process** (the client handler threads). flock would be meaningless there.

### 2.4 Multi-slot orchestration (how "parallel runs" actually work)

`run_multi_slot_session` (`slot_runner.py:628`) → `_run_subprocess_mode`:

1. `detect_shared_instruments(slots)` (`slots.py:106`) returns roles referenced by **≥2
   fixture slots** (declarative, **by role name**: `Counter` over `slot.instrument_roles`,
   `slots.py:117-120`).
2. The **orchestrator** connects each shared, non-mocked driver **once** via
   `load_and_connect` (`slot_runner.py:520`) and stands up **one** `InstrumentServer`
   (`:537`). Mocked shared roles are **not** served — each worker gets its own mock so mock
   state doesn't leak (`:508-509`).
3. `SlotRunner` injects `_LITMUS_INSTRUMENT_SERVER` + `_LITMUS_SHARED_ROLES` into every
   slot subprocess (`slot_runner.py:191-194`).
4. Each slot is a pytest subprocess with its **own** `instruments` fixture → own
   `InstrumentPool`; shared roles resolve to a proxy into the one server, dedicated roles
   take their own file lock.

**Topology, therefore: star (server-mediated), not mesh (peer file-lock arbitration).**
The N slot processes never each grab shared hardware; they funnel into one server-held
connection. "Multiple processes collectively arbitrating via timeouts/reserve/release" is
the *dedicated* path's safety mechanism, never the shared-instrument design.

### 2.5 Connection vs reservation — the disambiguation

The two paths differ in whether connection and reservation are separable:

- **Server path:** already decoupled. The server owns the connection persistently
  (session); the per-RPC lock is the reservation. Re-graining the reservation doesn't move
  the connection.
- **File-lock path:** **fused.** The file lock's lifetime *is* the connection's lifetime
  (`self._locks[role]`, session). There is no separate reservation concept.

**[locked]** For **single-session hardware** (typical GPIB/VISA), the reservation cannot be
made finer than the connection on the file-lock path: releasing the reservation while
keeping your session open means another process can't reserve without opening a *second*
session, which the device refuses. So a reservation finer than the connection requires one
process to own the single connection and lease it — **that is the coordinator/server**. For
**multi-session hardware** (some LXI/SCPI accept N sockets) the file lock *can* be a
reservation decoupled from connection (both connected, take turns). → The grain knob's
realizability is a per-instrument property (session cardinality).

---

## 3. The reservation-grain decision

### 3.1 Boundary = the step-body execution **[locked]**

Reservation is held around **one execution of the step body** (the session-scoped
`instruments` fixture provides the *connection*; the *reservation* cycles per step).
Consequences:

- Vectors produced **within** one step body (an in-body optimization/search loop, and
  **#13**'s in-body vector redo) run **under one held lease** — instrument config state is
  retained across them. Required: per-vector release would let another slot reprogram the
  instrument between a redo's iterations, invalidating the retry. **#11 and #13 must share
  the step-context boundary.**
- Vectors produced by **re-dispatch** (the framework re-enters the step body — e.g. pytest
  parametrize) get **separate** leases → release between. Dividing line: *does control
  leave the step body between vectors?*

Continuity is therefore expressed by **code structure** (loop inside the step = hold;
parametrize = release), not guessed by the framework. The lease boundary is also the safe
**handoff point** for take-control (you can't preempt mid-step).

### 3.2 Grain follows fixture-declaration scope — reentrancy, not a ladder **[locked 2026-07-09]**

**Correction (2026-07-09).** The earlier framing had a "grain ladder" whose `run/session`
rung was justified by the claim *"the only way to express cross-*step* continuity — no code
structure spans steps."* **That claim is false, and the ladder was the wrong model.** A
pytest **class is a code structure that spans steps**: `_ensure_class_container`
(`hooks.py:1281`) opens a **container step** on class-entry and closes it on class-exit; the
method-steps nest under it (`step_path` = `TestSeq/test_efficiency`) and its outcome is a
rollup of children. The class container is open continuously across the gap between methods —
exactly the span the ladder said didn't exist.

So there is **no new grain to name and no user-facing knob.** The reservation grain simply
**follows the scope at which the fixture is declared**, and three facts already in the code
compose to make that work with no new machinery:

1. **The class already is a step.** The container step brackets the sequence
   (`_ensure_class_container` open → `_close_open_class_container` close).
2. **A class-hoisted fixture is already the signal.** A user must go out of their way to
   move an instrument fixture to class scope (`@pytest.mark.usefixtures("dmm")` on the class,
   or a class-scoped fixture). That declaration site *is* the intent to hold across the
   sequence — nothing else needs to express it.
3. **`reserve()` is already re-entrant** for the same `(pid, session_id, role)` holder
   (`pool.py:124` — "increments the refcount without contending; each call requires a
   matching `release_reservation`").

Compose them and the hold falls out for free. Reserve at the step level where the fixture
lives:

- **Fixture on the method** → reserved at the method step, released between methods.
  (Today's behavior — unchanged.)
- **Fixture hoisted to the class** → reserved at the **container step** (open → close). Each
  method still calls `reserve()` for that role, but it's the same `(pid, session, role)`, so
  it's a re-entrant **increment**, not a re-acquire. The refcount never reaches zero between
  methods — the container holds the outer count — so **the lock spans the whole sequence**.
  Container close drops the outer count and releases.

This *is* the "bigger step lock": it's just "lock as step," recursively, with reentrant
refcounts making the nesting continuous. No vocabulary beyond "step" because there is no new
concept — steps nest under steps.

**The only unbuilt piece is one wire.** `_ensure_class_container` does **not** call
`reserve()` today, so the outer refcount is never taken and the chain collapses to
per-method. To realize the model: at container-open, `reserve()` the **class-scoped** roles
(class `usefixtures` ∩ registered roles) keyed to the container's `step_index`; at
container-close, release them. Method-level `reserve()` calls stay exactly as they are and
become reentrant increments for any role the container already holds.

**Status of the step rung itself (verified 2026-07-09): SHIPPED.** The per-step
reserve/release *is live today* and is the current default. The pytest `instruments` fixture
calls `pool.connect(role, ...)` only (`pytest_plugin/__init__.py:683`) — connection, **no
lock** — and the per-step `pytest_runtest_call` hookwrapper calls `pool.reserve(...,
step_index=...)` / `release_reservation(...)` around each method (`hooks.py:1465-1515`). So
the flock genuinely cycles at step grain; nothing holds an outer run-long refcount on the
pytest path. The class/sequence hold is therefore **purely additive on the shipped step
floor** — it needs no run-level grain and touches nothing that already works. `acquire()`
(= `connect` + `reserve`, `pool.py:287`) remains back-compat for `route_manager` /
interactive `connect()`, not the test path.

> **Supersedes §2.2 and §2.5.** Those sections describe the *pre-split* file-lock path where
> the lock was "held session-long" (§2.2) and connection/reservation were "fused" (§2.5).
> No longer true: `connect()` and `reserve()`/`release_reservation()` are separate on the
> file-lock path, and the fixture connects-only. Read §2.2/§2.5 as history; §3.1/§3.2 are the
> current model.

**Deadlock is already handled.** Holding multiple locks across a span is no worse than
`run`-hold (which holds them across the whole run), and the reserve loop already iterates
`roles = sorted(...)` (`hooks.py:1462`) — canonical lock ordering, the standard avoidance
discipline. No extra work.

**The surviving grain choices** (still a real menu, just not a "continuity" ladder):

| Grain | Meaning | Path | Notes |
|---|---|---|---|
| command | per RPC call | server only | exists today (per-RPC lock); too fine for atomic set→read sequences |
| **step** | per step-body execution | both | **default AND current shipped behavior** (see below). The §3.1 locked boundary. Class-hoisted fixtures span their sequence *via reentrancy*, not a distinct grain. |
| run/session | held whole run | both | **not currently wired on the pytest path** (the fixture `connect()`s only — no run-long lock). Was the old behavior when the fixture called `acquire`. Would be the way to hold across *unrelated* classes / the whole run if reintroduced as an override. |
| ~~vector~~ | per vector | — | **excluded** — breaks in-body optimization/redo (§3.1) |

**[open]** Config scope for the command↔step choice: per-instrument/role (contention is a
rig property) vs profile vs `litmus.yaml` global. Lean per-instrument/role. Not decided.
(The sequence-hold is *not* on this axis — it's expressed by fixture scope in test code, not
config.)

---

## 4. Verified defects / cracks

### 4.1 Arbitration grain is per-RPC, not per-step — **#11 core**

The server locks per RPC (`server.py:182`). A multi-command atomic sequence (`set range`
→ `read`) from slot A can be **interleaved by slot B between RPCs**, corrupting the logical
reading. Parallel runs don't crash — the *connection* has one owner — but the *operation*
isn't atomic. Fix: per-RPC lock → **step-duration lease**. The real "parallel runs don't
work as designed" for atomic sequences.

### 4.2 Shared-detection keys on role name, not resource — **narrow, real**

`detect_shared_instruments` counts **role names** across slots (`slots.py:117-120`). Misses:

- The **same physical resource referenced under two different role names** in two slots
  (`slot_1: dmm`, `slot_2: meter`, both `GPIB::16`) → not detected as shared → both take
  the file lock → second slot **blocks the first's whole run** (silent serialization, or a
  timeout failure if configured). No error.
- Any instrument used **outside** the fixture-declared slot connections.

Guard: dedupe/detect by **resolved resource address**, not role name; warn (or merge) on
resource collision across slots.

### 4.3 No station coordinator — server is per-run, env-scoped (fencing + discovery)

*(merges the former fencing + discovery cracks — two faces of one missing thing: a
station-scoped coordinator.)*

- **No fencing.** The automated server connects shared drivers via `load_and_connect`
  directly (`slot_runner.py:520`), **bypassing `acquire_resource`** — so it holds the
  connection but **not** the machine-global file lock. (The *interactive*
  `start_instrument_server` *does* lock, via `pool.acquire`, `connect.py:194` — the two
  startup paths differ.) A served resource is unprotected: an out-of-band
  `connect().instrument(role)` finds the file lock free, opens its **own** session →
  hardware collision, mid-test. **An API/UI user can corrupt a running automated test.**
- **No discovery.** `_LITMUS_INSTRUMENT_SERVER` is propagated **only** parent→child
  (`slot_runner.py:191`). An out-of-band session can't *find* a running server, so it can't
  route to a proxy — it can only collide (above) or be excluded.

**Both vanish if the server becomes a station-scoped coordinator that `connect()` joins**
(see *The model*): no uncoordinated path to fence, and discovery is "you joined on
connect." Live "held now?" comes from the coordinator's lease table; the event bus is the
projection for non-members. *(Precedent: multi-slot sync already coordinates across
processes via EventStore events — `get_sync(event_store)` / `SyncCoordinator`. And the
runs/channels/files daemons are the same "first touch stands up a shared service" shape the
coordinator's lifecycle can follow.)*

---

## 5. Safety status: exclusion vs coexistence

- **Safe serialization across processes? Yes** (dedicated path). An interactive
  `connect().instrument()` is cleanly refused with `ResourceInUse` when a dedicated
  resource is held. No half-open second session.
- **Safe *sharing* across processes? Only among the orchestrator's own spawned slots.** An
  out-of-band process can't join and isn't fenced from served resources (§4.3).
- **Safe *observation* of a running test? No.** The only way in is `pool.acquire`, which
  locks; there is no read-only path. → **#12.**

---

## 6. Read-only observe (#12)

Model: **one writer** per instrument (leases + emits reservation events), **N read-only
observers** that **subscribe to the channel stream** the writer already publishes — no
lock, no second hardware session, invisible to reservation/utilization. "Data-centric
observation" = subscribe to the *data* (channels) plus the *state* (reservation events),
never a hardware connection. The channel live fan-out already exists; the missing seam is a
read-only `connect()`/observe entry that routes to channel subscription instead of
`pool.acquire`. Reachable from a separate process once the coordinator is joinable (§4.3).

---

## 7. Reservation events → utilization + #17 spans

- Emit `instrument.reserved` / `instrument.released` at lease acquire/release (step grain).
  **Event-sourced**, so they capture interactive/bench reservations that produce **no run**
  — the event log is a superset of the run inventory. Accurate basis for asset utilization
  (≈ actual use at step grain), replacing the current UI-side connect/disconnect pairing
  off the raw WAL.
- **Shared-path wrinkle:** reservation today is an in-process `threading.Lock` inside the
  orchestrator's server — invisible to the event log. For accurate events on the shared
  path the **server must emit the lease** (reserved/released per client, per step), not
  just lock internally. Real work inside #11; it's what makes the server a first-class
  participant in the coordination plane (and keeps the event bus a faithful projection).
- **#17** brings reservation time spans onto the run parquet (alongside dialog spans) for
  honest time-loss decomposition. **HARD constraint:** do **not** fold temporal spans into
  the C5 `instruments` inventory `list<struct>` — its dense-per-row RLE depends on a value
  constant across the run; per-acquisition spans vary row-to-row and shatter it. Reservation
  spans are a separate column/fact (likely UNNEST-to-materialized, like C5's two-part shape).

---

## 8. Task map

| Task | Owns | This doc's findings |
|---|---|---|
| **#11** | reservation events + locking grain | §3 (step boundary, grain ladder), §4.1 (per-RPC→step-lease), §4.2 (detection), §7 (events + server-emits-lease) |
| **#12** | read-only observe | §5, §6 — depends on §4.3 (joinable coordinator) |
| **#13** | in-body vector redo | §3.1 (lease wraps in-body loop; shares step-context with #11) |
| **#17** | reservation/dialog spans on run parquet | §7 (event-sourced spans; keep out of C5 column) |
| *(new?)* | **station-scoped join-on-connect coordinator** (fencing + discovery) | §4.3 + *The model* — currently unowned; #12 and safe coexistence assume it |

---

## 9. Open questions

1. **[open]** Coordinator **lifecycle/ownership**: first-connector starts it? a
   `litmus serve`-style station daemon owns it? lazy-start only when contention appears?
   (Rhymes with the runs/channels/files daemons.)
2. **[open]** Does the **file-lock path survive** as the solo zero-infra default, or does
   everything eventually route through the coordinator? (Lean: file lock for solo,
   coordinator for shared.)
3. **[open]** **Take-control policy**: who can take control, when? (observe-always,
   control-when-idle, per-instrument policy — the "connected vs active / coverage vs
   granularity" decision.)
4. **[open]** Grain-config scope (§3.2): per-instrument/role vs profile vs global.
5. **[open]** §4.2 guard: dedupe shared-detection by resolved resource — warn vs auto-merge.
6. **[open]** §4.3 fencing mechanism: file lock vs a coordinator-registered discoverable
   claim (these may be the same thing once the coordinator is station-scoped).
7. **[open]** **Station identity scope (verified 2026-06-29).** Today station resolution is
   project-local, CWD-anchored: `connect()` → `find_station_config` reads the project's
   `stations/` YAML, and `_find_project_config` walks CWD ancestors for `litmus.yaml`
   (`connect.py:488-493,506-533`). There is **no global station registry**. So two processes
   agree on "the same station" only by sharing the project folder — even though the lock
   namespace (`LITMUS_HOME/locks/`, resource-keyed) is already machine-global. **Asymmetry:
   locks global, station identity project-local.** A station-scoped coordinator that
   *out-of-context* processes (an interactive UI started elsewhere) can join therefore needs
   a global station registry + addressing — which collides with data-dir resolution, config
   precedence, and multi-project-on-one-machine semantics. Big blast radius; do not design
   under #11. This is the substrate the "connecting = joining" keystone assumes.
8. **[open]** **Libraries — don't reinvent the proxy/coordinator (2026-06-29).** The
   hand-rolled `RemoteInstrumentProxy` + server reimplement transparent remote objects;
   the coordinator would reimplement discovery. Evaluate before building #18: **RPyC**
   (netref proxies — forward calls AND live remote objects, removing the pickle/flat-API
   limits of our `__getattr__` proxy), **Pyro5** (remote-object proxies + a **name server**
   = the §4.3 discovery piece), stdlib **`multiprocessing.managers`** (`BaseManager`/
   `AutoProxy`; we already use `multiprocessing.connection`). Heavyweight full-vision prior
   art: **Tango Controls** / **EPICS** (discoverable device servers + access control; likely
   too heavy for "starts simple"). Cautionary: **QCoDeS reportedly built then removed** a
   multiprocessing remote-instrument server — VERIFY why before doubling down on the
   hand-rolled path. Relevant to #18 + a possible future `RemoteInstrumentProxy` swap
   (additive-later per req-6), NOT the #11 branch. Real eval happens at #18.
