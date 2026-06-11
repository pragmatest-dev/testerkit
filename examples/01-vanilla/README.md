# Stage 1 — Vanilla pytest

Plain-pytest tests. The Litmus plugin is loaded but it stays out of
your way: you get a UUT-serial prompt and a run record on day one
without writing any Litmus-specific code. Every later stage layers
measurement features on top of this same setup.

## Layout

```
01-vanilla/
├── pyproject.toml        # pytest + litmus
├── pytest.ini            # plugin enabled; --mock-instruments default-on
├── conftest.py           # `psu` + `dmm` fixtures: real drivers, mocked when flagged
├── drivers/              # PSU + DMM driver classes (PyVISA-shaped)
│   ├── psu.py
│   └── dmm.py
└── tests/
    └── test_rail.py      # one direct test + one parametrized sweep
```

## What this shows

- `assert` for pass/fail
- `conftest.py` fixtures shared across tests (`scope="session"`)
- `@pytest.mark.parametrize` for a sweep
- Real `PSU` / `DMM` driver classes (resource string, connect/disconnect,
  SCPI-named methods) — same shape PyVISA / PyMeasure / vendor SDK use
- A run record stamped with the UUT serial — no measurement code yet

## Where drivers come from

Litmus does **not** ship instrument drivers. Use any of:

- **[PyMeasure](https://pymeasure.readthedocs.io/)** — 100+ ready-made
  drivers (`from pymeasure.instruments.keysight import Keysight34461A`)
- **[PyVISA](https://pyvisa.readthedocs.io/)** — raw SCPI; write a thin
  class on top
- **Vendor SDKs** — most major instrument vendors ship Python bindings
- **Hand-rolled** — what `drivers/dmm.py` + `drivers/psu.py` demonstrate
  here (PyVISA-shaped placeholders with `NotImplementedError` bodies)

`litmus.instruments.Mock(cls, **return_values)` works against any of
the above — Litmus doesn't care whether `cls` came from PyMeasure or
your `drivers/` folder.

## Drivers + optional mocks

You always write tests against the real driver classes. When the bench
isn't attached yet, Litmus mocks them for you — same fixture, same test
code, just a different runtime instance:

```python
@pytest.fixture(scope="session")
def psu(mock_instruments) -> PSU:
    if mock_instruments:
        return Mock(PSU, measure_voltage=5.0, measure_current=0.042)
    return PSU(resource="TCPIP::192.168.1.101::INSTR")
```

The `--mock-instruments` flag in `pytest.ini` makes that branch fire
for the demo run. Drop the flag (or pass `--no-mock-instruments`) once
you have hardware to point at — the test code below doesn't change.

This is the same conditional shape Litmus uses internally. Stage 5
lifts it out of `conftest.py` and into station YAML.

## Run it

```bash
cd examples/01-vanilla
uv run pytest --uut-serial=bob -v
```

> **No real serial yet?** Use anything memorable — `bob`, `proto-1`,
> `dev`. The value is just the identifier the run record is filed
> under. Later, swap in whatever uniquely identifies what is being
> tested and measured (printed serial, scanned barcode, lot+sequence).

You'll see four passes. What you **won't** see in pytest's own output:
the actual voltages that were measured. `pytest -v` reports test names
and pass/fail. It does not know anything called a "measurement."

Pytest does have `record_property` / `record_xml_attribute` for
capturing extra data, but those write to JUnit XML as name/value
string pairs — there's no concept of units, limits, or a measurement
record. For hardware test you need a shape that knows about those
things.

## The gap

If a rail reads **3.19 V** one day and **3.41 V** the next, the
vanilla test prints `FAIL` both times and tells you nothing else. No
history, no trend, no value captured. You'd have to print-debug to
find out what the instrument actually read.

Stage 2 closes the gap: same tests, same drivers, but measurements
flow into a Parquet log through a `verify(name, value, limit)` call.
You get a searchable record of every reading alongside the pass/fail.
