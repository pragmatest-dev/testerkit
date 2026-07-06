# Debugging a run (triage)

Work outside-in: find the run, read its steps, read its events, then open the
UI only if you need the visual. Don't guess a cause from the test source
before you've read what actually happened.

## 0 — Triage sequence

| Step | Command | Tells you |
|---|---|---|
| 1. Find it | `litmus runs` (`--since 1h`, `--limit N`) | run_id, UUT serial, station, outcome |
| 2. Read it | `litmus show <run_id>` | per-step outcomes, measurement count, first failure |
| 3. Read events | MCP `litmus_events` tool, or the operator UI `/events` page | ordered event timeline for the run/session (connect, mock, retry, failure) |
| 4. See it | operator UI `/results/<run_id>` (finished) or `/live/<run_id>` (in progress) | same data as `show`, rendered |

`litmus show <run_id> -v` adds each step's full `step_path` (and the run's
parquet file) as a location locator. `--env` shows the captured environment
snapshot instead of steps/measurements. `-f html|pdf|json|csv` generates a
report file instead of printing to the terminal.

There is no standalone `litmus events` CLI command — events are read through
the MCP `litmus_events`/`litmus_sessions` tools (filter by `session_id`,
`event_type`, `role`, `since`) or the HTTP `/events` route. Event shapes are
in `docs/reference/data/event-types.md`.

## 1 — Read the failure signature

| Signature in `litmus show` | Class | Go to |
|---|---|---|
| a measurement's `[FAILED]` next to a value that looks plausible | limit fail | §2 |
| a step's outcome is `error` (not `failed`) before any measurement prints | instrument/driver error | §3 |
| the run never appears in `litmus runs` at all | config validation / collection error | §4 |
| `litmus show` output looks like last week's run, or a new run is missing | stale index | §5 |
| the same step name appears twice with different outcomes | retry | §6 |

## 2 — Limit fail: where did the limit come from?

A `verify()` measurement fails when the value is outside the limit that was
resolved for it. The limit itself comes from the innermost layer that owns
it — inline `limit={...}` on the call, then a `<test>.yaml` sidecar, then a
part spec `characteristic:`, then a profile override. `litmus show` prints
the value and outcome, not the limit's origin — for that, read `litmus refs
show verify` (resolution order) and `litmus refs show part-specs` (spec
lookup).

## 3 — Instrument / driver error

| Symptom | Cause | Verified in |
|---|---|---|
| `instruments["dmm"]` (or any role) raises `KeyError` | that role failed to connect and was silently dropped — instrument-pool `connect()` catches `ValueError` per-role and continues rather than failing the session | `src/litmus/pytest_plugin/__init__.py` (`instruments` fixture) |
| `--mock-instruments` set, but `psu`/`dmm` fixtures return an empty dict / are missing | no `station_config` resolved (no `--station`, no `stations/*.yaml`) — **`--mock-instruments` swaps drivers for roles a station already declares; it does not invent roles** | `src/litmus/pytest_plugin/__init__.py` (`instruments` fixture, `if not station_config: yield {}`) |
| a mocked call (`inst.measure_voltage()`) returns `None` instead of raising | a typo'd `mock_config:` key in the station/instrument YAML — the mock is `Mock(object, **mock_config)`; any attribute not in `mock_config` and not on `object` resolves to a no-op that returns `None` | `src/litmus/instruments/mocks.py`, `src/litmus/instruments/lifecycle.py::load_and_connect` |
| `isinstance(dmm, MyDriverClass)` is `False` on a mocked instrument | the mock is built as `Mock(object, ...)`, not `Mock(MyDriverClass, ...)` — it doesn't inherit from the real driver | `src/litmus/instruments/lifecycle.py::load_and_connect` |
| `RuntimeError: <role>: instrument identity mismatch` (or a `UserWarning` with the same text) | connected hardware's queried identity (`manufacturer`/`model`/`serial`) doesn't match the station/instrument YAML; raises under `strict`, warns otherwise | `src/litmus/instruments/lifecycle.py` |

## 4 — Config validation & collection errors

- `litmus validate [paths...] [-t <type>]` checks catalog/part/station/
  fixture/instrument_asset/project YAML against their Pydantic schemas and
  prints field-path errors. Run it with no arguments to scan every standard
  directory (`catalog/`, `parts/`, `stations/`, `fixtures/`, `instruments/`)
  plus `litmus.yaml`.
- An invalid `litmus_retry` or stacked `litmus_*` marker (e.g. the same
  marker applied inline at two scopes) raises `pytest.UsageError` at
  collection time — the test never runs, and it won't show up in `litmus
  runs` at all. Read the error text; it names the offending nodeid.
- A profile key that matches no collected test name emits a `UserWarning`
  at collection ("Active profile has keys that match no collected test") —
  the test still runs, just without the intended profile override.

## 5 — Daemon / stale index

Litmus reads runs, events, and channels through background daemons that
cache a warm DuckDB index over the parquet on disk. If `litmus runs` /
`litmus show` look out of date after an upgrade or after another process
wrote data:

| Command | Effect |
|---|---|
| `litmus daemon status` | PID, alive/dead, ref count, location for each of `events`/`runs`/`channels` |
| `litmus daemon restart [events\|runs\|channels\|--all]` | SIGTERM the daemon; next access respawns fresh |
| `litmus daemon stop [...]` | stop without respawning (lazy respawn on next access) |
| `litmus data reindex` | stop the events/runs daemons and drop their index files so they rebuild from parquet |
| `litmus data index list` | every runs-index epoch by fingerprint, schema version, row count, size, last-seen |
| `litmus data index build [--rebuild] [--background]` | eagerly warm the current epoch; `--rebuild` discards it first |
| `litmus data index rm <fingerprint> [--force]` | delete one epoch (refuses the current one without `--force`) |
| `litmus data index prune [--keep-last N] [--older-than D]` | drop stale epochs (never the current one) |

Rebuilding an index is never a data-loss risk — it's a cache over durable
parquet.

## 6 — Retries

A test marked `@pytest.mark.litmus_retry(max_retries=N, delay=S, on=[...])`
(inline, sidecar, or profile) is translated to `pytest-rerunfailures`'
`flaky` marker at collection (`reruns=N`, `reruns_delay=S`,
`only_rerun=on`). Each attempt is its own step row — `step_retry` is `0` for
the first execution, `N` for the Nth rerun — so nothing is overwritten.
**Container/run-level rollup keeps only the final attempt's outcome per
test** (`retry_aware_rollup`, grouped by node id — same convention as
pytest-rerunfailures and STDF retest counts); earlier attempts stay in the
step records as retest metadata, not as separate contributions to the
overall run outcome.

What's solid today: the data model (`step_retry` on every step/vector row)
and the rollup semantics. `litmus show`'s terminal output does not yet
label which printed row is a retry attempt — a retried step currently
prints twice under the same step number with different outcomes; query
`StepsQuery` (or filter `step_retry > 0`) if you need that distinction
explicitly.
