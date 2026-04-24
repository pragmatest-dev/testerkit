# Tier 2 — Station + Product

Station-driven smoke tests against a product spec. Instrument fixtures
come from the station YAML; spec-backed limits come from the product
YAML; fixture routing (pin → instrument channel) comes from the
fixture YAML.

## Layout

```
02-station/
├── drivers/               # Stand-in driver classes (DMM, PSU, ELoad)
├── catalog/               # Generic instrument catalog entries
├── stations/
│   └── demo_station_001.yaml
├── products/
│   └── power_board.yaml
├── fixtures/
│   ├── power_board_fixture.yaml
│   └── dual_power_board.yaml
├── tests/
│   ├── test_power_board_smoke.py + sidecar YAML
│   └── test_dual_power_board_smoke.py + sidecar YAML
├── conftest.py            # Empty — nothing to wire up
├── litmus.yaml            # default_station / default_fixture / mock on
├── pytest.ini
└── pyproject.toml
```

## Run

```bash
cd examples/02-station
uv run pytest --mock-instruments -v
```

For the dual-DUT variant:

```bash
uv run pytest tests/test_dual_power_board_smoke.py \
    --fixture-config=fixtures/dual_power_board.yaml \
    --dut-serials=SN001,SN002 \
    --mock-instruments -v
```

## What Tier 2 adds vs Tier 1

* Station YAML resolves instrument fixtures (`psu`, `dmm`, `eload`)
  from driver classes + `mock_config`; no fixtures in `conftest.py`.
* Product YAML supplies `ref:` limits — sidecar entries like
  `output_voltage: {ref: output_voltage}` resolve against the product's
  `ProductCharacteristic`.
* Fixture YAML wires DUT pins to instrument channels; `verify(...)`
  auto-stamps pin / channel / net / spec_ref on every row.

## Graduating to Tier 3

Tier 3 layers profiles (`production` / `characterization`) on top of
this, plus multi-pin iteration via `ctx.points` and binding-aware
limits. See `../03-profiles/`.
