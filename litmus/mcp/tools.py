"""MCP tool implementations - 5 consolidated tools.

Tools:
- litmus: Unified CRUD (init, list, get, save, read)
- litmus_discover: Scan for VISA instruments
- litmus_match: Check compatibility
- litmus_run: Execute tests
- litmus_open: Get browser URL
"""

from pathlib import Path
from typing import Any

import yaml

# =============================================================================
# Project root management
# =============================================================================

_project_root: Path | None = None


def get_project_root() -> Path:
    """Get the active project root. Returns cwd if not explicitly set."""
    return _project_root or Path.cwd()


# =============================================================================
# Tool 1: litmus (unified CRUD)
# =============================================================================


def litmus_tool(
    action: str,
    type: str | None = None,
    id: str | None = None,
    path: str | None = None,
    content: dict[str, Any] | None = None,
    create: bool = True,
    scaffold: bool = True,
) -> dict[str, Any]:
    """Unified CRUD operations dispatcher."""
    valid_actions = ["init", "list", "get", "save", "read"]
    if action not in valid_actions:
        return {"error": f"Unknown action '{action}'. Valid: {valid_actions}"}

    if action == "init":
        return _init_project(path, create, scaffold)
    elif action == "list":
        if not type:
            return {"error": "action='list' requires 'type' parameter"}
        return _list_entities(type)
    elif action == "get":
        if not type:
            return {"error": "action='get' requires 'type' parameter"}
        if not id:
            return {"error": "action='get' requires 'id' parameter"}
        return _get_entity(type, id)
    elif action == "save":
        if not type:
            return {"error": "action='save' requires 'type' parameter"}
        if not id:
            return {"error": "action='save' requires 'id' parameter"}
        if not content:
            return {"error": "action='save' requires 'content' parameter"}
        return _save_entity(type, id, content)
    elif action == "read":
        if not path:
            return {"error": "action='read' requires 'path' parameter"}
        return _read_file(path)

    return {"error": "Not implemented"}


# =============================================================================
# Init project
# =============================================================================


def _init_project(
    path: str | None = None,
    create: bool = True,
    scaffold: bool = True,
) -> dict[str, Any]:
    """Initialize or switch to a project directory."""
    global _project_root

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
            "explicitly_set": _project_root is not None,
            "contents": contents,
            "message": "Current project root" if _project_root else "Using cwd (use action='init' with path to set)",
        }

    project_path = Path(path).expanduser().resolve()

    if create and not project_path.exists():
        project_path.mkdir(parents=True)

    if not project_path.exists():
        return {"error": f"Path does not exist: {path}"}

    if not project_path.is_dir():
        return {"error": f"Path is not a directory: {path}"}

    _project_root = project_path

    created_dirs = []
    created_files = []

    if scaffold:
        # Create directories
        for subdir in ["products", "stations", "sequences", "fixtures", "instruments", "tests", "results"]:
            dir_path = project_path / subdir
            if not dir_path.exists():
                dir_path.mkdir()
                created_dirs.append(subdir)

        # Create pyproject.toml
        pyproject_path = project_path / "pyproject.toml"
        if not pyproject_path.exists():
            project_name = project_path.name.replace("-", "_").replace(" ", "_")
            pyproject_content = f'''[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "litmus",  # Install from PyPI, or use: litmus @ git+https://github.com/your-org/litmus
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
'''
            pyproject_path.write_text(pyproject_content)
            created_files.append("pyproject.toml")

        # Create conftest.py
        conftest_path = project_path / "conftest.py"
        if not conftest_path.exists():
            conftest_content = '''"""Pytest configuration for Litmus tests.

NOTE: Mock instruments are RESPONSIVE - they return whatever value
was last SET. Tests should configure the stimulus, then measure.

Example test pattern:
    def test_output(psu, dmm):
        psu.set_voltage(12.0)  # Set input
        psu.enable_output()
        # DMM will return simulated output based on mock behavior
        return dmm.measure_voltage()
"""

import pytest
from litmus.instruments.mocks import MockDMM, MockPSU, MockELoad


@pytest.fixture
def dmm():
    """Simulated DMM - returns last measured/set value."""
    dmm = MockDMM()  # No hardcoded values - responsive to test
    dmm.connect()
    yield dmm
    dmm.disconnect()


@pytest.fixture
def psu():
    """Simulated PSU - tracks voltage/current settings."""
    psu = MockPSU()  # No hardcoded values - test sets conditions
    psu.connect()
    yield psu
    psu.disconnect()


@pytest.fixture
def eload():
    """Simulated electronic load."""
    eload = MockELoad()  # No hardcoded values
    eload.connect()
    yield eload
    eload.disconnect()
'''
            conftest_path.write_text(conftest_content)
            created_files.append("conftest.py")

        # Create litmus.yaml
        litmus_yaml_path = project_path / "litmus.yaml"
        if not litmus_yaml_path.exists():
            litmus_yaml_content = '''# Litmus project configuration
project:
  name: "{project_name}"

default_station: null

results:
  backend: parquet
  path: results/
'''
            litmus_yaml_path.write_text(litmus_yaml_content.format(
                project_name=project_path.name
            ))
            created_files.append("litmus.yaml")

        # Create .gitignore
        gitignore_path = project_path / ".gitignore"
        if not gitignore_path.exists():
            gitignore_content = '''# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# Litmus
results/

# IDE
.idea/
.vscode/
'''
            gitignore_path.write_text(gitignore_content)
            created_files.append(".gitignore")

    # List contents
    contents = []
    for item in sorted(project_path.iterdir()):
        if not item.name.startswith(".") or item.name == ".gitignore":
            contents.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
            })

    return {
        "success": True,
        "project_root": str(_project_root),
        "created_directories": created_dirs,
        "created_files": created_files,
        "contents": contents,
        "message": f"Project initialized at {_project_root}",
        "next_steps": [
            "Run 'uv sync' to install dependencies",
            "Use litmus(action='read', path='template:test') to see test pattern",
            "Create a product with litmus(action='save', type='product', ...)",
        ],
    }


# =============================================================================
# List entities
# =============================================================================

ENTITY_TYPES = ["station", "product", "fixture", "sequence", "instrument", "run"]


def _list_entities(entity_type: str) -> list[dict[str, Any]] | dict[str, Any]:
    """List entities of a given type."""
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Valid: {ENTITY_TYPES}"}

    if entity_type == "station":
        return _list_stations()
    elif entity_type == "product":
        return _list_products()
    elif entity_type == "fixture":
        return _list_fixtures()
    elif entity_type == "sequence":
        return _list_sequences()
    elif entity_type == "instrument":
        return _list_instruments()
    elif entity_type == "run":
        return _list_runs()

    return []


def _list_stations() -> list[dict[str, Any]]:
    """List all station configurations."""
    stations = []
    stations_dir = get_project_root() / "stations"

    if not stations_dir.exists():
        return []

    for yaml_file in stations_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "station" in data:
                    station_info = data["station"]
                    stations.append({
                        "id": station_info.get("id", yaml_file.stem),
                        "name": station_info.get("name", yaml_file.stem),
                        "location": station_info.get("location"),
                    })
        except Exception:
            continue

    return stations


def _list_products() -> list[dict[str, Any]]:
    """List all product specifications from products/ directory."""
    products = []
    products_dir = get_project_root() / "products"

    if not products_dir.exists():
        return []

    # Each product is a folder with spec.yaml inside
    for product_dir in products_dir.iterdir():
        if not product_dir.is_dir():
            continue
        spec_file = product_dir / "spec.yaml"
        if not spec_file.exists():
            continue
        try:
            with open(spec_file) as f:
                data = yaml.safe_load(f)
                if data and "product" in data:
                    product_info = data["product"]
                    products.append({
                        "id": product_info.get("id", product_dir.name),
                        "name": product_info.get("name", product_dir.name),
                        "description": product_info.get("description"),
                    })
        except Exception:
            continue

    return products


def _list_fixtures() -> list[dict[str, Any]]:
    """List all fixture configurations."""
    fixtures = []
    fixtures_dir = get_project_root() / "fixtures"

    if not fixtures_dir.exists():
        return []

    for yaml_file in fixtures_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "fixture" in data:
                    fixture_info = data["fixture"]
                    points = data.get("points", {})
                    fixtures.append({
                        "id": fixture_info.get("id", yaml_file.stem),
                        "name": fixture_info.get("name", yaml_file.stem),
                        "product_id": fixture_info.get("product_id"),
                        "point_count": len(points),
                    })
        except Exception:
            continue

    return fixtures


def _list_sequences() -> list[dict[str, Any]]:
    """List available test sequences."""
    sequences = []
    seq_dir = get_project_root() / "sequences"

    if not seq_dir.exists():
        return []

    for yaml_file in seq_dir.glob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "sequence" in data:
                    seq_info = data["sequence"]
                    sequences.append({
                        "id": seq_info.get("id", yaml_file.stem),
                        "name": seq_info.get("name", yaml_file.stem),
                        "description": seq_info.get("description"),
                    })
        except Exception:
            continue

    return sequences


def _list_instruments() -> list[dict[str, Any]]:
    """List available instrument types."""
    search_paths = [
        get_project_root() / "instruments",
        Path(__file__).parent.parent / "instruments" / "library",
    ]

    types = []
    seen_types = set()

    for library_dir in search_paths:
        if not library_dir.exists():
            continue

        for yaml_file in sorted(library_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                    if data and "instrument" in data:
                        inst = data["instrument"]
                        inst_type = inst.get("type", yaml_file.stem)

                        if inst_type in seen_types:
                            continue
                        seen_types.add(inst_type)

                        capabilities = data.get("capabilities", [])
                        types.append({
                            "id": inst_type,
                            "name": inst.get("name", yaml_file.stem),
                            "capabilities": [c.get("name", "") for c in capabilities],
                        })
            except Exception:
                continue

    return types


def _list_runs() -> list[dict[str, Any]]:
    """List recent test runs."""
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root() / "results")
    backend = ParquetBackend(results_dir=results_dir)
    return backend.list_runs(limit=50)


# =============================================================================
# Get entity
# =============================================================================


def _get_entity(entity_type: str, id: str) -> dict[str, Any]:
    """Get full details of an entity."""
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown type '{entity_type}'. Valid: {ENTITY_TYPES}"}

    if entity_type == "station":
        return _get_station(id)
    elif entity_type == "product":
        return _get_product(id)
    elif entity_type == "fixture":
        return _get_fixture(id)
    elif entity_type == "sequence":
        return _get_sequence(id)
    elif entity_type == "instrument":
        return _get_instrument(id)
    elif entity_type == "run":
        return _get_run(id)

    return {"error": "Not implemented"}


def _get_station(station_id: str) -> dict[str, Any]:
    """Get station configuration."""
    yaml_file = get_project_root() / "stations" / f"{station_id}.yaml"

    if not yaml_file.exists():
        return {"error": f"Station '{station_id}' not found"}

    try:
        with open(yaml_file) as f:
            return yaml.safe_load(f)
    except Exception as e:
        return {"error": f"Failed to load station: {e}"}


def _get_product(product_id: str) -> dict[str, Any]:
    """Get product specification from products/{product_id}/spec.yaml."""
    spec_file = get_project_root() / "products" / product_id / "spec.yaml"

    if not spec_file.exists():
        return {"error": f"Product '{product_id}' not found in products/"}

    try:
        with open(spec_file) as f:
            return yaml.safe_load(f)
    except Exception as e:
        return {"error": f"Failed to load product: {e}"}


def _get_fixture(fixture_id: str) -> dict[str, Any]:
    """Get fixture configuration."""
    yaml_file = get_project_root() / "fixtures" / f"{fixture_id}.yaml"
    if yaml_file.exists():
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            if data:
                return {
                    "fixture": data.get("fixture", {}),
                    "points": data.get("points", {}),
                }

    return {"error": f"Fixture '{fixture_id}' not found"}


def _get_sequence(sequence_id: str) -> dict[str, Any]:
    """Get test sequence."""
    yaml_file = get_project_root() / "sequences" / f"{sequence_id}.yaml"
    if yaml_file.exists():
        with open(yaml_file) as f:
            return yaml.safe_load(f)

    return {"error": f"Sequence '{sequence_id}' not found"}


def _get_instrument(instrument_type: str) -> dict[str, Any]:
    """Get instrument library definition."""
    search_paths = [
        get_project_root() / "instruments",
        Path(__file__).parent.parent / "instruments" / "library",
    ]

    for library_path in search_paths:
        yaml_file = library_path / f"{instrument_type}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file) as f:
                    return yaml.safe_load(f)
            except Exception:
                continue

    return {"error": f"Instrument type '{instrument_type}' not found"}


def _get_run(run_id: str) -> dict[str, Any]:
    """Get test run details."""
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = str(get_project_root() / "results")
    backend = ParquetBackend(results_dir=results_dir)
    run = backend.get_run(run_id)

    if not run:
        return {"error": f"Run '{run_id}' not found"}

    run["measurements"] = backend.get_measurements(run_id)
    return run


# =============================================================================
# Save entity
# =============================================================================


def _save_entity(entity_type: str, id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Validate and save an entity."""
    valid_types = ["station", "product", "fixture", "sequence", "instrument", "test"]
    if entity_type not in valid_types:
        return {"error": f"Unknown type '{entity_type}'. Valid: {valid_types}"}

    if entity_type == "station":
        return _save_station(id, content)
    elif entity_type == "product":
        return _save_product(id, content)
    elif entity_type == "fixture":
        return _save_fixture(id, content)
    elif entity_type == "sequence":
        return _save_sequence(id, content)
    elif entity_type == "instrument":
        return _save_instrument(id, content)
    elif entity_type == "test":
        return _save_test(id, content)

    return {"error": "Not implemented"}


def _save_station(station_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save station configuration."""
    stations_dir = get_project_root() / "stations"
    stations_dir.mkdir(parents=True, exist_ok=True)

    filepath = stations_dir / f"{station_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_product(product_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save product specification to products/{product_id}/spec.yaml."""
    errors = []

    if "product" not in content:
        errors.append("Missing 'product' section")
    else:
        if "id" not in content["product"]:
            errors.append("product.id is required")
        if "name" not in content["product"]:
            errors.append("product.name is required")

    if errors:
        return {"success": False, "errors": errors}

    # Save to products/{product_id}/spec.yaml
    product_dir = get_project_root() / "products" / product_id
    product_dir.mkdir(parents=True, exist_ok=True)

    filepath = product_dir / "spec.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_fixture(fixture_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save fixture configuration."""
    fixtures_dir = get_project_root() / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    filepath = fixtures_dir / f"{fixture_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_sequence(sequence_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save test sequence."""
    sequences_dir = get_project_root() / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    filepath = sequences_dir / f"{sequence_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_instrument(instrument_type: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save instrument library definition."""
    instruments_dir = get_project_root() / "instruments"
    instruments_dir.mkdir(parents=True, exist_ok=True)

    filepath = instruments_dir / f"{instrument_type}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_test(path: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save a Python test file."""
    if "code" not in content:
        return {"success": False, "errors": ["content.code is required"]}

    # Support both absolute-ish paths and relative paths
    if path.startswith("products/") or path.startswith("tests/"):
        filepath = get_project_root() / path
    else:
        filepath = get_project_root() / "tests" / path

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

"""Tests for {product_name}."""

from litmus.execution import litmus_test


@litmus_test
def test_output_voltage(vector, psu, dmm):
    """Measure output voltage. Limits in config.yaml."""
    psu.set_voltage(vector.get("vin", 12.0))
    psu.enable_output()
    return dmm.measure_dc_voltage()


@litmus_test
def test_load_regulation(vector, psu, dmm, eload):
    """Output voltage under load. Vectors/limits in config.yaml."""
    psu.set_voltage(vector.get("vin", 12.0))
    psu.enable_output()
    eload.set_current(vector["load_current"])
    eload.enable()
    vout = dmm.measure_dc_voltage()
    eload.disable()
    return vout


================================================================================
FILE 2: tests/config.yaml  (REQUIRED! Limits MUST be here, not in code)
================================================================================

# Limits for each test function - MUST match function names exactly

test_output_voltage:
  limits:
    test_output_voltage:
      low: 4.75       # From spec: nominal - tolerance
      high: 5.25      # From spec: nominal + tolerance
      nominal: 5.0    # From spec.test_conditions.default_vout
      units: V
      spec_ref: "output_voltage @ no load"

test_load_regulation:
  vectors:
    expand: product
    load_current: [0.5, 1.0, 2.0, 3.0]  # From spec.specs.continuous_output_current
  limits:
    test_load_regulation:
      low: 4.7
      high: 5.3
      nominal: 5.0
      units: V
      spec_ref: "output_voltage @ load"


================================================================================
CRITICAL: You MUST create BOTH files. The test file alone will NOT work.
================================================================================

The @litmus_test decorator auto-discovers config.yaml in the same directory.
Without config.yaml, there are NO LIMITS and tests will fail or be meaningless.

Values in config.yaml come from the product spec.yaml:
- nominal: spec.test_conditions.default_vout
- low/high: Calculate from spec tolerance (e.g., 5.0V ± 5% = 4.75 to 5.25)
- vectors: From spec ranges (e.g., load_current up to spec.specs.continuous_output_current.max)
'''

INSTRUMENT_TEMPLATE = '''"""{instrument_name} driver.

Supports both real hardware (VISA) and simulation (simulate=True).
"""

from decimal import Decimal
from typing import Any

from litmus.instruments.visa import VisaInstrument


class {class_name}(VisaInstrument):
    """{instrument_name} driver."""

    def __init__(
        self,
        resource: str,
        simulate: bool = False,
        sim_config: dict[str, Any] | None = None,
    ):
        super().__init__(resource=resource, simulate=simulate, sim_config=sim_config)

    # Implement capability methods here
    # def measure_voltage(self) -> Decimal:
    #     return Decimal(self.query("MEAS:VOLT:DC?"))
'''

INSTRUMENT_YAML_TEMPLATE = '''instrument:
  type: {instrument_type}
  name: {instrument_name}
  description: {description}

capabilities:
  - name: voltage_dc
    direction: input
    domain: voltage
    signal_types: [dc]

scpi_commands:
  identify: "*IDN?"
  reset: "*RST"
'''

CAPABILITY_INTERFACES = """
Available capability interfaces:

MEASUREMENT (direction: input):
  VoltageInput      - measure_voltage()
  CurrentInput      - measure_current()
  ResistanceInput   - measure_resistance()
  FrequencyInput    - measure_frequency()
  WaveformInput     - fetch_waveform()

STIMULUS (direction: output):
  VoltageOutput     - set_voltage(), enable_output()
  CurrentOutput     - set_current(), enable_output()
  WaveformOutput    - configure_waveform()

ELECTRONIC LOAD:
  ConstantCurrentLoad     - set_load_current(), enable_load()
  ConstantPowerLoad       - set_load_power()
  ConstantResistanceLoad  - set_load_resistance()
"""


def _read_file(path: str) -> dict[str, Any]:
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
    root = get_project_root()
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


def discover_tool() -> dict[str, Any]:
    """Scan for connected VISA instruments."""
    try:
        import pyvisa

        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()

        discovered = []
        for resource in resources:
            info = {
                "address": resource,
                "type": _classify_visa_resource(resource),
                "idn": None,
            }

            try:
                inst = rm.open_resource(resource)
                inst.timeout = 2000
                idn = inst.query("*IDN?").strip()
                inst.close()
                info["idn"] = idn
                info["suggested_type"] = _suggest_instrument_type(idn)
            except Exception:
                pass

            discovered.append(info)

        rm.close()

        return {
            "success": True,
            "count": len(discovered),
            "resources": discovered,
        }

    except ImportError:
        return {"error": "PyVISA not installed"}
    except Exception as e:
        return {"error": f"Discovery failed: {e}"}


def _classify_visa_resource(resource: str) -> str:
    """Classify a VISA resource string by connection type."""
    resource_upper = resource.upper()
    if resource_upper.startswith("TCPIP"):
        return "tcp"
    elif resource_upper.startswith("USB"):
        return "usb"
    elif resource_upper.startswith("GPIB"):
        return "gpib"
    elif resource_upper.startswith("ASRL"):
        return "serial"
    return "unknown"


def _suggest_instrument_type(idn: str) -> str | None:
    """Suggest an instrument type based on *IDN? response."""
    idn_lower = idn.lower()

    if any(x in idn_lower for x in ["34401", "34461", "dmm", "multimeter"]):
        return "dmm"
    if any(x in idn_lower for x in ["e36", "n67", "power supply", "psu"]):
        return "psu"
    if any(x in idn_lower for x in ["dso", "mso", "scope", "oscilloscope"]):
        return "scope"
    if any(x in idn_lower for x in ["load", "n33", "el3"]):
        return "eload"

    return None


# =============================================================================
# Tool 3: match
# =============================================================================


def match_tool(
    product_id: str | None = None,
    station_id: str | None = None,
    fixture_id: str | None = None,
) -> dict[str, Any]:
    """Check compatibility between products, stations, and fixtures."""
    from litmus.matching.service import (
        check_station_compatibility,
        find_compatible_stations,
        get_required_capabilities,
        load_product_by_id,
    )

    # Just product_id: find compatible stations
    if product_id and not station_id and not fixture_id:
        product = load_product_by_id(product_id)
        if not product:
            return {"error": f"Product '{product_id}' not found"}

        requirements = get_required_capabilities(product)
        req_list = [
            {
                "characteristic": req.characteristic_name,
                "direction": req.direction.value,
                "domain": req.domain.value,
            }
            for req in requirements
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
        result = check_station_compatibility(product_id, station_id)
        if not result:
            return {"error": f"Product or station not found"}
        return result

    # Fixture: find stations with required instruments
    if fixture_id:
        fixture_result = _get_fixture(fixture_id)
        if "error" in fixture_result:
            return fixture_result

        points = fixture_result.get("points", {})
        required_instruments = set()
        for point in points.values():
            if point.get("instrument"):
                required_instruments.add(point["instrument"])

        stations = _list_stations()
        compatible = []

        for station in stations:
            sid = station.get("id")
            station_config = _get_station(sid)

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


def run_tool(test: str, station: str, serial: str) -> dict[str, Any]:
    """Execute tests and return results."""
    import subprocess
    import sys
    from datetime import datetime

    # Determine test target
    root = get_project_root()
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
            return {"error": f"Test not found for: {test}", "searched": [str(p) for p in possible_paths]}

    # Build pytest command
    pytest_path = Path(sys.executable).parent / "pytest"
    cmd = [
        str(pytest_path),
        *test_targets,
        f"--dut-serial={serial}",
        f"--station={station}",
        "--results-dir=results",
        "-v", "--tb=short",
        "--simulate",
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
