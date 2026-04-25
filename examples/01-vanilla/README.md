# Stage 1 — Vanilla pytest

Pure pytest. No Litmus. This is the baseline every other stage builds on.

## Layout

```
01-vanilla/
├── pyproject.toml        # pytest as the only dep
├── pytest.ini            # disables the Litmus plugin so this is truly vanilla
├── conftest.py           # session-scoped `dut` fixture (FakeDut)
└── tests/
    └── test_rail.py      # one direct test + one parametrized sweep
```

## What this shows

- `assert` for pass/fail
- A `conftest.py` fixture shared across tests (`scope="session"`)
- `@pytest.mark.parametrize` for a sweep
- `FakeDut` stand-in so the example runs with no hardware

## Run it

```bash
cd examples/01-vanilla
uv run pytest -v
```

You'll see four passes. What you **won't** see: the actual voltages
that were measured. `pytest -v` reports test names and pass/fail.
It does not know anything called a "measurement."

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

Stage 2 closes the gap: same tests, same DUT, but measurements flow
into a Parquet log through a `verify(name, value, limit)` call. You
get a searchable record of every reading alongside the pass/fail.
