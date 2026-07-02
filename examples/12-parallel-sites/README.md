# Stage 12 — Multi-site parallel testing

Two UUT positions on one bench, tested at the same time. Each position is
a **site** — the same parallel-position concept STDF calls `SITE_NUM` and
NI TestStand calls a test socket. `fixtures/dual_site_bench.yaml` declares
two named sites, `left` and `right`, that share the bench's `psu` and `dmm`
but land on different instrument channels (`left` → channel `"1"`,
`right` → channel `"2"`). A fixture with 2+ sites `is_multi_site`, and
that's what turns a bare `pytest` invocation into a parallel run: the
**orchestrator** parent process spawns one **worker** subprocess per site,
each running the full test session against its own site — no test code
change required to go from one UUT to two.

See [docs/how-to/execution/multi-uut-testing.md](../../docs/how-to/execution/multi-uut-testing.md)
for the full site model reference this example exercises.

## What's in here

- **`stations/bench_dual.yaml`** — one bench, mock `psu` + `dmm` (same
  `mock_config:` pattern as `examples/06-station-catalog`).
- **`fixtures/dual_site_bench.yaml`** — the 2-site fixture. Each site's
  `connections:` wires `vout` (dmm) and `vin` (psu) to that site's own
  `instrument_channel`; the instrument roles are shared, the channels
  aren't.
- **`tests/test_dual_rail.py`** — one test, `test_vout_within_spec`. It
  never mentions a site, a channel, or an index — it just reads `dmm`
  through `context.connections`. The site-specific channel routing lives
  entirely in the fixture YAML.
- **`litmus.yaml`** — `default_station: bench_dual`, `default_fixture:
  dual_site_bench`, local `data_dir: data` (this example's runs never
  touch the global data dir).
- **`pytest.ini`** — `addopts = --mock-instruments --uut-serials
  left=SN-A,right=SN-B`, so a bare `pytest` is already a complete
  2-site parallel demo.

## Run it

```bash
cd examples/12-parallel-sites
uv run pytest -q
```

```
[site:0] .                                                                        [100%]
[site:0] 1 passed in 0.43s
[site:1] .                                                                        [100%]
[site:1] 1 passed in 0.43s

============================================================
Multi-UUT Results
============================================================
  site[0]: PASS  1 passed in 0.43s
  site[1]: PASS  1 passed in 0.43s
============================================================
```

`[site:N]` prefixes both workers' output as they run concurrently. Site 0
(`left`) ran as `SN-A`, site 1 (`right`) ran as `SN-B` — two independent
runs land in `data/`:

```bash
uv run litmus runs --limit 5
```

```
Run ID     Started                    UUT Serial      Project              Station              Outcome
----------------------------------------------------------------------------------------------------------
a3a0978f   2026-07-01T17:48:28-0600   SN-B            parallel-sites-example bench_dual           passed
78a953bc   2026-07-01T17:48:28-0600   SN-A            parallel-sites-example bench_dual           passed
```

Each run's parquet carries the `site_index` / `site_name` it was frozen
with at start: `SN-A` → `site_index=0, site_name="left"`, `SN-B` →
`site_index=1, site_name="right"`.

## CLI overrides

`pytest.ini`'s `addopts` is a *default*, not a lock — any `--uut-serials`
/ `--site` / `--uut-serial` passed on the command line overrides it for
that invocation. Useful for experimenting with different serials or
running one site in isolation without editing the file.

**Positional** — one serial per site, in fixture list order (`left`
gets the first):

```bash
uv run pytest -q --uut-serials SN1,SN2
```

**Indexed** — `site_index=serial` pairs, any order:

```bash
uv run pytest -q --uut-serials 0=SN1,1=SN2
```

**Named** — `site_name=serial` pairs (works here because both sites in
`dual_site_bench.yaml` have a `name:`):

```bash
uv run pytest -q --uut-serials left=SN1,right=SN2
```

All three still spawn the 2-worker orchestrator — `--uut-serials` just
changes which serial lands on which site.

**Single-lane** — target one site, single-process, no orchestrator:

```bash
uv run pytest -q --site left --uut-serial SN1
```

This runs in the current process (no `[site:N]` prefix, no "Multi-UUT
Results" banner) and records exactly as `left` would inside a parallel
run — `site_index=0, site_name="left"` — useful for debugging one UUT
position without waiting on its neighbor. `--site` always runs
single-process, even against a multi-site fixture; pair it with
`--uut-serial` (singular), not `--uut-serials`.

## Why this matters

A shared instrument (`psu`, `dmm`) is one physical box in this example
(and would be one physical box on a real 2-up bench too) — Litmus
serializes calls to it so `left` and `right` never collide mid-measurement,
without either test author writing a lock. Under `--mock-instruments`
each site additionally gets its **own** mock state, so a fault injected on
one site's `dmm` never leaks into the other's.
