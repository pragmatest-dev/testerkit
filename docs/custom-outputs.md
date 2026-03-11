# Writing Custom Outputs

Litmus output infrastructure is extensible via three protocols: **Exporter** (file format), **Transport** (file shipping), and **EventSubscriber** (real-time event processing).

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

## Writing a Custom EventSubscriber

Event subscribers receive typed Pydantic events in real time as they are emitted. Declare which event types you handle via `event_types`.

```python
# my_project/subscribers/my_database.py
from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    EventBase,
    MeasurementRecorded,
    SessionStarted,
)

class MyDatabaseSubscriber:
    format_name = "my_db"
    event_types = {SessionStarted, MeasurementRecorded}

    def open(self) -> None:
        self.conn = connect(...)

    def on_event(self, event: EventBase) -> None:
        if isinstance(event, SessionStarted):
            self.conn.insert("sessions", {
                "session_id": str(event.session_id),
                "station_id": event.station_id,
                "dut_serial": event.dut_serial,
            })
        elif isinstance(event, MeasurementRecorded):
            self.conn.insert("measurements", {
                "session_id": str(event.session_id),
                "name": event.measurement_name,
                "value": event.value,
                "units": event.units,
                "outcome": event.outcome,
            })

    def close(self) -> None:
        self.conn.close()
```

Register on an EventLog:

```python
event_log.add_subscriber(MyDatabaseSubscriber())
```

See [Subscribing to Events](guides/subscribing-to-events.md) for more patterns.

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
| Event subscriber | `EventSubscriber` | `format_name`, `event_types`, `open()`, `on_event(event: EventBase)`, `close()` |
