# Instrument access model — locking, reservation, observation

**Status:** design (2026-06-27), shaped via discussion. Not yet scheduled to a release
(separate from the 0.3.0 reshape / 0.4.0 analytics). Captures tasks #11 (reservation events)
and #12 (read-only observe). Verified findings against current source noted inline.

## Current architecture (verified 2026-06-27)

- **Lock substrate:** `instruments/locks.py` — per-resource `FileLock` (`acquire_resource` /
  `release_resource`), `ResourceMeta` (pid, session, station, role, acquired_at), lock files
  under `TESTERKIT_HOME/locks/`, robust to process death (SIGKILL releases). `instruments/pool.py`
  holds `self._locks` and acquires on connect.
- **Two paths**, routed in `pool.acquire`:
  - **Shared role + `_TESTERKIT_INSTRUMENT_SERVER` set →** `RemoteInstrumentProxy` to the
    **`InstrumentServer`** (`instruments/server.py`): a *shared pool* of connected drivers,
    served to worker sessions via TCP RPC, ref-counted shutdown. Locks **per-resource, acquired
    PER RPC CALL** (per command) in `_handle_client` (`:188`) with a `_HEARTBEAT_TIMEOUT`
    force-acquire on a dead client (a documented brief-mutual-exclusion race). `concurrent=True`
    roles skip the lock.
  - **Else → local connect + file lock**, held for the **session** (the `instruments` fixture
    is `scope="session"`, `__init__.py:645` — `acquire` once at session start, `release_all` at
    teardown).

## The gaps

- **Per-command locking = wire safety, NOT sequence atomicity.** Each command locks
  independently, so a step's `set range → read` can be interrupted mid-sequence by another
  slot's command → the step reads at the wrong instrument state.
- **Session-long file lock = too coarse.** Over-counts utilization ("held" ≠ "used") and a
  slot's session-long hold blocks shared-instrument parallel UUTs for its whole run.
- **No reservation EVENTS** → utilization uncaptured.
- **No read-only observe mode** → readers must take the exclusive lock.

## Settled design (2026-06-27)

- **Reservation grain = THE STEP. "Lock around the step yield."** Parallel UUTs sharing an
  instrument interleave at **step boundaries** — not run grain (one slot would block others its
  whole run) and not command grain (another slot could interrupt a step's command sequence and
  change state out from under it). A step's command sequence stays atomic; slots interleave
  between steps.
- **Decouple connection from reservation.** Connection stays **session-scoped** (connect once —
  driver load + `*IDN?` + verify is expensive). Only the **reservation** cycles per step.
  Unified on both paths: local/dedicated → a **step-scoped file lock** (held for the step, not
  the session); shared → the server grants a **step-duration lease** (instead of its current
  per-command lock). Within a held lease, the low-level per-command lock is uncontended (keep it
  as the wire guard).
- **Recursive (re-entrant) locks.** A holder re-acquiring its own resource (the step lease, then
  an inner per-command acquire on the same resource) must **not self-deadlock** — use an
  RLock-equivalent / re-entrant file lock keyed by holder identity.
- **Timeout: support "wait forever" (sentinel `-1`), but split it from dead-holder detection.**
  Infinite acquire-timeout is correct for "willing to wait for a *live* holder to release." It
  must be paired with a **separate liveness/heartbeat watchdog** that force-releases a **dead**
  holder's lease — otherwise a crashed holder hangs everyone forever. The current code
  *conflates* these (the 15 s timeout doubles as both "give up waiting" and "detect dead
  client"). Split them: **timeout = how long to wait for a live holder** (`-1` = forever);
  **heartbeat = detect + recover a dead holder**, independent of the wait.
- **Reservation events (#11).** Emit `instrument.reserved` / `instrument.released` at step
  acquire/release. Two consumers, not one:
  1. **Event-sourced utilization** (the *temporal* span — when/how-long, accurate at step
     grain). Must be event-sourced because instruments are also reserved in **interactive**
     sessions that produce no run, so for *utilization* the event log is the superset.
  2. **The step/vector at-rest instrument set (#26).** *Which* instruments a step reserved is a
     **source of the step's at-rest `instruments` set** in the C5 column — so #11 feeds the
     at-rest reshape, it is not disjoint from it. (Corrected 2026-06-29: the prior framing
     "run-scoped C5 inventory is a subset" wrongly treated C5 as run-grain only. The C5 column
     is **multi-grain** — run/step/vector; the superset/subset relation holds only for the
     *temporal* utilization data, not the instrument set.)
- **Read-only observe (#12).** Writers take the step lease; **readers subscribe to the channel
  stream** the writer publishes (no lock, no contention). One writer + N channel-subscriber
  readers. Lets an interactive user watch a running automated test's instrument live without
  fighting for the lock. `concurrent=True` instruments opt out of leasing entirely.

## Relationship to other work

The locking architecture is independent of 0.4.0 analytics. It is **not independent of the
instrument-set work (#26)**: the reservation signal (which instruments a step leased) is a
**source of the step/vector instrument set stored at rest** — so #11 and the #26 producer
share that signal and must be designed together. The follow-on instrument-set work **includes
reservation changes**. What stays purely event-sourced and out of the at-rest `instruments`
column is the **temporal utilization** span (when/how-long), which feeds a later utilization
view. (Corrected 2026-06-29 from "Independent of the 0.3.0 at-rest reshape… run-scoped
instrument inventory.")
