# Stage 5 — Station YAML + catalog

The conditional-mock conftest from chapters 1-4 disappears. The
bench is declared once in YAML; instrument fixtures (`psu`, `dmm`)
materialize automatically. The same `--mock-instruments` flag now
flips the whole rig — declared values come from each instrument's
`mock_config` block instead of a Python branch.

## Diff from stage 4

- Deleted `conftest.py` — no more hand-written `psu` / `dmm`
  fixtures. The plugin builds them from the station + catalog at
  session start.
- Added `catalog/` — generic instrument capability definitions
  (referenced from stations).
- Added `stations/bench_01.yaml` — a specific bench, wiring driver
  class → catalog entry → mock values.
- Added `litmus.yaml` — project defaults (`default_station: bench_01`,
  `mock_instruments: true`).

The `drivers/` folder is unchanged from chapter 1 — same `DMM` /
`PSU` driver classes; same PyVISA-shaped interface. Test bodies are
also unchanged: `psu.set_voltage(...)` / `dmm.measure_dc_voltage()`
on the same fixture names.

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
3. **Mock declarations move from Python to YAML.** Chapters 1-4
   wrote `Mock(PSU, measure_voltage=5.0, ...)` in `conftest.py`.
   Now those values live in `stations/bench_01.yaml: mock_config:`.
   Same `--mock-instruments` flag, same `Mock(driver_class, **values)`
   factory under the hood — only the declaration site changes.
   Ops can edit values without touching Python; the same YAML file
   stays the truth when `--no-mock-instruments` points the rig at
   real hardware.

## The gap this stage leaves

The limit is still a raw number (`low: 3.2, high: 3.4`). If you
manufacture three product variants with different spec limits, you
duplicate the number in three test sidecars. Stage 6 introduces a
**product** YAML so the spec lives once, and tests **reference** it
— `characteristic: rail_3v3` + `tolerance_pct: 2` derives the band
from the characteristic's declared value.
