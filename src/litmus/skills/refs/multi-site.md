# Multi-site

A **site** is a physical UUT position on a fixture — the parallel
position STDF calls `SITE_NUM` and TestStand calls a test socket.
This is Tier 4 (`litmus refs show tiers`) — testing one UUT at a time
needs none of it; if that's the request, stop here and see
`litmus refs show routing`.

## The site model

`fixtures/<id>.yaml` declares a `sites:` list; 2+ entries makes the
fixture `is_multi_site`. Each site is a named position with its own
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

`site_index` is the 0-based position in that list (`left`=0,
`right`=1). `site_name` is the optional label (`"left"`), frozen onto
`TestRun` at run start and denormalized onto every run row — queries
never join back to the fixture YAML to know which site a run belongs
to. Test bodies never mention a site or an index; the same test runs
unmodified on every site because `fixture_config` flattens
`sites[site_index]` into the flat `context.connections` dict each
worker resolves against — the same connection name (`vout`) is safe
to reuse across sites because each worker only ever sees its own
site's flattened dict.

Two CLI flags select a site:

- `--site <index-or-name>` — single-process, single-site. Runs in the
  current process, records `site_index`/`site_name` for that one
  site, no orchestrator spawned. Use it to debug one UUT position
  without waiting on its neighbor. Errors if combined with the
  `_LITMUS_SITE_INDEX` env var an orchestrator worker already carries.
- `--uut-serial <serial>` — pairs with `--site` for that single UUT's
  identity (singular; not the multi-site `--uut-serials`).

## Running N sites in parallel

One `pytest` invocation, not N. A multi-site fixture with no `--site`
flag turns a bare `pytest` into an **orchestrator**: the parent
process detects `fixture.is_multi_site`, spawns one **worker**
subprocess per site (each gets its own OS process, `_LITMUS_SITE_INDEX`
env var, and UUT identity), and aggregates results:

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

Each worker session lands as its own independent run in the data
dir — two `run_id`s, one per site, not one run with two sub-parts.

Per-site UUT serials via `--uut-serials`, three forms (all still
spawn the orchestrator):

```bash
pytest --uut-serials SN1,SN2              # positional: fixture list order
pytest --uut-serials 0=SN1,1=SN2          # indexed: site_index=serial
pytest --uut-serials left=SN1,right=SN2   # named: site_name=serial
```

A shared instrument (one `psu`, one `dmm` on the station) is one
physical box — Litmus serializes calls to it so two sites never
collide mid-measurement, with no lock code in the test. Under
`--mock-instruments` each site additionally gets its own mock state,
so a fault injected on one site's driver never leaks into the other's.

## Querying per-site data

Every run row carries `site_index` (int) and `site_name` (string,
optional) — the same two fields frozen at run start. `litmus runs`
and `litmus show <run_id>` list them per run; `RunsQuery` (the runs
query API) accepts `site_index` / `site_name` as filter fields, so
"only site 1's runs" or "only the `right` position" is a query
parameter, not a parquet scan.

## Cross-site coordination

The `sync` fixture provides a rendezvous point across sites in the
same session — `None` outside worker mode, a wait-for-all-sites
object inside it:

```python
def test_measure_hot(dmm, sync):
    if sync:
        sync.wait("thermal_soak", timeout=300)
    assert dmm.measure_voltage() > 3.0
```

Every worker calling `sync.wait("thermal_soak")` blocks until every
other site in the session has called it too (or dies, which
unblocks the rest rather than deadlocking).

## Cross-references

- `litmus/execution/sites.py` — `ResolvedSite`, `resolve_site_token`
- `litmus/execution/site_runner.py` — `SiteRunner`, orchestrator
  detection (`is_orchestrator_mode`)
- `litmus/execution/sync.py` — `SyncCoordinator`, `get_sync`
- `litmus/pytest_plugin/__init__.py` — `sync` fixture, `fixture_config`
  site flattening
- `litmus/pytest_plugin/hooks.py` — `--site` / `--uut-serials` options
- `examples/12-parallel-sites` — working 2-site demo
- `docs/how-to/execution/multi-uut-testing.md` — user-facing guide
- `litmus refs show tiers` — where multi-site sits on the ladder
