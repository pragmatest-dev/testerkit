# Stage 5 — Station YAML + catalog

Replace the hand-rolled `FakeDut` fixture with a station
configuration. Declare the bench once; instrument fixtures (`psu`,
`dmm`) materialize automatically. Run with mocks by default so
bringup doesn't need hardware.

## Diff from stage 4

- Deleted `conftest.py` entirely
- Added `catalog/` — generic instrument capability definitions (referenced from stations)
- Added `drivers/` — stub driver classes (`DMM`, `PSU`) with method signatures
- Added `stations/bench_01.yaml` — a specific bench, wiring driver class → catalog entry → mock values
- Added `litmus.yaml` — project defaults (`default_station: bench_01`, `mock_instruments: true`)

Tests now ask for `psu` and `dmm` by name. The plugin constructs them.

## Run it

```bash
cd examples/05-station-catalog
uv run pytest -v
```

## Why station + catalog

Three things gain leverage:

1. **One station, many tests.** Change the PSU model once in
   `stations/bench_01.yaml`; every test that uses `psu` follows.
2. **Bring your own driver.** Litmus doesn't ship instrument drivers.
   Point `driver:` at your PyVISA / PyMeasure / vendor class; the
   catalog describes *what* the instrument can do, the driver class
   describes *how* to talk to it.
3. **Mock mode is a flag, not a rewrite.** `mock_instruments: true`
   wraps every instrument in a `Mock` that returns the values from
   each instrument's `mock_config` block. Flip to real hardware by
   setting `mock_instruments: false` — the test code doesn't change.

## The gap this stage leaves

The limit is still a raw number (`low: 3.2, high: 3.4`). If you
manufacture three product variants with different spec limits, you
duplicate the number in three test sidecars. Stage 6 introduces a
**product** YAML so the spec lives once, and tests **reference** it
— `characteristic: rail_3v3` + `tolerance_pct: 2` derives the band
from the characteristic's declared value.
