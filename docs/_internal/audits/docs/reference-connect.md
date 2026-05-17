# Page audit: docs/reference/connect.md

**Quadrant:** Reference (connect() function + StationConnection class — full API surface)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 1 | 1 |
| Accuracy | 1 | 3 | 2 |
| Gaps | 1 | 4 | 3 |
| Cross-links | 0 | 2 | 3 |
| **Total** | **2** | **11** | **13** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L52–L95 | `StationConnection` section introduces `EventLog`, `EventStore`, `ChannelStore`, `InstrumentPool`, and `SessionStarted`/`SessionEnded` events in the properties/lifecycle tables before any of these are defined or linked. A reader top-to-bottom hits all five terms cold in tables; the Flight-server section (L118) is the first prose explanation, and the channel store / event log are never defined on-page. |
| SUGGESTION | L26 vs L93 | The "context manager or explicit start/stop" lifecycle is stated at L26 (after the function signature) and then re-stated and expanded at L93 ("Context-manager protocol"). A reference is fine to repeat itself, but the second mention should foreshadow the outcome-derivation table at L50, not just restate the principle. |
| SUGGESTION | L98 vs L114 | The "Per-resource locking" section opens with the cross-process scenario (L99–L112) and only at L114 explains that locks live under `~/.local/share/litmus/locks/`. The location + auto-release semantics are the densest reference fact; surface them above the multi-script example. |

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| SUGGESTION | L3 | passive / generic noun stack | "all use it to acquire a `StationConnection` that owns the event log, the channel store, and the locked instruments for the session." — fine on first pass but the three nouns are themselves cold (see Audience / Ordering). |
| SUGGESTION | L26 | hedging | "Usable as a context manager... or with the explicit `start()` / `stop()` lifecycle." — "Usable as" is soft; reference prose would say "Use as a context manager... or call `start()` / `stop()` directly." |

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L72 | jargon | "build the `InstrumentPool`" — `InstrumentPool` is an internal class name that a test engineer doesn't need to type. A reference can mention it, but here it sits in the user-facing lifecycle column with no link or one-liner. Say "connect instruments per the station config" or link to a definition. |
| SUGGESTION | L82 | wrong vocabulary for the audience | "Start the IPC instrument server so external processes can share these instruments." — "IPC instrument server" is platform jargon. Test engineer reads this row and doesn't know whether they need this. A one-line motivator ("so a separate operator UI or pytest worker on this host can talk to the same locked instruments") would land better. |

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L15, L23 | `connect(..., data_dir: Path \| str \| None = None, ...)` — claims `str` accepted. | Signature is `data_dir: Path \| None = None`. `str` is not accepted at the `connect()` callsite. `StationConnection.__init__` also takes `Path \| None`. `resolve_data_dir()` accepts `Path \| str \| None`, but `connect()` does not pass through to it directly. | `src/litmus/connect.py:474`, `src/litmus/connect.py:56` |
| WARNING | L23 | `data_dir` resolution chain: "explicit arg → `litmus.yaml` `data_dir:` → `LITMUS_HOME` → `platformdirs.user_data_dir("litmus")`". | The chain is correct, but `connect()` itself does NOT resolve `data_dir` — it stashes whatever the caller passed and `EventStore(_data_dir=...)` ends up calling `resolve_data_dir()`. So when `data_dir=None`, resolution happens lazily inside `EventStore`. The page implies `connect()` does the resolution. | `src/litmus/connect.py:76`; `src/litmus/data/data_dir.py:32` |
| WARNING | L72 | `start()` description: "Create `EventLog`, emit `SessionStarted`, open `ChannelStore`, build the `InstrumentPool`, register process-exit cleanup." | Actual order: (1) create EventStore, (2) get EventLog from EventStore, (3) create + `open()` ChannelStore, (4) build InstrumentPool, (5) `register_cleanup()`, (6) set up sync point, (7) **emit SessionStarted last**. The doc puts `SessionStarted` second; in source it is final. | `src/litmus/connect.py:71-114` |
| WARNING | L114 | "Lock files live in `~/.local/share/litmus/locks/` (Linux) and use `fcntl.flock()`." | Lock files live under `LITMUS_HOME/locks/` (defaults to `platformdirs.user_data_dir("litmus")/locks/`), which on Linux is `~/.local/share/litmus/locks/` — correct. However `fcntl.flock()` is not called directly; the code uses the `filelock` library (`FileLock(lock_path).acquire(...)`). `filelock` uses `fcntl.flock()` under the hood on Linux/macOS, so the kernel-level claim is right, but the library name should be stated for accuracy. | `src/litmus/instruments/locks.py:21`, `src/litmus/instruments/locks.py:85-91` |
| SUGGESTION | L50 | "the outcome [is picked] from the exception type: `None` → `passed`, `KeyboardInterrupt` / `SystemExit` → `terminated`, anything else → `errored`." | Accurate for `__exit__`. But `_emergency_stop` (SIGTERM/atexit path) emits `terminated` first and falls back to `aborted` if `stop()` itself fails. The page never mentions `aborted` and gives the impression only three outcomes exist. | `src/litmus/connect.py:432-449`, `src/litmus/connect.py:411-426` |
| SUGGESTION | L67 | `instrument_server_address` typed `str \| None` with description `host:port` of the IPC instrument server. | Source returns `self._instrument_server.address_str` (or `None`). The "host:port" format is a property of `address_str`; documenting that contract on this row is fine, but `start_instrument_server()` already says `host:port` — consider linking. | `src/litmus/connect.py:117-121` |
| VERIFIED | — | 21 claims verified against source (function signature, all `StationConnection` property types, all method signatures and return types, `ResourceInUse` exception path, mock-skips-locking behavior, session-id is a `uuid4`, `connect()` resolves station from `./stations/<id>.yaml` then `LITMUS_HOME/stations/<id>.yaml`, `default_station` field on `ProjectConfig`, `SessionStarted` / `SessionEnded` / `InstrumentConfigure` event classes, sync-point single-slot fast-return, `on_event` replay-then-push semantics, `observe()` returns a `channel://` URI, ref-counted daemon with idle timeout). | — | — |

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L52 (`StationConnection` section) | `event_log`, `event_store`, `channel_store` properties are typed `... \| None` with description "Active ... (after `start()`)". The page never tells the reader what happens if they access these before `start()` — do they get `None`, or does the property raise? (Source: returns `None`.) A reference reader will hit this. |
| WARNING | L79 | `instrument(role, timeout: float = 0)`: doc says "Raises `ResourceInUse` if the underlying resource address is locked." Doesn't say: (a) `timeout=0` means fail-fast; (b) any positive value waits N seconds and then raises; (c) what raises if `role` is not in the station config (source raises `KeyError`). |
| WARNING | L88 | `events(*, event_type=None, role=None)`: doc omits other supported filters on the underlying `EventStore.events()` — `since`, `until`, `until_event_number`, `limit`. The page is the reference; if these aren't exposed on `StationConnection.events()` deliberately, say so; otherwise document them. |
| WARNING | L26, L93 | Re-entrant `with` blocks: page says "Re-entrant `with` blocks are not supported — one `StationConnection` per lifetime." Source actually no-ops a second `start()` (`if self._started: return`) and a second `stop()` (`if not self._started: return`), so a second `with` mostly works but the outcome on the outer block is overwritten by the inner. The "not supported" wording leaves the reader guessing what actually happens. State the failure mode (or that it's a no-op + silent outcome overwrite). |
| WARNING | L114 | "auto-release when the process exits, even on `SIGKILL`" — true for the OS file lock, but the page doesn't say what happens to the live instrument connections (PyVISA sessions, sockets). If the process dies hard, those don't get the `stop()`/release path. Worth one line, since this is the "what if my script crashes" question every reader has. |
| SUGGESTION | L67 (`instrument_server_address`) | No example of when this is non-`None`. A one-liner — "non-`None` after `start_instrument_server()` returns" — would close the loop with L82. |
| SUGGESTION | L91 (`sync(name, timeout=...)`) | Doc says "Used for multi-DUT slot coordination" but doesn't link to the multi-DUT slot concept or how the slot ID is set (`_LITMUS_SLOT_ID` env var; mentioned in source docstring, hidden from the page). At minimum, a forward-link to where multi-DUT is explained. |
| SUGGESTION | L128 (`Station-config resolution`) | What if `station="cell-7"` resolves but `default_station` in `litmus.yaml` is wrong and the explicit arg was meant to override — does it? (Yes: explicit arg always wins.) Stating the precedence rule explicitly would help. |

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| WARNING | L60 (`config`) | First mention of `StationConfig` (also at L54) — no link to `reference/models.md` or wherever the model is defined. A reference reader following the property table will want to know its fields. |
| WARNING | L64, L65 | First mentions of `EventLog`, `EventStore`, `ChannelStore` — no links. There is a `docs/concepts/event-log.md` and `docs/concepts/three-stores.md` (per `docs/tutorial/index.md:19`); these should be linked from the property table or above it. |
| SUGGESTION | L82 (`start_instrument_server`) | Could link to a concepts page on IPC / multi-process instrument sharing if one exists; if not, the term "IPC instrument server" is a candidate for a glossary cross-link. |
| SUGGESTION | L91 (`sync`) | "Used for multi-DUT slot coordination" — link to the multi-DUT how-to or concept page. (None linked from this page; check `docs/concepts/` for a multi-slot doc.) |
| SUGGESTION | L142 (`See also`) | Consider adding cross-links to: `reference/cli.md` (for `litmus runs` / `litmus show` which read these sessions), and a `concepts/three-stores.md` or `concepts/event-log.md` reference so the `event_log` / `event_store` / `channel_store` properties have a defining page. |
