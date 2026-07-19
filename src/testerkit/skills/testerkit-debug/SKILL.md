---
name: testerkit-debug
description: Use when a user asks why a run failed, errored, looks wrong, or is missing — triage a run before guessing a cause from the test source.
---

# Triaging a failed run

Work outside-in: find the run, read its steps, read its events, then open
the UI only if you need the visual. Don't guess a cause before you've read
what actually happened.

## 0. Triage sequence

```bash
testerkit runs --since 1h                 # 1. find it — run_id, UUT serial, station, outcome
testerkit show <run_id>                   # 2. read it — per-step outcomes, first failure
```

Then read events (MCP `testerkit_events(session_id=, event_type=, role=,
since=)`, or the operator UI `/events` page) for the ordered timeline
(connect, mock, retry, failure). There is no standalone `testerkit events`
CLI command. `testerkit show <run_id> -v` adds each step's `step_path` and
parquet file as a location locator; `--env` swaps in the captured
environment snapshot; `-f html|pdf|json|csv` writes a report instead of
printing. For the UI: `/results/<run_id>` (finished) or `/live/<run_id>`
(in progress) — same data as `show`, rendered.

## 1. Read the failure signature

| Signature in `testerkit show` | Class | Go to |
|---|---|---|
| a measurement's `[FAILED]` next to a plausible-looking value | limit fail | §2 |
| a step's outcome is `error` (not `failed`) before any measurement prints | instrument/driver error | §3 |
| the run never appears in `testerkit runs` at all | config validation / collection error | §4 |
| `testerkit show` looks stale, or a new run is missing | stale index | §5 |
| the same step name appears twice with different outcomes | retry | §6 |
| the run hangs with no progress, or `PromptUnavailableError` in CI | operator prompt waiting | §7 |

## 2. Limit fail — where did the limit come from?

`testerkit show` prints the value and outcome, not the limit's origin. The
limit is resolved from the innermost layer that owns it: inline
`limit={...}` on the call, then a `<test>.yaml` sidecar, then a part spec
`characteristic:`, then a profile override — see `testerkit-tests` (usage)
and `testerkit-parts` (spec lookup) for the resolution order.

## 3. Instrument / driver error

| Symptom | Cause |
|---|---|
| `instruments["dmm"]` (or any role) raises `KeyError` | that role failed to connect and was dropped — the instrument pool catches `ValueError` per-role and continues rather than failing the session |
| `--mock-instruments` set, but `psu`/`dmm` return an empty dict / are missing | no `station_config` resolved (no `--station`, no `stations/*.yaml`) — `--mock-instruments` swaps drivers for roles a station already declares; it does not invent roles |
| a mocked call returns `None` instead of raising | a typo'd `mock_config:` key — the mock is built as `Mock(object, **mock_config)`; any attribute not in `mock_config` and not on `object` resolves to a no-op returning `None` |
| `isinstance(dmm, MyDriverClass)` is `False` on a mocked instrument | same cause — the mock is `Mock(object, ...)`, not `Mock(MyDriverClass, ...)`; it doesn't inherit from the real driver |
| `RuntimeError`/`UserWarning`: instrument identity mismatch | connected hardware's queried identity doesn't match the station/instrument YAML — raises under `strict`, warns otherwise |

## 4. Config validation & collection errors

```bash
testerkit validate                 # scans catalog/, parts/, stations/, fixtures/, instruments/, testerkit.yaml
testerkit validate <path> -t <type> --json
```

An invalid `testerkit_retry` or a `testerkit_*` marker stacked twice at the same
scope raises `pytest.UsageError` at collection — the test never runs and
never shows up in `testerkit runs`. A profile key matching no collected test
name emits a `UserWarning` at collection instead — the test still runs,
just without the intended override.

## 5. Stale index

Runs/events/channels are read through background daemons caching a warm
DuckDB index over the parquet on disk. If `testerkit runs`/`testerkit show` look
out of date after another process wrote data:

```bash
testerkit daemon status                            # PID, alive/dead, ref count per daemon
testerkit daemon restart [events|runs|channels|--all]
testerkit data index list                          # every index epoch: fingerprint, schema version, row count
testerkit data index build [--rebuild] [--background]
testerkit data reindex                             # stop events/runs daemons, drop index files, rebuild from parquet
```

Rebuilding an index is never a data-loss risk — it's a cache over durable
parquet.

## 6. Retries

A test marked `@pytest.mark.testerkit_retry(max_retries=N, delay=S, on=[...])`
runs each attempt as its own step row (`step_retry` 0, 1, ... N). The
container/run-level rollup keeps only the final attempt's outcome per test
node id; earlier attempts stay as retest metadata, not separate
contributions to the run outcome. `testerkit show`'s terminal output doesn't
yet label which printed row is a retry attempt — filter `StepsQuery` by
`step_retry > 0` if you need that distinction explicitly.

## 7. Hung / timed-out operator prompt

The test is blocked on an operator `prompt` with no way to resolve it: no
`testerkit serve` UI dialog handler installed, and `TESTERKIT_AUTO_CONFIRM` unset —
so it either blocks on stdin (tty fallback) or hangs waiting for a human to
answer the dialog. Fix: set `TESTERKIT_AUTO_CONFIRM=1` in CI to auto-resolve
prompts, or run under the operator UI so the dialog actually surfaces to a
human. A `timeout_seconds` on the prompt raises `PromptUnavailableError`
instead of hanging forever — see `testerkit-interactive`.

## Best-practice defaults

- **Outside-in** — `testerkit runs` → `testerkit show` → events → UI. Don't open
  the test source first.
- **A missing run is a collection problem, not a data problem** — check
  `testerkit validate` and marker stacking before touching the index.
- **Index staleness is never data loss** — `testerkit data reindex` rebuilds
  from parquet.

## Deeper
Read the docs:
```bash
testerkit docs show how-to/data/mcp-debug-failures
testerkit docs show how-to/data/find-flaky-tests
testerkit docs show concepts/execution/outcomes
```
Sibling skills: `testerkit-data` (reading runs past the single failing one),
`testerkit-analysis` (yield/Ppk across many runs), `testerkit-tests` (limit
resolution order), `testerkit-mocks` (mock setup),
`testerkit-stations` (driver/connection setup), `testerkit-interactive` (guided
prompts during a debug session).
