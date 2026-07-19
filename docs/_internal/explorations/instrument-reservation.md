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

The instrument access model is **three planes behind one facade.** A testerkit user (UI,
agent, pytest, bench script) only ever touches the facade.

- **Station = the coordination domain.** Instruments belong to a station; the station is
  the only coherent scope for arbitrating them.
- **Connection to the station = admission.** `testerkit.connect(station)` is the act that
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
(`testerkit.connect()`) interacts with running automated tests in ways that aren't fully safe
today. Rather than re-derive this every time an instrument task comes up, the verified
state, the unifying model (above), and the open questions live here.

---

## 2. Verified architecture (today)

### 2.1 Two paths, chosen per role

`InstrumentPool.acquire` (`instruments/pool.py:85-89`) routes on env:

- **Shared** — role in `_TESTERKIT_SHARED_ROLES` **and** `_TESTERKIT_INSTRUMENT_SERVER` set →
  `_acquire_remote` → `RemoteInstrumentProxy` to a server. **Skips the file lock**
  ("server handles serialization", `pool.py:154`).
- **Dedicated** — else → `acquire_resource` → per-resource cross-process **file lock**
  (`instruments/locks.py:71`).

### 2.2 The file lock (dedicated path)

- Per-resource OS file lock via `filelock`/`fcntl.flock()` (`locks.py`). Auto-releases on
  process death incl. SIGKILL.
- Lock files under `TESTERKIT_HOME/locks/` — **machine-global, cross-project** (different
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
- Arbitration is **two layers** (verified 2026-07-09):
  1. **Step-duration lease** (the admission gate). A client sends `_RESERVE` → the server
     grants an **exclusive, refcounted, re-entrant-per-connection** lease on the resource
     (`_acquire_lease`, `server.py:247`). While a lease is held, any *other* connection's RPC
     is **refused up front** with `ResourceInUse` before it reaches the lock
     (`server.py:278-292`); `_RELEASE` decrements the refcount (`server.py:259-268`).
  2. **Per-resource `threading.Lock`, taken PER RPC CALL** (`server.py:294-318`), with a
     heartbeat-timeout force-acquire for dead clients (a known race). This is the **original**
     mechanism and is **retained as the floor** — every dispatched RPC still takes and
     releases it. When no lease is held it is the sole arbitration and "serialises all callers
     as before, preserving existing behaviour unchanged" (`server.py:47-48`).
- The lease is the gate *above* the per-call lock, not a replacement for it. Reserved (step)
  callers get an atomic multi-call sequence because others are refused admission; unreserved
  (command-grain) callers fall through to per-call serialization. Roles flagged
  `concurrent=True` (switches) **skip both** (`server.py:41`, `server.py:72-73`).
- The per-call lock is a `threading.Lock`, not a file lock, **because the contenders are
  threads in one process** (the client handler threads). flock would be meaningless there.
  It is not re-entrant, but is safe by construction: held only within one dispatch and never
  nested (never across a lease or another RPC). The lease *is* refcounted/re-entrant per
  connection, which is what composes the step-over-connection nesting.

### 2.4 Multi-slot orchestration (how "parallel runs" actually work)

`run_multi_slot_session` (`slot_runner.py:628`) → `_run_subprocess_mode`:

1. `detect_shared_instruments(slots)` (`slots.py:106`) returns roles referenced by **≥2
   fixture slots** (declarative, **by role name**: `Counter` over `slot.instrument_roles`,
   `slots.py:117-120`).
2. The **orchestrator** connects each shared, non-mocked driver **once** via
   `load_and_connect` (`slot_runner.py:520`) and stands up **one** `InstrumentServer`
   (`:537`). Mocked shared roles are **not** served — each worker gets its own mock so mock
   state doesn't leak (`:508-509`).
3. `SlotRunner` injects `_TESTERKIT_INSTRUMENT_SERVER` + `_TESTERKIT_SHARED_ROLES` into every
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
rig property) vs profile vs `testerkit.yaml` global. Lean per-instrument/role. Not decided.
(The sequence-hold is *not* on this axis — it's expressed by fixture scope in test code, not
config.)

---

## 3.3 Governance — who chooses the sharing mode **[locked 2026-07-15]**

**Two layers, authored in two places — the split every mature T&M/control system uses.**
Prior-art research (2026-07-15, four systems, primary-sourced) found a remarkably consistent
pattern:

| System | Who chooses **exclusivity** | Who authors the **permission / topology** | Read-only observer? |
|---|---|---|---|
| **VISA / PyVISA** (our base layer) | **Client, at connect/lock** (`no_lock`/`exclusive`/`shared`); sharing via a bearer **accessKey** the first locker mints & relays | Nobody — no central authority | **None** |
| **Tango** | **Client** — `DeviceProxy::lock()` (10 s validity, auto-relock, force-unlock) | **Admin** — access-control, read-vs-write per user/host | **Yes** — reads/events never blocked by a write lock |
| **EPICS** | **No client lock** — concurrent writes last-writer-wins | **Admin** — central `.acf` policy (ASG NONE/READ/WRITE) | **Yes** — reads always multi-client |
| **NI / IVI / TestStand** | **Client, in code** — momentary `LockSession`/`UnlockSession` | **Station/admin** — logical-name → instrument mapping | **None** — "a genuine gap in the NI/IVI model" |

Two governance layers fall out, and the ecosystem authors them separately:

1. **Permission + topology (the ceiling)** — *"is this resource coordinated? who may write it?
   what is it?"* → **authored centrally in station config** (mirrors EPICS `.acf`, IVI logical
   names, Tango access-control). Declarative.
2. **Exclusivity (the act)** — *"I am taking control now, for this scope"* → **chosen by the
   client at `connect()` time, scoped to a bracket** (unanimous: VISA / Tango / IVI). Imperative.
   First-mover `INITIALIZE_NEW` (stand up + publish endpoint to the registry) vs late-joiner
   `ATTACH_TO_EXISTING` — independently the exact model NI's cross-process broker arrived at.

**TesterKit decision:** adopt the two-layer split. Config authors the ceiling (per-resource:
coordinated? who may write?); the client opts into the momentary mode at `connect()`
(**write** / **observe**); a config-less loose process (script, custom UI) may opt into
`shared` explicitly at the connect call, and the first connector *publishes* the mode to the
machine-global registry so every later process — any entrypoint, any project — reads it from
there. This is reinforced by our own substrate: **TesterKit sits directly on VISA, whose only
native primitive is client-at-connect locking** — centrally-declared exclusivity would fight
the ground we stand on.

**The observer tier is a control-system import.** Observe-is-default / only-writes-arbitrated
is the Tango/EPICS lineage; the bench lineage (VISA, IVI/NI) has **no observer mode at all**.
TesterKit brings the control-system's best idea into the pytest-bench world (§6).

**BYODriver — the model is driver-agnostic by construction (2026-07-18).** The "instrument" is
an arbitrary Python object the user brings (PyVISA, PyMeasure, vendor SDK, a socket, a mock).
- The **coordination/lease layer already forwards** `getattr`/method calls to *whatever* object
  it holds (§2.3) — it never assumes VISA. The single-owner write-lease works for any driver.
- **We do NOT command-filter a "read-only" handle.** There is no reliable way to know which
  methods of an arbitrary driver mutate hardware, and a "read" is a bus *write* anyway (see §6).
  So the read/write split is enforced by **which plane you're handed** (channel subscription vs
  driver handle), never by policing commands. This is what makes it BYODriver-safe.
- The one requirement BYODriver imposes: a **stable resource-identity string** to key the lock +
  registry (VISA `GPIB::16::INSTR`, socket `IP:port`, or a user-supplied id in config). Given
  any stable identity, the machine-global keying generalizes past VISA unchanged.

---

## 4. Verified defects / cracks

### 4.1 Cross-RPC interleaving — **FIXED via step lease (was #11 core)**

**Original problem.** The server locked only per RPC. A multi-command atomic sequence
(`set range` → `read`) from slot A could be **interleaved by slot B between RPCs**,
corrupting the logical reading. Parallel runs didn't crash — the *connection* had one owner —
but the *operation* wasn't atomic. This was the real "parallel runs don't work as designed"
for atomic sequences.

**Status (verified 2026-07-09): FIXED.** The per-RPC lock was **not replaced** — the
step-duration lease was added as an **admission gate above it** (see §2.3). A reserved caller
holds the lease for the step body; while it does, slot B's RPCs are refused with
`ResourceInUse` before they reach the per-call lock (`server.py:278-292`), so the
`set range → read` sequence is now atomic against other slots. The per-RPC `threading.Lock`
remains as the floor — it is what serializes the *unreserved* (command-grain) path, exactly
as before. So interleaving corruption is only reachable by a caller that issues raw RPCs
**without** reserving; the normal per-step `reserve()` path (and the class-container reserve)
is atomic.

**On the #11 "event-sourcing" residual — it is an *analytics* feature, not a reservation
mechanism (clarified 2026-07-11).** The server already has a complete, authoritative
reservation engine: the live in-memory lease table (`self._leases: resource_key →
(refcount, conn_id)`, `_acquire_lease`/`_release_lease`, `server.py:174-223`), reentrant
per connection, blocking contenders on a condition variable, auto-releasing every lease on
disconnect (`_release_all_leases`, the `finally` at `server.py:325`), and gating the
operation path too (`:278-292`). Locking is correct with **zero** events. Event-sourcing
the lease (§7) buys only *derived outputs* — a durable "who held what, when" for utilization
analytics and the #17 reservation spans — plus a projection for non-member observers. If
reservation analytics is never built, the server needs no lease events at all. So the #11
residual is better read as "reservation **analytics** needs events," not "reservation needs
events."

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
- **No discovery.** `_TESTERKIT_INSTRUMENT_SERVER` is propagated **only** parent→child
  (`slot_runner.py:191`). An out-of-band session can't *find* a running server, so it can't
  route to a proxy — it can only collide (above) or be excluded.

**Both vanish if the server becomes a station-scoped coordinator that `connect()` joins**
(see *The model*): no uncoordinated path to fence, and discovery is "you joined on
connect." Live "held now?" comes from the coordinator's lease table; the event bus is the
projection for non-members. *(Precedent: multi-slot sync already coordinates across
processes via EventStore events — `get_sync(event_store)` / `SyncCoordinator`. And the
runs/channels/files daemons are the same "first touch stands up a shared service" shape the
coordinator's lifecycle can follow.)*

**The coordinator is a *per-host* problem — no station registry needed for it (clarified
2026-07-11).** The file lock already keys on the **resolved resource address**, under a
**machine-global** `TESTERKIT_HOME/locks/` (`locks.py:40,81` — `_sanitize_resource` filenames,
default `platformdirs.user_data_dir`). So cross-project contention on one host is *already*
correct with no station identity at all — two projects opening `GPIB::16::INSTR` hit the same
lock file. It follows that a **single-host coordinator** needs only hostname/convention to be
found (a well-known socket / `TESTERKIT_HOME`-anchored path), exactly like the runs/channels/
files daemons — **not** a global station registry. A registry is required for *only* one
slice: a **station that spans hosts** — networked LXI/SCPI instruments driven from several
machines, or a remote observer/dashboard on a different host — where each machine has its own
`TESTERKIT_HOME/locks/` and so cannot arbitrate via the shared file. That slice is deferrable;
the per-host coordinator (the common bench) can ship without it. **The registry is not a
0.3.2 prerequisite — the cross-host case is.**

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

## 6. Read-only observe (#12) **[locked 2026-07-18]**

Model: **one writer** per instrument (leases + emits reservation events), **N read-only
observers**. A producer gets the **driver handle**; an observer gets a **data subscription**
and *never the driver*. The two intents are different sessions, not one handle throttled two
ways — the read/write split is enforced by **which plane you're handed**, not by policing
commands.

**Enforcement is by absence, not by a read-only allow-list.** The observer holds no driver, so
there is nothing to police — it physically cannot issue a command. This is the *only* robust
mechanism, for two reasons:

- **You can't command-filter an arbitrary (BYODriver) handle.** Nothing tells the forwarder
  which of a vendor driver's methods mutate hardware (§3.3). A "read-only handle" would demand
  users annotate every driver — hostile and unreliable.
- **On a shared instrument there is no safe non-holder "read" anyway** — a read *is* a bus write
  (`MEAS:VOLT?` writes the query, then reads). An observer's own read would inject bytes that
  **interleave with the holder's command sequence** — the exact §4.1 corruption the lease
  prevents. A non-holder live read is indistinguishable from a second writer and must contend
  for the lease. **Product limitation, stated plainly: you cannot issue your own live reads to
  an instrument someone else is driving — you get their published readings, or you wait your
  turn.** (This is *why* Tango/EPICS also forbid raw client I/O on a shared device.)

So observation = subscribe to **what the producer publishes**, via two things TesterKit already
has, split cleanly:

- **Coordinator = write-lease + discovery.** Answers "who is producing on this instrument now,
  under what `session_id` / `instrument_role`?" It does **not** fan out instrument data itself.
- **Channel store = read fan-out.** Already streams live-from-now, tagged by `session_id` +
  `instrument_role`. The observer asks the coordinator "who's driving the DMM?", gets the keys,
  and subscribes to *those* channels through the existing stream.

"You can read things **associated with** the requested instrument even if you can't write it" =
the producer's published channels (its `observe()` outputs / channel writes) plus the *state*
(reservation events). **Deliberate boundary:** the observer sees what the producer *publishes*,
**not** eavesdropped raw VISA traffic — and because it reads channels (a driver-neutral plane),
the observe path is BYODriver-agnostic by decoupling. The missing seam is a read-only
`connect()`/observe entry that routes to channel subscription instead of `pool.acquire`.
Reachable from a separate process once the coordinator is joinable (§4.3).

**Producer contention (settled):** a *writer* that hits a held exclusive lease **blocks up to a
specified timeout, then stops cleanly** (fails the step with `ResourceInUse`). No demotion to
observer, no polling-to-acquire — the caller chooses the timeout (default `0` = immediate
refuse, as the file lock does today). Falling back to observe is a *separate, explicit* call,
never an imposed state.

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
| **#11** | reservation events + locking grain | §3 (step boundary, grain), §4.1 (step-lease grain fix — **DONE**; residual = server-emits-lease events, §7), §4.2 (detection), §7 (events + server-emits-lease) |
| **#12** | read-only observe | §5, §6 — depends on §4.3 (joinable coordinator) |
| **#13** | in-body vector redo | §3.1 (lease wraps in-body loop; shares step-context with #11) |
| **#17** | reservation/dialog spans on run parquet | §7 (event-sourced spans; keep out of C5 column) |
| *(new?)* | **station-scoped join-on-connect coordinator** (fencing + discovery) | §4.3 + *The model* — currently unowned; #12 and safe coexistence assume it |

---

## 9. Open questions

1. **[resolved 2026-07-15 — §3.3]** **Who chooses the sharing mode.** Two layers: config
   authors the ceiling (per-resource: coordinated? who may write?); the client opts into the
   momentary mode (**write**/**observe**) at `connect()`; first-mover publishes it to the
   machine-global registry; late-joiners read it. Entrypoint-agnostic (test, script, custom
   UI are peers). Backed by four prior-art systems.
2. **[open]** Coordinator **lifecycle/ownership**: first-connector (`INITIALIZE_NEW`) stands it
   up, late-joiners `ATTACH_TO_EXISTING` — but does a `testerkit serve` context own it, and does
   it lazy-start only on the shared path? (Rhymes with the runs/channels/files daemons; the
   *trigger* is config/registry, **not** the launching command — see §3.3.)
3. **[open]** Does the **file-lock path survive** as the solo zero-infra default, or does
   everything eventually route through the coordinator? (Lean: file lock for solo/dedicated,
   coordinator for shared.)
4. **[deferred — observe-only-first]** **Take-control policy**: who can seize a *live* holder,
   when? (observe-always, control-when-idle, per-instrument.) Explicitly OUT of this epic —
   control transfers only at lease boundaries (release → acquire), never mid-hold; live seizure
   is a later epic. This is *why* observe-only-first is coherent (§6): no seizure mechanism =
   no demote-and-poll behavior to design.
5. **[open]** Grain-config scope (§3.2): per-instrument/role vs profile vs global.
6. **[open]** §4.2 guard: dedupe shared-detection by resolved resource — warn vs auto-merge.
7. **[open]** §4.3 fencing mechanism: file lock vs a coordinator-registered discoverable
   claim (these may be the same thing once the coordinator is station-scoped).
8. **[largely resolved 2026-07-11]** **Station identity scope.** Today station resolution is
   project-local, CWD-anchored: `connect()` → `find_station_config` reads the project's
   `stations/` YAML, and `_find_project_config` walks CWD ancestors for `testerkit.yaml`
   (`connect.py:488-493,506-533`); the data plane stamps `station_hostname =
   socket.gethostname()` (`run_scope.py:468`). There is **no global station registry** — and,
   per §4.3, the **per-host coordinator does not need one**: the lock is already resource-keyed
   and machine-global, so same-host contention is correct with no station identity, and the
   coordinator is discoverable by hostname/convention. **A registry is required only for the
   cross-host slice** (a station spanning machines, or a remote observer) — deferrable, not a
   0.3.2 prerequisite.

   **Sub-point — hostname and IP are NOT synonyms, and trying to unify them is a category
   error.** (a) The lookup is unreliable: forward DNS is one-name→many-IPs (a multi-homed bench
   PC on lab-LAN *and* instrument-subnet), reverse PTR is usually absent on a LAN, DHCP moves
   the IP, and even the host has no canonical form (`gethostname()` ≠ `getfqdn()`). (b) More
   fundamentally they identify **different entities** — the *driving-host* hostname vs the
   *instrument's* address — so "make them synonyms" conflates a computer with a device. The
   clean rule: **key contention on the resolved resource address, which self-scopes** — a bus
   address (`GPIB`/`USB`) is host-local by nature (the machine-global lock dir already scopes
   it); a `TCPIP::<ip>` address is network-global by nature (two hosts agree because the string
   is identical). No host↔IP resolver is needed. The *only* real resolution work is
   **canonicalizing alias forms of one networked resource** (`TCPIP::10.0.0.5` vs
   `TCPIP::bench-dmm.lab` vs `...::SOCKET`) so one device ≠ two lock files — a best-effort
   normalization at lock time (the §4.2 "dedupe by resolved resource" guard, extended to DNS),
   **not** a station registry.
9. **[RESOLVED 2026-07-12 — deep prior-art research, 24 primary-sourced claims]** **Do NOT
   adopt a transparent-object-remoting library (RPyC / Pyro5); build the coordinator on the
   existing command-forwarding message-passing server.** The question was "integrate (RPyC/
   Pyro5) vs extend the hand-rolled server." Research inverts the naive P2 read — the thing a
   library would "integrate" (transparent remote objects) is precisely what fails for live
   drivers:
   - **The QCoDeS failure is fundamental, not incidental.** QCoDeS built the exact design under
     consideration — `RemoteInstrument`/`InstrumentServer`, one OS process per instrument, calls
     routed through queues — and **removed it entirely** in v0.1.4 (PR #510, changelog:
     "Multiprocessing removed"). Root cause: a live driver wraps an **exclusive OS/VISA handle
     (ctypes pointer) that cannot be pickled or migrated across a process boundary — and
     shouldn't be, since that would permit the competing connections exclusivity exists to
     prevent** (#53: *"they can't be pickled… otherwise you'd be able to make competing
     connections where only one is allowed"*; still enforced today — `Instrument.__getstate__`
     raises). Downstream failure modes: **orphaned processes** holding hardware after the parent
     died (`VI_ERROR_RSRC_BUSY`, only fix = kill all Python, #172/#120), cross-process **queue
     timeouts** (#119), **meta-instruments** failing to compose across the boundary (#119).
   - **No project remotes a live driver via RPyC or Pyro5** (absence-of-adoption is the signal).
     Pyro5 enforces **single-thread proxy ownership** that collides with VISA/OS-handle thread
     affinity (`_pyroClaimOwnership`). RPyC netrefs are generic; nobody has made them survive a
     live session.
   - **The two living Python T&M solutions both hand-rolled explicit command-forwarding
     message-passing** where the server owns the session and forwards *commands* (not state):
     **pyvisa-proxy** (reflection-forwarding over ZMQ) and **instrumentserver** (ZMQ ROUTER/
     DEALER + a PUB socket) — zero references to rpyc/Pyro in either. This is the dominant
     industry pattern, and **TesterKit already built it** (`InstrumentServer` forwards per-RPC
     commands over `multiprocessing.connection`; never serializes the driver). So evolving the
     existing server is *matching proven prior art*, not reinventing.
   - **Two bonuses:** (a) the worst QCoDeS failure was **orphaned processes** → the coordinator's
     #1 lifecycle requirement is **robust death-cleanup**, which TesterKit already has (`flock`
     auto-release on process death + `_release_all_leases`/refcount-shutdown on disconnect) —
     harden and test it, don't rearchitect. (b) **Read-only observe (#12) is validated and maps
     to existing infra**: Tango's device-server-pushes-to-all-subscribers events and
     instrumentserver's PUB socket are exactly TesterKit's **channel-stream fan-out** — so #12 rides
     the channel stream (§6), now backed by two independent precedents.
   - **Deferred, additive:** a **ZMQ transport swap** (matching both living tools) is the natural
     option *if/when* cross-host is needed — it travels with the cross-host slice (§4.3, open-Q6),
     not this line. Skip the RPyC/Pyro5 bake-off spike entirely (evidence-closed); the only spike
     worth running is *our own* server under two concurrent observers.

   Sources: QCoDeS #53, #119, #120, #172, changelog 0.1.4/PR #510; pyvisa-proxy
   (github.com/casabre/pyvisa-proxy); instrumentserver (github.com/toolsforexperiments/
   instrumentserver); Pyro5 client docs; RPyC README; Tango Controls events doc.
