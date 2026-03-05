# Writing Custom Outputs

Litmus output infrastructure is extensible via three protocols: **Exporter** (file format), **Transport** (file shipping), and **StreamingDestination** (real-time per-measurement).

## Writing a Custom Exporter

```python
# my_project/exporters/my_format.py
from pathlib import Path
from litmus.data.models import TestRun

class MyFormatExporter:
    format_name = "my_format"

    def export(self, test_run: TestRun, output_path: Path) -> Path:
        """Convert TestRun to your format."""
        output_path.mkdir(parents=True, exist_ok=True)
        out_file = output_path / f"{str(test_run.id)[:8]}.myformat"

        # Access the full hierarchy:
        # test_run.steps → list[TestStep]
        #   step.vectors → list[TestVector]
        #     vector.measurements → list[Measurement]
        for step in test_run.steps:
            for vector in step.vectors:
                for m in vector.measurements:
                    # m.name, m.value, m.units, m.outcome, m.low_limit, ...
                    pass

        out_file.write_text("...")
        return out_file
```

## Writing a Custom Transport

```python
# my_project/transports/internal_server.py
from pathlib import Path
from litmus.schemas import OutputConfig

class InternalServerTransport:
    transport_name = "internal_server"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        """Ship file to your internal system."""
        server = config.extras["server"]
        # ... upload logic ...
        return f"{server}/uploads/{local_path.name}"
```

## Writing a Custom StreamingDestination

Streaming destinations receive a typed `MeasurementRow` model for each measurement in real time. The `open()` method receives the `TestRun` with full run-level context (DUT, station, operator) so you can write run-level headers before measurements arrive.

```python
# my_project/destinations/my_database.py
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.models import TestRun
from litmus.schemas import OutputConfig

class MyDatabaseDestination:
    format_name = "my_db"

    def open(self, config: OutputConfig, test_run: TestRun) -> None:
        self.conn = connect(config.extras["dsn_env"])
        # Write run-level record before measurements arrive
        self.conn.insert("runs", {
            "run_id": str(test_run.id),
            "dut_serial": test_run.dut.serial,
            "station_id": test_run.station_id,
        })

    def append_row(self, row: MeasurementRow) -> None:
        # Typed field access — IDE autocomplete works
        self.conn.insert("measurements", {
            "run_id": row.run_id,
            "name": row.measurement_name,
            "value": row.value,
            "units": row.units,
            "outcome": row.outcome,
            # Or flatten everything at once:
            # **row.to_flat_dict(),
        })

    def mark_run_boundary(self, run_id: str) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
```

## Registering Custom Outputs

In your `conftest.py`:

```python
from litmus.data.exporters import register_exporter
from litmus.data.transports import register_transport
from my_project.exporters.my_format import MyFormatExporter
from my_project.transports.internal_server import InternalServerTransport

register_exporter(MyFormatExporter())
register_transport(InternalServerTransport())
```

Then reference in `litmus.yaml`:

```yaml
outputs:
  - format: my_format
    output_dir: results/my_format/
    transport: internal_server
    server: https://internal.company.com
```

## Data Model Reference

The `TestRun` hierarchy (see `litmus/data/models.py`):

```
TestRun
├── id, started_at, ended_at, outcome
├── dut: DUT (serial, part_number, revision, lot_number)
├── station_id, operator_id, sequence_id, test_phase, git_commit
├── environment_json, station_config_yaml, product_spec_yaml
└── steps: list[TestStep]
      ├── name, index, started_at, ended_at, outcome
      └── vectors: list[TestVector]
            ├── index, attempt, params, observations
            └── measurements: list[Measurement]
                  ├── name, value, units, outcome
                  ├── low_limit, high_limit, nominal, comparator
                  ├── spec_id, spec_ref
                  └── instrument_name, dut_pin, fixture_point
```

For denormalized row dicts (used by StreamingDestination.append_row), see `litmus/data/backends/_row_helpers.py` — `build_run_metadata()` and `build_measurement_fields()`.

## Protocol Summary

| What | Protocol | Required |
|---|---|---|
| File format exporter | `Exporter` | `format_name`, `export(test_run, output_path) → Path` |
| Remote transport | `Transport` | `transport_name`, `send(local_path, config: OutputConfig) → str` |
| Streaming destination | `StreamingDestination` | `format_name`, `open(config, test_run)`, `append_row(row: MeasurementRow)`, `mark_run_boundary(run_id)`, `close()` |
