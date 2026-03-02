"""MCP tool implementations - 5 consolidated tools.

Tools:
- litmus: Unified CRUD (init, list, get, save, read)
- litmus_discover: Scan for VISA instruments
- litmus_match: Check compatibility
- litmus_run: Execute tests
- litmus_open: Get browser URL
"""

from pathlib import Path
from typing import Any, cast

# =============================================================================
# Project root management
# =============================================================================


def get_project_root(project: str | None = None) -> Path:
    """Get the project root path.

    Args:
        project: Explicit project path. If None, uses cwd.

    Returns:
        Resolved project root path.
    """
    if project:
        return Path(project).expanduser().resolve()
    return Path.cwd()


# =============================================================================
# Tool 1: litmus (unified CRUD)
# =============================================================================


def _lookup_enum(term: str) -> dict[str, Any]:
    """Look up enum value by abbreviation."""
    from litmus.config.enum_meta import lookup_enum

    if not term:
        return {
            "error": "Provide a term via 'id' parameter, "
            "e.g. litmus(action='lookup_enum', id='FRES')"
        }

    results = lookup_enum(term)
    if not results:
        return {"term": term, "candidates": [], "message": f"No matches for '{term}'"}

    return {
        "term": term,
        "candidates": [
            {
                "enum_value": r.enum_value,
                "enum_type": r.enum_type,
                "name": r.name,
                "instrument_classes": r.instrument_classes,
                "matched_on": r.matched_on,
            }
            for r in results
        ],
    }


def _enum_reference() -> dict[str, Any]:
    """Return full enum reference as markdown."""
    from litmus.config.enum_meta import render_enum_reference

    return {"markdown": render_enum_reference()}


def litmus_tool(
    action: str,
    type: str | None = None,
    id: str | None = None,
    path: str | None = None,
    content: dict[str, Any] | None = None,
    create: bool = True,
    scaffold: bool = True,
    project: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Unified CRUD operations dispatcher.

    Args:
        project: Project root path. Required for list/get/save/read actions.
                 For init action, use 'path' parameter instead.
    """
    valid_actions = ["init", "list", "get", "save", "read", "lookup_enum", "enum_reference"]
    if action not in valid_actions:
        return {"error": f"Unknown action '{action}'. Valid: {valid_actions}"}

    if action == "lookup_enum":
        return _lookup_enum(id or path or "")

    if action == "enum_reference":
        return _enum_reference()

    if action == "init":
        return _init_project(path, create, scaffold)

    # All other actions require project parameter
    if not project:
        return {
            "error": f"action='{action}' requires 'project' parameter"
            " - use the path from litmus(action='init')"
        }

    if action == "list":
        if not type:
            return {"error": "action='list' requires 'type' parameter"}
        return _list_entities(type, project)
    elif action == "get":
        if not type:
            return {"error": "action='get' requires 'type' parameter"}
        if not id:
            return {"error": "action='get' requires 'id' parameter"}
        return _get_entity(type, id, project)
    elif action == "save":
        if not type:
            return {"error": "action='save' requires 'type' parameter"}
        if not id:
            return {"error": "action='save' requires 'id' parameter"}
        if not content:
            return {"error": "action='save' requires 'content' parameter"}
        return _save_entity(type, id, content, project)
    elif action == "read":
        if not path:
            return {"error": "action='read' requires 'path' parameter"}
        return _read_file(path, project)

    return {"error": "Not implemented"}


# =============================================================================
# Init project
# =============================================================================


def _init_project(
    path: str | None = None,
    create: bool = True,
    scaffold: bool = True,
) -> dict[str, Any]:
    """Initialize or switch to a project directory.

    Uses shared scaffolding from litmus.init for consistency with CLI.
    """
    from litmus.init import get_project_contents, init_project

    # If no path, report current status
    if path is None:
        root = get_project_root()
        contents = []
        if root.exists():
            for item in sorted(root.iterdir()):
                if not item.name.startswith("."):
                    contents.append({
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                    })

        return {
            "project_root": str(root),
            "contents": contents,
            "message": (
                f"Current directory: {root}."
                " Use action='init' with path to initialize a project."
            ),
        }

    project_path = Path(path).expanduser().resolve()

    if create and not project_path.exists():
        project_path.mkdir(parents=True)

    if not project_path.exists():
        return {"error": f"Path does not exist: {path}"}

    if not project_path.is_dir():
        return {"error": f"Path is not a directory: {path}"}

    created_dirs: list[str] = []
    created_files: list[str] = []

    if scaffold:
        # Use shared scaffolding logic
        result = init_project(project_path, git=True)
        created_dirs = result["created_dirs"]
        created_files = result["created_files"]

    # List contents
    contents = get_project_contents(project_path)

    return {
        "success": True,
        "project_root": str(project_path),
        "created_directories": created_dirs,
        "created_files": created_files,
        "contents": contents,
        "message": f"Project initialized at {project_path}",
        "next_steps": [
            "Run 'uv sync' to install dependencies",
            "Use litmus(action='read', path='template:test') to see test pattern",
            "Create a product with litmus(action='save', type='product', ...)",
        ],
    }


# =============================================================================
# List entities
# =============================================================================

ENTITY_TYPES = ["station", "product", "fixture", "sequence", "catalog", "instrument_asset", "run"]


def _list_entities(entity_type: str, project: str) -> list[dict[str, Any]] | dict[str, Any]:
    """List entities of a given type."""
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Valid: {ENTITY_TYPES}"}

    handlers = {
        "station": _list_stations,
        "product": _list_products,
        "fixture": _list_fixtures,
        "sequence": _list_sequences,
        "catalog": _list_catalog_entries,
        "instrument_asset": _list_instrument_assets,
        "run": _list_runs,
    }
    items = handlers[entity_type](project)
    return {
        "type": entity_type,
        "count": len(items),
        "items": items,
    }


def _list_stations(project: str) -> list[dict[str, Any]]:
    """List all station configurations."""
    from litmus.store import load_station

    stations = []
    stations_dir = get_project_root(project) / "stations"

    if not stations_dir.exists():
        return []

    for yaml_file in stations_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            station = load_station(yaml_file)
            stations.append({
                "id": station.id,
                "name": station.name,
                "location": station.location,
            })
        except Exception:
            continue

    return stations


def _list_products(project: str) -> list[dict[str, Any]]:
    """List all product specifications from products/ directory."""
    import os

    from litmus.store import list_products

    old_cwd = os.getcwd()
    try:
        os.chdir(get_project_root(project))
        products = list_products()
    finally:
        os.chdir(old_cwd)

    return [{"id": p.id, "name": p.name, "description": p.description} for p in products]


def _list_fixtures(project: str) -> list[dict[str, Any]]:  # noqa: C901
    """List all fixture configurations."""
    from litmus.store import load_fixture

    fixtures = []
    fixtures_dir = get_project_root(project) / "fixtures"

    if not fixtures_dir.exists():
        return []

    for yaml_file in fixtures_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            fixture = load_fixture(yaml_file)
            fixtures.append({
                "id": fixture.id,
                "name": fixture.name or yaml_file.stem,
                "product_id": fixture.product_id,
                "point_count": len(fixture.points),
            })
        except Exception:
            continue

    return fixtures


def _list_sequences(project: str) -> list[dict[str, Any]]:
    """List available test sequences."""
    from litmus.store import load_sequence

    sequences = []
    seq_dir = get_project_root(project) / "sequences"

    if not seq_dir.exists():
        return []

    for yaml_file in seq_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            seq = load_sequence(yaml_file)
            sequences.append({
                "id": seq.id,
                "name": seq.name or yaml_file.stem,
                "description": seq.description,
            })
        except Exception:
            continue

    return sequences


def _list_catalog_entries(project: str) -> list[dict[str, Any]]:
    """List available catalog entries (instrument models and capabilities)."""
    from litmus.ui.shared.services import discover_instrument_types

    return discover_instrument_types()


def _list_instrument_assets(project: str) -> list[dict[str, Any]]:
    """List instrument asset files (physical devices you own)."""
    from litmus.store import list_instrument_assets

    return [a.model_dump() for a in list_instrument_assets()]


def _list_runs(project: str) -> list[dict[str, Any]]:
    """List recent test runs."""
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root(project) / "results")
    backend = ParquetBackend(results_dir=results_dir)
    return backend.list_runs(limit=50)


# =============================================================================
# Get entity
# =============================================================================


def _get_entity(entity_type: str, id: str, project: str) -> dict[str, Any]:
    """Get full details of an entity."""
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Valid: {ENTITY_TYPES}"}

    handlers = {
        "station": _get_station,
        "product": _get_product,
        "fixture": _get_fixture,
        "sequence": _get_sequence,
        "catalog": _get_catalog_entry,
        "instrument_asset": _get_instrument_asset,
        "run": _get_run,
    }
    result = handlers[entity_type](id, project)

    # Pass through errors unwrapped
    if "error" in result:
        return result

    return {
        "type": entity_type,
        "id": id,
        "data": result,
    }


def _get_station(station_id: str, project: str) -> dict[str, Any]:
    """Get station configuration."""
    from litmus.store import load_station

    yaml_file = get_project_root(project) / "stations" / f"{station_id}.yaml"

    if not yaml_file.exists():
        return {"error": f"Station '{station_id}' not found"}

    try:
        return load_station(yaml_file).model_dump()
    except Exception as e:
        return {"error": f"Failed to load station: {e}"}


def _get_product(product_id: str, project: str) -> dict[str, Any]:
    """Get product specification from products/{product_id}.yaml."""
    import os

    from litmus.store import get_product

    old_cwd = os.getcwd()
    try:
        os.chdir(get_project_root(project))
        product = get_product(product_id)
    finally:
        os.chdir(old_cwd)

    if product is None:
        return {"error": f"Product '{product_id}' not found in products/"}
    return product.model_dump(mode="json")


def _get_fixture(fixture_id: str, project: str) -> dict[str, Any]:
    """Get fixture configuration."""
    from litmus.store import load_fixture

    yaml_file = get_project_root(project) / "fixtures" / f"{fixture_id}.yaml"
    if yaml_file.exists():
        try:
            return load_fixture(yaml_file).model_dump()
        except Exception as e:
            return {"error": f"Failed to load fixture: {e}"}

    return {"error": f"Fixture '{fixture_id}' not found"}


def _get_sequence(sequence_id: str, project: str) -> dict[str, Any]:
    """Get test sequence."""
    from litmus.store import load_sequence

    yaml_file = get_project_root(project) / "sequences" / f"{sequence_id}.yaml"
    if yaml_file.exists():
        try:
            return load_sequence(yaml_file).model_dump()
        except Exception as e:
            return {"error": f"Failed to load sequence: {e}"}

    return {"error": f"Sequence '{sequence_id}' not found"}


def _get_catalog_entry(entry_id: str, project: str) -> dict[str, Any]:
    """Get a catalog entry by type or ID."""
    from litmus.ui.shared.services import load_catalog_entry_by_type

    result = load_catalog_entry_by_type(entry_id)
    if not result:
        return {"error": f"Catalog entry '{entry_id}' not found"}
    return result.model_dump()


def _get_instrument_asset(instrument_id: str, project: str) -> dict[str, Any]:
    """Get an instrument asset file by ID."""
    from litmus.ui.shared.services import load_instrument_asset_by_id

    result = load_instrument_asset_by_id(instrument_id)
    if not result:
        return {"error": f"Instrument asset '{instrument_id}' not found"}
    return result.model_dump()


def _get_run(run_id: str, project: str) -> dict[str, Any]:
    """Get test run details."""
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root(project) / "results")
    backend = ParquetBackend(results_dir=results_dir)
    run = backend.get_run(run_id)

    if not run:
        return {"error": f"Run '{run_id}' not found"}

    run["measurements"] = backend.get_measurements(run_id)
    return run


# =============================================================================
# Save entity
# =============================================================================


def _validate_against_schema(
    entity_type: str, content: dict[str, Any],
) -> list[str]:
    """Validate content against the Pydantic model for this entity type.

    Returns a list of validation error strings (empty if valid).
    """
    from pydantic import ValidationError

    from litmus.schemas import SCHEMA_MAP, FileType

    model = SCHEMA_MAP.get(cast(FileType, entity_type))
    if model is None:
        return []  # No schema for this type (e.g. test, instrument)

    try:
        model.model_validate(content)
        return []
    except ValidationError as e:
        return [
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]


def _schema_for_error(entity_type: str) -> dict[str, Any] | None:
    """Return the JSON Schema for an entity type, or None."""
    from litmus.schemas import SCHEMA_MAP, FileType

    model = SCHEMA_MAP.get(cast(FileType, entity_type))
    if model is None:
        return None
    return model.model_json_schema()


def _save_entity(
    entity_type: str, id: str, content: dict[str, Any], project: str,
) -> dict[str, Any]:
    """Validate and save an entity."""
    valid_types = ["station", "product", "fixture", "sequence", "instrument", "test"]
    if entity_type not in valid_types:
        return {"error": f"Unknown type '{entity_type}'. Valid: {valid_types}"}

    # Validate content against schema before saving
    schema_errors = _validate_against_schema(entity_type, content)
    if schema_errors:
        result: dict[str, Any] = {
            "success": False,
            "errors": schema_errors,
            "message": (
                f"Content does not match the {entity_type} schema. "
                "Fix the errors above and retry."
            ),
        }
        schema = _schema_for_error(entity_type)
        if schema:
            result["schema"] = schema
        return result

    if entity_type == "station":
        return _save_station(id, content, project)
    elif entity_type == "product":
        return _save_product(id, content, project)
    elif entity_type == "fixture":
        return _save_fixture(id, content, project)
    elif entity_type == "sequence":
        return _save_sequence(id, content, project)
    elif entity_type == "instrument":
        return _save_instrument(id, content, project)
    elif entity_type == "test":
        return _save_test(id, content, project)

    return {"error": "Not implemented"}


def _save_station(station_id: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save station configuration — validate through StationConfig, write via dump_yaml."""
    from pydantic import ValidationError

    from litmus.config.fmt import dump_yaml
    from litmus.config.normalize import check_instrument_types
    from litmus.schemas import StationConfig

    try:
        station = StationConfig.model_validate(content)
    except ValidationError as e:
        return {
            "success": False,
            "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        }

    _, type_warnings = check_instrument_types(
        {k: v.model_dump() for k, v in station.instruments.items()}
    )

    stations_dir = get_project_root(project) / "stations"
    stations_dir.mkdir(parents=True, exist_ok=True)
    filepath = stations_dir / f"{station_id}.yaml"
    filepath.write_text(dump_yaml(station.model_dump(exclude_none=True)))

    result: dict[str, Any] = {"success": True, "path": str(filepath)}
    if type_warnings:
        result["warnings"] = type_warnings
    return result


def _save_product(product_id: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save product specification — validate through Product, write via store."""
    import os

    from pydantic import ValidationError

    from litmus.products.models import Product
    from litmus.store import save_product

    try:
        product = Product.model_validate(content)
    except ValidationError as e:
        return {
            "success": False,
            "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        }

    old_cwd = os.getcwd()
    try:
        os.chdir(get_project_root(project))
        save_product(product)
    finally:
        os.chdir(old_cwd)

    products_dir = get_project_root(project) / "products"
    return {"success": True, "path": str(products_dir / f"{product_id}.yaml")}


def _save_fixture(fixture_id: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save fixture configuration — validate through FixtureConfig, write via dump_yaml."""
    from pydantic import ValidationError

    from litmus.config.fmt import dump_yaml
    from litmus.config.models import FixtureConfig

    try:
        fixture = FixtureConfig.model_validate(content)
    except ValidationError as e:
        return {
            "success": False,
            "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        }

    fixtures_dir = get_project_root(project) / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    filepath = fixtures_dir / f"{fixture_id}.yaml"
    filepath.write_text(dump_yaml(fixture.model_dump(exclude_none=True)))

    return {"success": True, "path": str(filepath)}


def _save_sequence(sequence_id: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save test sequence — validate through TestSequenceConfig, write via dump_yaml."""
    from pydantic import ValidationError

    from litmus.config.fmt import dump_yaml
    from litmus.config.models import TestSequenceConfig

    try:
        sequence = TestSequenceConfig.model_validate(content)
    except ValidationError as e:
        return {
            "success": False,
            "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        }

    sequences_dir = get_project_root(project) / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)
    filepath = sequences_dir / f"{sequence_id}.yaml"
    filepath.write_text(dump_yaml(sequence.model_dump(exclude_none=True)))

    return {"success": True, "path": str(filepath)}


def _save_instrument(instrument_type: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save instrument asset file — validate through InstrumentAssetFile, write via dump_yaml."""
    from pydantic import ValidationError

    from litmus.config.fmt import dump_yaml
    from litmus.schemas import InstrumentAssetFile

    try:
        asset = InstrumentAssetFile.model_validate(content)
    except ValidationError as e:
        return {
            "success": False,
            "errors": [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ],
        }

    instruments_dir = get_project_root(project) / "instruments"
    instruments_dir.mkdir(parents=True, exist_ok=True)
    filepath = instruments_dir / f"{instrument_type}.yaml"
    filepath.write_text(dump_yaml(asset.model_dump(exclude_none=True)))

    return {"success": True, "path": str(filepath)}


def _save_test(path: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save a Python test file."""
    if "code" not in content:
        return {"success": False, "errors": ["content.code is required"]}

    # Ensure .py extension for pytest discovery
    if not path.endswith(".py"):
        path = f"{path}.py"

    # Support both absolute-ish paths and relative paths
    if path.startswith("products/") or path.startswith("tests/"):
        filepath = get_project_root(project) / path
    else:
        filepath = get_project_root(project) / "tests" / path

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w") as f:
        f.write(content["code"])

    return {"success": True, "path": str(filepath)}


# =============================================================================
# Read file
# =============================================================================

TEST_TEMPLATE = '''
================================================================================
FILE 1: tests/test_{product_id}.py
================================================================================

"""Tests for {product_name}.

Inline decorator config is used for ad-hoc pytest runs.
When running with --sequence, sequence step config overrides decorator config.
"""

from litmus.execution import litmus_test


@litmus_test(
    config={{"vectors": {{"expand": "product", "temperature": [25, 85], "load": [0.1, 0.5, 3.0]}}}},
    limits={{"output_voltage": {{
        "ref": "output_voltage", "guardband_pct": 10, "comparator": "GELE"
    }}}},
)
def test_output_voltage(context, psu, dmm):
    """Signal output voltage under various conditions."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()


@litmus_test(
    limits={{"quiescent_current": {{"high": 100, "comparator": "LE", "units": "uA"}}}},
)
def test_quiescent_current(context, psu):
    """Signal quiescent current in uA."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    current_a = psu.measure_current()  # Returns float in Amps
    current_ua = current_a * 1e6  # Convert to µA
    return current_ua


@litmus_test(
    config={{"vectors": [
        {{"load_current": 0.5, "_mocks": {{"dmm.measure_dc_voltage": 5.02}}}},
        {{"load_current": 1.0, "_mocks": {{"dmm.measure_dc_voltage": 5.00}}}},
        {{"load_current": 2.0, "_mocks": {{"dmm.measure_dc_voltage": 4.95}}}},
    ]}},
    limits={{"output_voltage": {{"ref": "output_voltage", "guardband_pct": 10}}}},
)
def test_load_regulation(context, psu, dmm, eload):
    """Output voltage under load."""
    psu.set_voltage(context.get_in("vin", 12.0))
    psu.enable_output()
    eload.set_current(context.inputs["load_current"])
    eload.enable()
    vout = dmm.measure_dc_voltage()
    eload.disable()
    return vout


================================================================================
NOTES
================================================================================

Test config (vectors, limits, mocks, retry) comes from two sources:
1. Sequence steps (primary) — when running with --sequence
2. Inline decorator (fallback) — for ad-hoc pytest runs

Sequence step config REPLACES inline decorator config entirely.

Naming convention:
- Test/step level: vectors, limits, mocks, retry
- Inside vector dicts: _limits, _mocks (underscore = metadata)

Mock values:
- Test-level mocks: constant for all vectors
- Per-vector _mocks: different values per condition

Limits with ref: Auto-derived from SpecBand using vector conditions
- Nominal value ± accuracy from matching SpecBand
- Guardband applied for manufacturing margin
'''

INSTRUMENT_TEMPLATE = '''"""{instrument_name} driver.

Supports both real hardware (VISA) and mock mode (--mock-instruments).
"""

from typing import Any

from litmus.instruments.visa import VisaInstrument


class {class_name}(VisaInstrument):
    """{instrument_name} driver."""

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        mock_config: dict[str, Any] | None = None,
    ):
        super().__init__(resource=resource, simulate=simulate, sim_config=mock_config)


    # Implement capability methods here
    # def measure_voltage(self) -> float:
    #     return float(self.query("MEAS:VOLT:DC?"))
'''

INSTRUMENT_YAML_TEMPLATE = '''instrument:
  type: {instrument_type}
  name: {instrument_name}
  description: {description}
  channels:
    "1":
      terminals: [hi, lo]
      connector: binding_post

capabilities:
  - function: dc_voltage
    direction: input
    parameters:
      voltage:
        range: {{min: 0, max: 1000, units: V}}

scpi_commands:
  identify: "*IDN?"
  reset: "*RST"
'''

CAPABILITY_INTERFACES = """
Available capability interfaces:

MEASUREMENT (direction: input):
  dc_voltage   - Signal DC voltage
  ac_voltage   - Signal AC voltage
  dc_current   - Signal DC current
  ac_current   - Signal AC current
  resistance   - 2-wire resistance
  resistance_4w - 4-wire resistance
  frequency    - Signal frequency
  waveform     - Capture waveform (oscilloscope)
  temperature  - Signal temperature (RTD/thermocouple)

STIMULUS (direction: output):
  dc_voltage   - Source DC voltage (PSU, SMU)
  dc_current   - Source DC current (current source, SMU)

ELECTRONIC LOAD (direction: input):
  dc_current   - Sink current (constant current mode)
  dc_power     - Sink power (constant power mode)
  resistance   - Constant resistance mode

READBACK (readback: true):
  dc_voltage/input with readback: true  - Built-in voltage readback (PSU, eload)
  dc_current/input with readback: true  - Built-in current readback (PSU, eload)
  Readback capabilities are excluded from auto-matching to prevent competition with DMMs.

TERMINAL TOPOLOGY:
  terminals: [hi, lo]                 - Standard 2-wire (binding posts)
  terminals: [hi, lo, sense_hi, sense_lo] - 4-wire Kelvin (PSU remote sense)
  terminals: [signal]                 - Single-ended (BNC, probe)
  terminals: [hi, lo, guard]          - Guarded (triax, SMU)
  ground: floating | shared | earth   - Channel ground topology
  connector: binding_post | bnc | banana | triax | terminal_block | probe
"""


def _read_file(path: str, project: str) -> dict[str, Any]:
    """Read a file from the project directory."""
    # Special template paths
    if path == "template:test":
        return {
            "type": "template",
            "name": "test",
            "content": TEST_TEMPLATE.format(product_name="ProductName", product_id="product_id"),
            "notes": [
                "@litmus_test decorator handles vectors, limits, and logging",
                "Just return the measured value - limits come from config.yaml",
            ],
        }

    if path == "template:instrument":
        return {
            "type": "template",
            "name": "instrument_driver",
            "content": INSTRUMENT_TEMPLATE,
        }

    if path == "template:instrument_yaml":
        return {
            "type": "template",
            "name": "instrument_definition",
            "content": INSTRUMENT_YAML_TEMPLATE,
        }

    if path == "template:capabilities":
        return {
            "type": "reference",
            "name": "capability_interfaces",
            "content": CAPABILITY_INTERFACES,
        }

    # Security: only allow reading from project directory
    root = get_project_root(project)
    filepath = root / path

    try:
        filepath = filepath.resolve()
        if not str(filepath).startswith(str(root.resolve())):
            return {"error": "Path must be within project directory"}
    except Exception:
        return {"error": f"Invalid path: {path}"}

    if not filepath.exists():
        suggestions = []
        parent = filepath.parent
        if parent.exists():
            suggestions = [f.name for f in parent.glob("*") if f.is_file()][:5]

        return {
            "error": f"File not found: {path}",
            "suggestions": suggestions if suggestions else None,
        }

    if filepath.is_dir():
        contents = []
        for f in sorted(filepath.iterdir()):
            contents.append({
                "name": f.name,
                "type": "dir" if f.is_dir() else "file",
            })
        return {
            "type": "directory",
            "path": path,
            "contents": contents,
        }

    try:
        content = filepath.read_text()
        return {
            "type": "file",
            "path": path,
            "content": content,
        }
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


# =============================================================================
# Tool 2: discover
# =============================================================================


def discover_tool(protocols: list[str] | None = None) -> dict[str, Any]:
    """Scan for connected instruments using the pluggable discovery system.

    Args:
        protocols: Protocol names to scan (e.g. ["visa", "ni", "serial"]).
            None scans all registered protocols.

    Returns:
        Discovered instruments grouped by protocol with identity info.
    """
    try:
        from litmus.instruments.discovery import discover_and_identify, list_protocols

        results = discover_and_identify(protocols)

        from litmus.store import find_catalog_dirs, load_catalog_from_directory

        # Load catalog once for type lookups
        catalog = {}
        for cat_dir in find_catalog_dirs():
            catalog.update(load_catalog_from_directory(cat_dir))

        discovered = []
        for proto, items in results.items():
            for resource, info in items:
                entry: dict[str, Any] = {
                    "address": resource,
                    "protocol": proto,
                    "manufacturer": None,
                    "model": None,
                    "serial": None,
                    "type": None,
                    "catalog_ref": None,
                }
                if info:
                    entry["manufacturer"] = info.manufacturer
                    entry["model"] = info.model
                    entry["serial"] = info.serial
                    # Look up type from catalog by model match
                    for cat_id, cat_entry in catalog.items():
                        if (
                            info.model
                            and cat_entry.model
                            and info.model.lower() == cat_entry.model.lower()
                        ):
                            entry["type"] = cat_entry.type
                            entry["catalog_ref"] = cat_id
                            break
                discovered.append(entry)

        return {
            "success": True,
            "count": len(discovered),
            "protocols_scanned": list(results.keys()),
            "available_protocols": list_protocols(),
            "resources": discovered,
        }

    except Exception as e:
        return {"error": f"Discovery failed: {e}"}



# =============================================================================
# Tool 3: match
# =============================================================================


def match_tool(
    product_id: str | None = None,
    station_id: str | None = None,
    fixture_id: str | None = None,
    requirements: list[dict[str, Any]] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Check compatibility between products, stations, and fixtures.

    Args:
        product_id: Product ID to check compatibility for
        station_id: Station ID for detailed check
        fixture_id: Fixture ID to find compatible stations
        requirements: Ad-hoc capability requirements for catalog recommendations
        project: Project root path (required for fixture/requirements matching)
    """
    from litmus.matching.service import (
        check_station_compatibility,
        find_compatible_stations,
        get_required_capabilities,
    )
    from litmus.store import get_product

    # Requirements mode: recommend catalog instruments
    if requirements is not None:
        from litmus.matching.service import recommend_from_catalog

        project_path = get_project_root(project) if project else None
        return recommend_from_catalog(requirements, project_path)

    # Just product_id: find compatible stations
    if product_id and not station_id and not fixture_id:
        product = get_product(product_id)
        if not product:
            return {"error": f"Product '{product_id}' not found"}

        cap_reqs = get_required_capabilities(product)
        req_list = [
            {
                "characteristic": req.characteristic_name,
                "function": req.function.value,
                "direction": req.direction.value,
            }
            for req in cap_reqs
        ]

        matches = find_compatible_stations(product)
        stations = [
            {
                "station_id": m.station_id,
                "station_name": m.station_name,
                "compatible": m.compatible,
            }
            for m in matches
        ]

        return {
            "product_id": product_id,
            "required_capabilities": req_list,
            "compatible_stations": stations,
        }

    # Product + station: detailed check
    if product_id and station_id:
        project_root = get_project_root(project) if project else None
        result = check_station_compatibility(product_id, station_id, project_root)
        if not result:
            return {"error": "Product or station not found"}
        return result

    # Fixture: find stations with required instruments
    if fixture_id:
        if not project:
            return {"error": "fixture_id matching requires 'project' parameter"}

        fixture_result = _get_fixture(fixture_id, project)
        if "error" in fixture_result:
            return fixture_result

        points = fixture_result.get("points", {})
        required_instruments = set()
        for point in points.values():
            if point.get("instrument"):
                required_instruments.add(point["instrument"])

        stations = _list_stations(project)
        compatible = []

        for station in stations:
            sid = station.get("id", "")
            station_config = _get_station(sid, project)

            if "error" not in station_config:
                station_instruments = set(station_config.get("instruments", {}).keys())
                missing = required_instruments - station_instruments

                compatible.append({
                    "station_id": sid,
                    "compatible": len(missing) == 0,
                    "missing_instruments": list(missing) if missing else None,
                })

        return {
            "fixture_id": fixture_id,
            "required_instruments": list(required_instruments),
            "stations": compatible,
        }

    return {"error": "Provide product_id, product_id+station_id, or fixture_id"}


# =============================================================================
# Tool 4: run
# =============================================================================


def run_tool(test: str, station: str, serial: str, project: str | None = None) -> dict[str, Any]:
    """Execute tests and return results.

    Args:
        test: Test file path relative to project root
        station: Station ID
        serial: DUT serial number
        project: Project root path (required)
    """
    import subprocess
    import sys
    from datetime import datetime

    if not project:
        return {
            "error": "project parameter is required"
            " - pass the path returned from litmus(action='init')"
        }

    # Determine test target
    root = get_project_root(project)
    if test.endswith(".py") or "/" in test:
        test_path = root / test
        if not test_path.exists():
            return {"error": f"Test file not found: {test}"}
        test_targets = [str(test_path)]
    else:
        # Try to find test file
        possible_paths = [
            root / "tests" / f"test_{test}.py",
            root / "products" / test / "tests" / f"test_{test}.py",
        ]
        test_targets = []
        for p in possible_paths:
            if p.exists():
                test_targets = [str(p)]
                break

        if not test_targets:
            return {
                "error": f"Test not found for: {test}",
                "searched": [str(p) for p in possible_paths],
            }

    # Build pytest command
    pytest_path = Path(sys.executable).parent / "pytest"
    cmd = [
        str(pytest_path),
        *test_targets,
        f"--dut-serial={serial}",
        f"--station={station}",
        "--results-dir=results",
        "-v", "--tb=short",
        "--mock-instruments",
    ]

    started_at = datetime.now()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(root),
        )

        # Parse output for summary
        summary_line = ""
        for line in reversed(result.stdout.split("\n")):
            if "passed" in line or "failed" in line or "error" in line:
                summary_line = line.strip()
                break

        if result.returncode == 0:
            status = "passed"
        elif result.returncode == 1:
            status = "failed"
        else:
            status = "error"

        # Get run_id from results
        from litmus.data.backends.parquet import ParquetBackend
        backend = ParquetBackend(results_dir=str(root / "results"))
        recent_runs = backend.list_runs(limit=1)
        run_id = recent_runs[0].get("test_run_id", "unknown") if recent_runs else "unknown"

        return {
            "run_id": run_id,
            "status": status,
            "summary": summary_line,
            "test": test,
            "station": station,
            "serial": serial,
            "started_at": started_at.isoformat(),
            "output": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        }

    except subprocess.TimeoutExpired:
        return {"error": "Test execution timed out"}
    except Exception as e:
        return {"error": f"Failed to run tests: {e}"}


# =============================================================================
# Tool 5: open
# =============================================================================


def open_tool(
    entity_type: str, id: str, base_url: str = "http://localhost:8000"
) -> dict[str, Any]:
    """Get URL to open entity in browser UI."""
    routes = {
        "product": f"/products/{id}",
        "station": f"/stations/{id}",
        "run": f"/results/{id}",
        "fixture": f"/fixtures/{id}",
        "sequence": f"/sequences/{id}",
    }

    if entity_type not in routes:
        return {"error": f"Unknown type '{entity_type}'. Valid: {list(routes.keys())}"}

    url = f"{base_url}{routes[entity_type]}"

    return {
        "success": True,
        "url": url,
        "message": f"Open {url} to view/edit {entity_type} '{id}'",
    }


def schema_tool(yaml_type: str | None = None) -> dict[str, Any]:
    """Get JSON Schema for Litmus YAML file types.

    Returns the JSON Schema so AI agents can validate generated YAML
    before saving it.

    Args:
        yaml_type: A file type from SCHEMA_MAP (e.g. catalog, product,
            station, sequence, fixture, instrument_asset, project).
            If None, returns the list of available types.

    Returns:
        The JSON Schema dict, or list of available types.
    """
    from litmus.schemas import SCHEMA_MAP

    if yaml_type is None:
        return {
            "success": True,
            "available_types": list(SCHEMA_MAP.keys()),
            "message": "Pass yaml_type to get the schema for a specific type.",
        }

    if yaml_type not in SCHEMA_MAP:
        return {
            "error": f"Unknown type '{yaml_type}'. Valid: {list(SCHEMA_MAP.keys())}"
        }

    model = SCHEMA_MAP[yaml_type]
    return {
        "success": True,
        "type": yaml_type,
        "schema": model.model_json_schema(),
    }
