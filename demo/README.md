# Litmus Demo

This demo showcases the Litmus test framework with a simulated power board test.

## Quick Start

```bash
cd demo
python run_demo.py
```

## What's Included

- **specs/power_board.yaml** - Product specification for a DC-DC converter
- **stations/demo_station.yaml** - Station configuration with simulated instruments
- **tests/test_power_board.py** - Test suite using `@measure` decorator

## Running Tests Manually

```bash
# From the demo directory
pytest tests/ --dut-serial=DPB001-0001 -v

# With custom options
pytest tests/ \
  --dut-serial=MY-SERIAL \
  --station=my_station \
  --operator="Test Engineer" \
  --results-dir=./my_results \
  -v
```

## Querying Results

```python
import pyarrow.parquet as pq

# Read all measurements
table = pq.read_table("results/measurements")
for i in range(table.num_rows):
    print(f"{table.column('measurement_name')[i]}: {table.column('value')[i]}")

# Read test run summary
table = pq.read_table("results/test_runs")
print(f"Result: {table.column('pass_fail')[0]}")
```

## Test Limits

| Measurement | Nominal | Low | High | Units |
|-------------|---------|-----|------|-------|
| Input Voltage | 5.0 | 4.5 | 5.5 | V |
| Input Current | 0.010 | 0.005 | 0.015 | A |
| Output Voltage | 3.3 | 3.135 | 3.465 | V |
| Efficiency | 85 | 75 | 100 | % |
