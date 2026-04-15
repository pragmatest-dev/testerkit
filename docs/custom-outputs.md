# Writing Custom Outputs

Litmus output infrastructure is extensible via two protocols: **EventSubscriber** (format output) and **Transport** (file shipping).

All output formats — CSV, JSON, STDF, HDF5, TDMS, MDF4, ATML — are implemented as EventSubscribers. They work both live (during a test session) and post-hoc (via event replay from stored IPC files).

## Subscriber Contract

Every subscriber follows the same constructor contract:

```python
from collections.abc import Callable
from pathlib import Path
from litmus.data.subscribers._output_file import OutputFile

class MySubscriber(EventSubscriber):
    format_name: str          # e.g. "csv"
    event_types: set[type]    # event classes to receive

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        """
        Args:
            output_dir: Results root. Subscriber owns its subfolder.
            on_output: Called after each file write with metadata.
                       Pipeline uses this to enqueue for transport.
        """

    def open(self) -> None: ...
    def on_event(self, event) -> None: ...
    def close(self) -> None: ...
```

### Rules

1. **`output_dir` is the results root** — subscriber creates its own subfolder (e.g. `exports/csv/`, `runs/`)
2. **`on_output` callback** — called after each file is successfully written, with an `OutputFile` descriptor. `None` = no transport.
3. **Crash-resilient** — files written before a crash are already enqueued via callback. No waiting for `close()`.

### `OutputFile` descriptor

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class OutputFile:
    path: Path          # absolute path to the written file
    format: str         # "parquet", "csv", "stdf", etc.
    run_id: str | None = None
```

### Subfolder conventions

| Subscriber | Subfolder |
|-----------|-----------|
| parquet | `runs/{date}/` |
| csv | `exports/csv/` |
| json | `exports/json/` |
| atml | `exports/atml/` |
| hdf5 | `exports/hdf5/` |
| mdf4 | `exports/mdf4/` |
| stdf | `exports/stdf/` |
| tdms | `exports/tdms/` |

## Writing a Custom Subscriber

```python
# my_project/subscribers/my_format.py
from collections.abc import Callable
from pathlib import Path
from litmus.data.events import (
    EventBase,
    MeasurementRecorded,
    RunStarted,
    RunEnded,
)
from litmus.data.subscribers._output_file import OutputFile

class MyFormatSubscriber(EventSubscriber):
    format_name = "my_format"
    event_types = {RunStarted, MeasurementRecorded, RunEnded}

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self._output_dir = output_dir / "exports" / "my_format"
        self._on_output = on_output
        self._run_started = None
        self._measurements = []
        self._written = False

    def open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: EventBase) -> None:
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, MeasurementRecorded):
            self._measurements.append(event)
        elif isinstance(event, RunEnded):
            self._write()

    def close(self) -> None:
        if not self._written:
            self._write()

    def _write(self) -> None:
        if self._written:
            return
        self._written = True
        s = self._run_started
        if not s:
            return

        run_id = str(s.run_id)[:8] if s.run_id else "unknown"
        out_file = self._output_dir / f"{run_id}.myformat"

        # Write self._measurements to your format ...

        if self._on_output:
            self._on_output(OutputFile(
                path=out_file,
                format="my_format",
                run_id=run_id,
            ))
```

## Writing a Custom Transport

```python
# my_project/transports/internal_server.py
from pathlib import Path
from litmus.models.project import OutputConfig

class InternalServerTransport(Transport):
    transport_name = "internal_server"

    def send(self, local_path: Path, config: OutputConfig) -> str:
        """Ship file to your internal system."""
        server = config.extras["server"]
        # ... upload logic ...
        return f"{server}/uploads/{local_path.name}"
```

## Registering Custom Outputs

Subclassing `EventSubscriber` or `Transport` auto-registers via `__init_subclass__`.
For third-party packages, declare entry points in `pyproject.toml`:

```toml
[project.entry-points."litmus.subscribers"]
my_format = "my_project.subscribers.my_format:MyFormatSubscriber"

[project.entry-points."litmus.transports"]
internal_server = "my_project.transports.internal_server:InternalServerTransport"
```

Then reference in `litmus.yaml`:

```yaml
outputs:
  - format: my_format
    transport: internal_server
    server: https://internal.company.com
```

## Post-hoc Export via Event Replay

Any subscriber can also be used for post-hoc conversion. The `replay_to_subscriber()` function reads stored event dicts and feeds them through a subscriber:

```python
from litmus.data.subscribers import replay_to_subscriber, get_subscriber_class

cls = get_subscriber_class("csv")
sub = cls(Path("results/"))
replay_to_subscriber(sub, event_dicts)
```

The CLI `litmus export` command uses this pattern:

```bash
litmus export abc123 -f csv
litmus export abc123 -f stdf -o results/stdf/
```

## Event Types Reference

Subscribers declare which event types they handle via `event_types`. Common types:

| Event | When emitted |
|---|---|
| `RunStarted` | Test session begins (metadata: station, DUT, operator) |
| `StepStarted` | Test step begins (name, path, description) |
| `MeasurementRecorded` | A measurement is taken (value, limits, outcome) |
| `StepEnded` | Test step completes (outcome) |
| `RunEnded` | Test session ends (overall outcome) |
| `InstrumentConnected` | Instrument is connected (role, driver, resource) |
| `SessionStarted` | Session envelope (multi-DUT orchestration) |

See `litmus/data/events.py` for the full list.

## Protocol Summary

| What | Protocol | Required |
|---|---|---|
| Format subscriber | `EventSubscriber` | `format_name`, `event_types`, `__init__(output_dir, *, on_output)`, `open()`, `on_event(event)`, `close()` |
| Remote transport | `Transport` | `transport_name`, `send(local_path, config: OutputConfig) → str` |
