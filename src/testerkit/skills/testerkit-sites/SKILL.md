---
name: testerkit-sites
description: Use when a user wants to test multiple UUTs at once on one fixture — multi-site / multi-socket / parallel production testing, or debugging one position without waiting on its neighbors.
---

# Testing N units in parallel

A **site** is a physical UUT position on a fixture — what STDF calls
`SITE_NUM` and TestStand calls a test socket. Skip all of this for a
single UUT at a time; see `testerkit-tests` instead.

## 1. Declare the sites

`fixtures/<id>.yaml` gets a `sites:` list. 2+ entries makes the fixture
`is_multi_site`. Each site is a named position with its own
`connections:` (own instrument channels), sharing the station's
instrument roles:

```yaml
# fixtures/dual_site_bench.yaml
sites:
  - name: left
    connections:
      vout: {instrument: dmm, instrument_channel: "1", ...}
  - name: right
    connections:
      vout: {instrument: dmm, instrument_channel: "2", ...}
```

`site_index` is the 0-based position in that list (`left`=0, `right`=1);
`site_name` is the optional label. Both are frozen on `TestRun` at run
start and denormalized onto every run row — queries never join back to
the fixture YAML. Test bodies never mention a site: the same test runs
unmodified on every site because each worker's `context.connections`
dict is flattened from `sites[site_index]` before the test sees it.

## 2. Run one site (debug)

```bash
pytest --site 0                    # or --site left
pytest --site left --uut-serial SN1
```

Single-process, single-site. Records that one site's `site_index`/
`site_name`, no orchestrator spawned. Use it to check one UUT position
without waiting on its neighbor. `--uut-serial` (singular) pairs with
`--site` for that one UUT's identity — it's distinct from the
multi-site `--uut-serials` below.

## 3. Run all sites in parallel (production)

One `pytest` invocation, not N. A multi-site fixture with **no**
`--site` flag turns a bare `pytest` into an orchestrator: the parent
process spawns one worker subprocess per site (each with its own OS
process and UUT identity) and aggregates results:

```cli
$ cd examples/12-parallel-sites
$ uv run pytest -q
[site:0] .                                                     [100%]
[site:0] 1 passed in 0.43s
[site:1] .                                                     [100%]
[site:1] 1 passed in 0.43s
============================================================
Multi-UUT Results
============================================================
  site[0]: PASS  1 passed in 0.43s
  site[1]: PASS  1 passed in 0.43s
============================================================
```

Each worker session lands as its own independent run — two `run_id`s
sharing one `session_id`, not one run split into parts.

Per-site UUT serials via `--uut-serials`, three interchangeable forms:

```bash
pytest --uut-serials SN1,SN2              # positional: fixture list order
pytest --uut-serials 0=SN1,1=SN2          # indexed: site_index=serial
pytest --uut-serials left=SN1,right=SN2   # named: site_name=serial
```

A shared instrument (one `psu`, one `dmm` on the station) is one
physical box — TesterKit serializes calls to it so two sites never collide
mid-measurement, with no lock code in the test. Under
`--mock-instruments` (see `testerkit-mocks`) each site additionally gets
its own mock state, so a fault injected on one site's driver never
leaks into the other's.

## 4. Coordinate across sites

The `sync` fixture is a rendezvous point across sites in the same
session — `None` outside worker mode, a wait-for-all object inside it:

```python
def test_measure_hot(dmm, sync):
    if sync:
        sync.wait("thermal_soak", timeout=300)
    assert dmm.measure_voltage() > 3.0
```

Every worker calling `sync.wait("thermal_soak")` blocks until every
other site in the session has called it too (or dies, which unblocks
the rest rather than deadlocking forever).

## 5. Query per-site data

`site_index` and `site_name` are columns on every run row. `testerkit runs
--json` and `testerkit show <run_id>` surface them per run; `RunsQuery`'s
`list_for_session(session_id)` returns every sibling run from one
multi-site session together, and `StepsQuery`'s `list_for_session`
orders steps by `site_index` so a multi-site session's steps interleave
correctly.

## Run it, then validate

```bash
uv run pytest -q                 # bare pytest — orchestrates if is_multi_site
testerkit validate                  # fixtures/*.yaml against FixtureConfig
```

## Best-practice defaults
- Reach for `--site <n>` only to debug one position; production runs use
  the bare orchestrator, not N manual invocations.
- Never hardcode a site index or name inside a test body — it comes from
  the fixture's flattened `context.connections`, not from test code.
- Use `sync` for a real rendezvous (thermal soak, sequenced power-up);
  don't invent a `time.sleep` in its place.
- 0-based `site_index` everywhere, including STDF export; the frozen
  `site_name` is the human label.

## Deeper
Read the docs:
```bash
testerkit docs show how-to/execution/multi-uut-testing
```
Runnable example: `examples/12-parallel-sites`.
Sibling skills: `testerkit-tests` (verb choice, right-sizing), `testerkit-mocks`
(per-site mock state), `testerkit-stations` (station/fixture wiring),
`testerkit-data` (session/site queries).
