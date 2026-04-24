# Tier 3 — Profiles + Multi-pin

The full production flow: a user-maintained catalog, a station +
fixture + product, and two profiles (`production` / `characterization`)
that gate vectors, limits, and pytest options.

## Layout

```
03-profiles/
├── drivers/               # Driver classes (DMM, PSU, ELoad)
├── catalog/               # User-maintained vendor catalog
│   ├── keysight/          #   34465A DMM, E36313A PSU
│   └── chroma/            #   63103A ELoad
├── stations/
│   └── bench_alpha.yaml
├── products/
│   └── pmic_a23.yaml      # Multi-pin rail_voltage_trio characteristic
├── fixtures/
│   └── pmic_a23_bench.yaml
├── tests/
│   ├── test_power_on.py + sidecar YAML
│   ├── test_rails.py     + sidecar YAML   # ctx.points iteration
│   └── test_regulation.py + sidecar YAML  # sweep + profile overrides
├── conftest.py
├── litmus.yaml            # Profiles live here
├── pytest.ini
└── pyproject.toml
```

## Run

```bash
cd examples/03-profiles

# Default profile — full sweeps
uv run pytest --mock-instruments -v

# Production — collapsed vectors, tight limits
uv run pytest --litmus-profile=production --mock-instruments -v

# Characterization — full sweeps, verbose output
uv run pytest --litmus-profile=characterization --mock-instruments -v
```

## What Tier 3 adds vs Tier 2

* **Profiles** (`litmus.yaml`) — override vectors, limits, and pytest
  options by run phase. Select with `--litmus-profile=<name>`.
* **Multi-pin characteristics** — `rail_voltage_trio` iterates
  3V3 / 1V8 / 1V2 from one test body via `for _ in ctx.points:`.
* **User-maintained catalog** — `catalog/<vendor>/<model>.yaml`
  describes instrument capability data once; stations reference it
  via `catalog_ref:`.
* **Binding-aware limits** — sidecar `limits:` resolve per-point
  against the active `ProductCharacteristic` spec bands.
