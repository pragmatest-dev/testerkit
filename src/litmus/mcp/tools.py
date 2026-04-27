"""MCP tool implementations - 5 consolidated tools.

Tools:
- litmus: Unified CRUD (init, list, get, save, read)
- litmus_discover: Scan for VISA instruments
- litmus_match: Check compatibility
- litmus_run: Execute tests
- litmus_open: Get browser URL
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from litmus.models.enum_meta import lookup_enum as _lookup_enum_fn
from litmus.models.enum_meta import render_enum_reference as _render_enum_reference_fn

logger = logging.getLogger(__name__)

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
    if not term:
        return {
            "error": "Provide a term via 'id' parameter, "
            "e.g. litmus(action='lookup_enum', id='FRES')"
        }

    results = _lookup_enum_fn(term)
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
    return {"markdown": _render_enum_reference_fn()}


def litmus_tool(
    action: str,
    type: str | None = None,
    id: str | None = None,
    path: str | None = None,
    content: dict[str, Any] | None = None,
    create: bool = True,
    scaffold: bool = True,
    project: str | None = None,
) -> dict[str, Any]:
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
                    contents.append(
                        {
                            "name": item.name,
                            "type": "dir" if item.is_dir() else "file",
                        }
                    )

        return {
            "project_root": str(root),
            "contents": contents,
            "message": (
                f"Current directory: {root}. Use action='init' with path to initialize a project."
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

ENTITY_TYPES = [
    "station",
    "product",
    "fixture",
    "catalog",
    "instrument_asset",
    "run",
]
# Everything except "run" (read-only test results) can be saved.
SAVEABLE_TYPES = [
    "station",
    "product",
    "fixture",
    "catalog",
    "instrument_asset",
    "test",
]


def _list_entities(entity_type: str, project: str) -> dict[str, Any]:
    """List entities of a given type."""
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Valid: {ENTITY_TYPES}"}

    handlers = {
        "station": _list_stations,
        "product": _list_products,
        "fixture": _list_fixtures,
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


def _list_yaml_entities(
    project: str,
    dir_name: str,
    loader: Callable[[Path], Any],
    extractor: Callable[[Any, Path], dict[str, Any]],
) -> list[dict[str, Any]]:
    """List entities from a YAML directory.

    Args:
        project: Project path.
        dir_name: Subdirectory name (e.g. "stations").
        loader: Callable that takes a Path and returns a Pydantic model.
        extractor: Callable that takes (model, yaml_file) and returns a summary dict.
    """
    entity_dir = get_project_root(project) / dir_name
    if not entity_dir.exists():
        return []

    entities = []
    for yaml_file in entity_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            model = loader(yaml_file)
            entities.append(extractor(model, yaml_file))
        except (OSError, ValueError) as exc:
            logger.debug("Skipping %s: %s", yaml_file.name, exc)
            continue
    return entities


def _list_stations(project: str) -> list[dict[str, Any]]:
    """List all station configurations."""
    from litmus.store import load_station

    return _list_yaml_entities(
        project,
        "stations",
        load_station,
        lambda s, _f: {"id": s.id, "name": s.name, "location": s.location},
    )


def _list_products(project: str) -> list[dict[str, Any]]:
    """List all product specifications from products/ directory."""
    from litmus.store import list_products

    root = get_project_root(project)
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "revision": p.revision,
            "characteristics_count": len(p.characteristics),
        }
        for p in list_products(project_root=root)
    ]


def _list_fixtures(project: str) -> list[dict[str, Any]]:
    """List all fixture configurations."""
    from litmus.store import load_fixture

    return _list_yaml_entities(
        project,
        "fixtures",
        load_fixture,
        lambda f, yf: {
            "id": f.id,
            "name": f.name or yf.stem,
            "product_id": f.product_id,
            "point_count": len(f.points),
        },
    )


def _list_catalog_entries(project: str) -> list[dict[str, Any]]:
    """List available catalog entries (instrument models and capabilities)."""
    from litmus.store import find_catalog_dirs, load_catalog_from_directory

    root = get_project_root(project)
    entries = []
    for cat_dir in find_catalog_dirs(project_root=root):
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            entries.append({"id": entry_id, "type": entry.type, "name": entry.name})
    return entries


def _list_instrument_assets(project: str) -> list[dict[str, Any]]:
    """List instrument asset files (physical devices you own)."""
    from litmus.store import list_instrument_assets

    root = get_project_root(project)
    return [a.model_dump() for a in list_instrument_assets(project_root=root)]


def _list_runs(project: str) -> list[dict[str, Any]]:
    """List recent test runs."""
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root(project) / "results")
    backend = ParquetBackend(results_dir=results_dir)
    return [r.model_dump(exclude={"file_path"}) for r in backend.list_runs(limit=50)]


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


def _get_yaml_entity(
    entity_id: str,
    project: str,
    dir_name: str,
    entity_label: str,
    loader: Any,
) -> dict[str, Any]:
    """Load a single YAML entity by ID from a project subdirectory."""
    yaml_file = get_project_root(project) / dir_name / f"{entity_id}.yaml"
    if not yaml_file.exists():
        return {"error": f"{entity_label} '{entity_id}' not found"}
    try:
        return loader(yaml_file).model_dump()
    except (OSError, ValueError) as e:
        return {"error": f"Failed to load {entity_label.lower()}: {e}"}


def _get_station(station_id: str, project: str) -> dict[str, Any]:
    """Get station configuration."""
    from litmus.store import load_station

    return _get_yaml_entity(station_id, project, "stations", "Station", load_station)


def _get_product(product_id: str, project: str) -> dict[str, Any]:
    """Get product specification from products/{product_id}.yaml."""
    from litmus.store import get_product

    root = get_project_root(project)
    product = get_product(product_id, project_root=root)

    if product is None:
        return {"error": f"Product '{product_id}' not found in products/"}
    return product.model_dump()


def _get_fixture(fixture_id: str, project: str) -> dict[str, Any]:
    """Get fixture configuration."""
    from litmus.store import load_fixture

    return _get_yaml_entity(fixture_id, project, "fixtures", "Fixture", load_fixture)


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
    from litmus.api.schemas import build_run_view
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root(project) / "results")
    backend = ParquetBackend(results_dir=results_dir)
    run = backend.get_run(run_id)

    if not run:
        return {"error": f"Run '{run_id}' not found"}

    rows = backend.get_measurements(run_id)
    view = build_run_view(rows)
    if view.outcome is None:
        view.outcome = run.outcome
    return view.model_dump(mode="json")


# =============================================================================
# Save entity
# =============================================================================


def _validate_against_schema(
    entity_type: str,
    content: dict[str, Any],
) -> list[str]:
    """Validate content against the Pydantic model for this entity type.

    Returns a list of validation error strings (empty if valid).
    """
    from pydantic import ValidationError

    from litmus.schema_export import SCHEMA_MAP, FileType

    model = SCHEMA_MAP.get(cast(FileType, entity_type))
    if model is None:
        return []  # No schema for this type (e.g. test, instrument)

    try:
        model.model_validate(content)
        return []
    except ValidationError as e:
        return [f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in e.errors()]


def _schema_for_error(entity_type: str) -> dict[str, Any] | None:
    """Return the JSON Schema for an entity type, or None."""
    from litmus.schema_export import SCHEMA_MAP, FileType

    model = SCHEMA_MAP.get(cast(FileType, entity_type))
    if model is None:
        return None
    return model.model_json_schema()


def _save_generic_yaml(
    model_class: type,
    save_fn: Callable[..., Any],
    dir_name: str,
    entity_id: str,
    content: dict[str, Any],
    project: str,
) -> dict[str, Any]:
    """Shared save logic for uniform YAML entities."""
    obj = model_class.model_validate(content)
    root = get_project_root(project)
    save_fn(obj, project_root=root)
    return {"success": True, "path": str(root / dir_name / f"{entity_id}.yaml")}


def _save_entity(
    entity_type: str,
    id: str,
    content: dict[str, Any],
    project: str,
) -> dict[str, Any]:
    """Validate and save an entity."""
    if entity_type not in SAVEABLE_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Saveable: {SAVEABLE_TYPES}"}

    # Validate content against schema before saving
    schema_errors = _validate_against_schema(entity_type, content)
    if schema_errors:
        result: dict[str, Any] = {
            "success": False,
            "errors": schema_errors,
            "message": (
                f"Content does not match the {entity_type} schema. Fix the errors above and retry."
            ),
        }
        schema = _schema_for_error(entity_type)
        if schema:
            result["schema"] = schema
        return result

    if entity_type == "station":
        from litmus.models.station import StationConfig
        from litmus.store import check_instrument_types, save_station

        station = StationConfig.model_validate(content)
        _, type_warnings = check_instrument_types(
            {k: v.model_dump() for k, v in station.instruments.items()}
        )
        root = get_project_root(project)
        save_station(station, project_root=root)
        res: dict[str, Any] = {"success": True, "path": str(root / "stations" / f"{id}.yaml")}
        if type_warnings:
            res["warnings"] = type_warnings
        return res

    if entity_type == "product":
        from litmus.models.product import Product
        from litmus.store import save_product

        return _save_generic_yaml(Product, save_product, "products", id, content, project)

    if entity_type == "fixture":
        from litmus.models.test_config import FixtureConfig
        from litmus.store import save_fixture

        return _save_generic_yaml(FixtureConfig, save_fixture, "fixtures", id, content, project)

    if entity_type == "catalog":
        from litmus.models.catalog import InstrumentCatalogEntry
        from litmus.store import save_catalog_entry

        return _save_generic_yaml(
            InstrumentCatalogEntry, save_catalog_entry, "catalog", id, content, project
        )

    if entity_type == "instrument_asset":
        from litmus.models.instrument_asset import InstrumentAssetFile
        from litmus.store import save_instrument_asset

        return _save_generic_yaml(
            InstrumentAssetFile, save_instrument_asset, "instruments", id, content, project
        )

    return _save_test(id, content, project)


def _save_test(path: str, content: dict[str, Any], project: str) -> dict[str, Any]:
    """Save a Python test file."""
    if "code" not in content:
        return {"success": False, "errors": ["content.code is required"]}

    # Ensure .py extension for pytest discovery
    if not path.endswith(".py"):
        path = f"{path}.py"

    root = get_project_root(project)

    # Support both absolute-ish paths and relative paths
    if path.startswith("products/") or path.startswith("tests/"):
        filepath = root / path
    else:
        filepath = root / "tests" / path

    # Guard against path traversal (e.g. ../../etc/passwd.py)
    if not filepath.resolve().is_relative_to(root.resolve()):
        return {"success": False, "errors": ["Path must be within the project directory"]}

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

Tests are plain pytest functions. Vectors, limits, and mocks live in
the sidecar YAML next to this file (``test_{product_id}.yaml``). The
``context`` and ``verify`` fixtures come from the Litmus pytest plugin.
"""

import pytest


@pytest.mark.parametrize("temperature", [25, 85])
@pytest.mark.parametrize("load", [0.1, 0.5, 3.0])
def test_output_voltage(context, psu, dmm, verify) -> None:
    """Output voltage under various conditions."""
    psu.set_voltage(context.get_param("vin", 12.0))
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()))


def test_quiescent_current(context, psu, verify) -> None:
    """Quiescent current in uA."""
    psu.set_voltage(context.get_param("vin", 12.0))
    psu.enable_output()
    current_a = psu.measure_current()  # amps
    verify("quiescent_current", current_a * 1e6)  # uA


@pytest.mark.parametrize("load_current", [0.5, 1.0, 2.0])
def test_load_regulation(context, psu, dmm, eload, verify) -> None:
    """Output voltage under load."""
    psu.set_voltage(context.get_param("vin", 12.0))
    psu.enable_output()
    eload.set_current(context.get_param("load_current"))
    eload.enable()
    verify("output_voltage", float(dmm.measure_dc_voltage()))
    eload.disable()


================================================================================
FILE 2: tests/test_{product_id}.yaml
================================================================================

# Sidecar: vectors/limits/mocks keyed by test function name.
tests:
  test_output_voltage:
    limits:
      output_voltage:
        ref: output_voltage
        guardband_pct: 10
        comparator: GELE
  test_quiescent_current:
    limits:
      quiescent_current:
        high: 100
        comparator: LE
        units: uA
  test_load_regulation:
    limits:
      output_voltage:
        ref: output_voltage
        guardband_pct: 10
    mocks:
      dmm.measure_dc_voltage: 5.0

================================================================================
NOTES
================================================================================

- Vectors: use ``@pytest.mark.parametrize`` or sidecar ``vectors:``.
  Sidecar overrides decorator at collection time.
- Limits: declared per-measurement in sidecar; use ``ref:`` to derive
  from a product characteristic, or inline ``low/high`` for direct
  bounds. Condition-indexed bands (``when:``) also supported.
- Mocks: sidecar ``mocks:`` installs per-test. Use
  ``--mock-instruments`` to run without hardware.
- The framework checks limits inside ``verify(name, value)`` and
  records a measurement row per call.
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

INSTRUMENT_YAML_TEMPLATE = """instrument:
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
"""

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
                "Tests are plain pytest functions; use context/verify fixtures",
                "Vectors, limits, and mocks live in the sidecar YAML next to the test",
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
    except (OSError, ValueError):
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
            contents.append(
                {
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                }
            )
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
    except (OSError, UnicodeDecodeError) as e:
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

    except (ImportError, OSError, RuntimeError) as e:
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
        root = get_project_root(project) if project else None
        product = get_product(product_id, project_root=root)
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

                compatible.append(
                    {
                        "station_id": sid,
                        "compatible": len(missing) == 0,
                        "missing_instruments": list(missing) if missing else None,
                    }
                )

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
        "-v",
        "--tb=short",
        "--mock-instruments",
    ]

    started_at = datetime.now(UTC)

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
        run_id = recent_runs[0].test_run_id or "unknown" if recent_runs else "unknown"

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
    except (OSError, RuntimeError) as e:
        return {"error": f"Failed to run tests: {e}"}


# =============================================================================
# Tool 5: open
# =============================================================================


def open_tool(entity_type: str, id: str, base_url: str = "http://localhost:8000") -> dict[str, Any]:
    """Get URL to open entity in browser UI."""
    routes = {
        "product": f"/products/{id}",
        "station": f"/stations/{id}",
        "run": f"/results/{id}",
        "fixture": f"/fixtures/{id}",
    }

    if entity_type not in routes:
        return {"error": f"Unknown type '{entity_type}'. Valid: {list(routes.keys())}"}

    url = f"{base_url}{routes[entity_type]}"

    return {
        "success": True,
        "url": url,
        "message": f"Open {url} to view/edit {entity_type} '{id}'",
    }


def _resolve_results_dir(project: str | None) -> Path | None:
    """Resolve results dir from a project path."""
    if project:
        return get_project_root(project) / "results"
    return None


def events_query(
    session_id: str | None = None,
    event_type: str | None = None,
    role: str | None = None,
    since: str | None = None,
    limit: int = 100,
    *,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Query events from the event store.

    Shared implementation for HTTP API and MCP tool.

    Args:
        session_id: Filter by session UUID.
        event_type: Filter by event type (e.g. "instrument.read").
        role: Filter by instrument role.
        since: ISO timestamp — only events after this time.
        limit: Max events to return (default 100).
        results_dir: Explicit results directory (takes precedence).
    """
    from litmus.data.event_store import EventStore

    store = EventStore(_results_dir=results_dir)
    try:
        since_dt = datetime.fromisoformat(since) if since else None
        sid = UUID(session_id) if session_id else None
        events = store.events(
            session_id=sid,
            event_type=event_type,
            role=role,
            since=since_dt,
        )
        return {"events": events[:limit], "count": len(events[:limit])}
    finally:
        store.close()


def sessions_query(*, results_dir: Path | None = None) -> dict[str, Any]:
    """List known sessions with metadata from SessionStarted events.

    Shared implementation for HTTP API and MCP tool.
    """
    from litmus.data.event_store import EventStore

    store = EventStore(_results_dir=results_dir)
    try:
        sessions = store.sessions()
        return {"sessions": sessions, "count": len(sessions)}
    finally:
        store.close()


def session_detail_query(
    session_id: str,
    *,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Get events for a specific session.

    Shared implementation for HTTP API and MCP tool.
    Returns dict with session_id and events, or None if not found.
    """
    from uuid import UUID

    from litmus.data.event_store import EventStore

    store = EventStore(_results_dir=results_dir)
    try:
        sid = UUID(session_id)
        events = store.events(session_id=sid)
        if not events:
            return {"session_id": session_id, "events": None}
        return {"session_id": session_id, "events": events}
    finally:
        store.close()


def channels_query(
    channel_id: str,
    session_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    last_n: int | None = None,
    max_points: int | None = None,
    *,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """Query channel data from the channel store.

    Shared implementation for HTTP API and MCP tool.
    """
    from litmus.data.channels.store import ChannelStore

    channels_dir = (results_dir / "channels") if results_dir else Path("results/channels")
    if not channels_dir.exists():
        return {"channel_id": channel_id, "data": []}

    store = ChannelStore(channels_dir, uuid4())
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None
    table = store.query(
        channel_id,
        session_id=session_id,
        start=since_dt,
        end=until_dt,
        last_n=last_n,
        max_points=max_points,
    )
    return {"channel_id": channel_id, "data": table.to_pylist()}


def channels_list_query(*, results_dir: Path | None = None) -> dict[str, Any]:
    """List known channels from the channel registry.

    Shared implementation for HTTP API and MCP tool.
    """
    channels_dir = (results_dir / "channels") if results_dir else Path("results/channels")
    registry_path = channels_dir / "_registry.json"
    if not registry_path.exists():
        return {"channels": {}}
    return {"channels": json.loads(registry_path.read_text())}


# Thin wrappers for MCP tools (resolve project → results_dir)


def events_tool(
    session_id: str | None = None,
    event_type: str | None = None,
    role: str | None = None,
    since: str | None = None,
    limit: int = 100,
    project: str | None = None,
) -> dict[str, Any]:
    """Query events from the event store (MCP tool wrapper)."""
    return events_query(
        session_id,
        event_type,
        role,
        since,
        limit,
        results_dir=_resolve_results_dir(project),
    )


def sessions_tool(project: str | None = None) -> dict[str, Any]:
    """List known sessions (MCP tool wrapper)."""
    return sessions_query(results_dir=_resolve_results_dir(project))


def channels_tool(
    channel_id: str,
    session_id: str | None = None,
    last_n: int | None = None,
    max_points: int | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Query channel data (MCP tool wrapper)."""
    return channels_query(
        channel_id,
        session_id=session_id,
        last_n=last_n,
        max_points=max_points,
        results_dir=_resolve_results_dir(project),
    )


def schema_tool(yaml_type: str | None = None) -> dict[str, Any]:
    """Get JSON Schema for Litmus YAML file types.

    Returns the JSON Schema so AI agents can validate generated YAML
    before saving it.

    Args:
        yaml_type: A file type from SCHEMA_MAP (e.g. catalog, product,
            station, fixture, instrument_asset, project).
            If None, returns the list of available types.

    Returns:
        The JSON Schema dict, or list of available types.
    """
    from litmus.schema_export import SCHEMA_MAP

    if yaml_type is None:
        return {
            "success": True,
            "available_types": list(SCHEMA_MAP.keys()),
            "message": "Pass yaml_type to get the schema for a specific type.",
        }

    if yaml_type not in SCHEMA_MAP:
        return {"error": f"Unknown type '{yaml_type}'. Valid: {list(SCHEMA_MAP.keys())}"}

    model = SCHEMA_MAP[yaml_type]
    return {
        "success": True,
        "type": yaml_type,
        "schema": model.model_json_schema(),
    }


# ---------------------------------------------------------------------------
# Tool 10: litmus_gold — gold layer analytics
# ---------------------------------------------------------------------------

GoldAction = Literal["summary", "pareto", "cpk", "trend", "retest", "time_loss"]
_GOLD_ACTIONS: tuple[GoldAction, ...] = (
    "summary",
    "pareto",
    "cpk",
    "trend",
    "retest",
    "time_loss",
)


def gold_tool(
    action: str,
    product: str | None = None,
    station: str | None = None,
    phase: str | None = None,
    since: str | None = None,
    until: str | None = None,
    period: str = "day",
    top_n: int = 10,
    min_samples: int = 10,
    project: str | None = None,
) -> dict[str, Any]:
    """Query pre-aggregated manufacturing metrics (DuckDB SQL on silver).

    Args:
        action: One of: summary, pareto, cpk, trend, retest, time_loss.
        product: Filter by product/part number.
        station: Filter by station name.
        phase: Filter by test phase (default: exclude development, 'all' = no filter).
        since: Start date (ISO format, inclusive).
        until: End date (ISO format, inclusive).
        period: Time bucket — day, week, or month (default: day).
        top_n: Number of top failures for pareto (default: 10).
        min_samples: Minimum sample count for cpk (default: 10).
        project: Project root path.
    """
    if action not in _GOLD_ACTIONS:
        return {"error": f"Unknown action '{action}'. Valid: {list(_GOLD_ACTIONS)}"}

    from litmus.analysis.gold import GoldStore

    results_dir = _resolve_results_dir(project)
    store = GoldStore(_results_dir=results_dir)

    kwargs: dict[str, Any] = {
        "product": product,
        "station": station,
        "phase": phase,
        "since": since,
        "until": until,
    }

    match action:
        case "summary":
            return {"data": store.yield_summary(**kwargs, period=period)}
        case "pareto":
            return {"data": store.pareto(**kwargs, top_n=top_n)}
        case "cpk":
            return {"data": store.cpk(**kwargs, min_samples=min_samples)}
        case "trend":
            return {"data": store.trend(**kwargs, period=period)}
        case "retest":
            return {"data": store.retest(**kwargs, period=period)}
        case "time_loss":
            return {"data": store.time_loss(**kwargs, period=period)}
        case _:
            return {"error": f"Unknown action '{action}'"}
