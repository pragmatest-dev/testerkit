"""Project scaffolding for litmus init.

Shared between CLI and MCP tool for consistent project initialization.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any


def _sanitize_name(name: str) -> str:
    return name.replace("-", "_").replace(" ", "_")


def _resolve_project_name(path: Path) -> str:
    """Resolve project name: git remote leaf → git repo root → folder name."""
    from litmus.execution._git import _git_repo_root, _remote_leaf_name, get_git_remote

    remote = get_git_remote(path)
    if remote:
        leaf = _remote_leaf_name(remote)
        if leaf:
            return _sanitize_name(leaf)

    root = _git_repo_root(path)
    if root:
        return _sanitize_name(root.name)

    return _sanitize_name(path.name)


def check_command(cmd: str) -> bool:
    """Check if a command is available on the system."""
    return shutil.which(cmd) is not None


TIER_CHOICES = ("bringup", "bench", "factory")


def init_project(
    path: Path,
    git: bool = True,
    station: dict[str, Any] | None = None,
    starter: bool = False,
    name: str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """Initialize a new Litmus project.

    Args:
        path: Directory path to initialize (must already exist).
        git: Whether to initialize a git repository.
        station: Optional station data to write.  Dict with
            ``instruments`` mapping role names to dicts with
            ``resource`` and optional ``info`` keys.
        starter: Whether to create starter example files.
            Equivalent to ``tier="bench"``.
        name: Explicit project name (from CLI arg). If None, resolves
            via git remote leaf → git root folder → directory name.
        tier: Scaffold tier (``"bringup"``, ``"bench"``, or
            ``"factory"``). ``"bringup"`` creates a Tier 0/1 scaffold
            (MagicMock fixtures in conftest, one test, one sidecar — no
            station/product/fixture YAML). ``"bench"`` is equivalent to
            ``starter=True``. ``"factory"`` is the bench scaffold plus
            production/characterization profile skeletons.

    Returns:
        Dict with created_dirs, created_files, warnings, and git_initialized.
    """
    if tier is not None and tier not in TIER_CHOICES:
        raise ValueError(f"tier must be one of {TIER_CHOICES}, got {tier!r}")
    if tier == "bench":
        starter = True
    created_dirs: list[str] = []
    created_files: list[str] = []
    warnings: list[str] = []

    # Create directories. Bringup tier skips station/product/fixture —
    # those layers are off until the user graduates to Tier 2.
    if tier == "bringup":
        subdirs = ["tests", "reports"]
    else:
        subdirs = [
            "products",
            "stations",
            "fixtures",
            "instruments",
            "tests",
            "reports",
        ]
    for subdir in subdirs:
        dir_path = path / subdir
        if not dir_path.exists():
            dir_path.mkdir()
            created_dirs.append(subdir)

    project_name = _sanitize_name(name) if name else _resolve_project_name(path)

    # Create pyproject.toml
    pyproject_path = path / "pyproject.toml"
    if not pyproject_path.exists():
        if tier == "bringup":
            pytest_section = """[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
filterwarnings = ["ignore::pytest.PytestReturnNotNoneWarning"]
"""
        elif starter:
            # Starter mode: include pytest defaults so users can just run "pytest"
            addopts = "-v --station=starter_station --mock-instruments --dut-serial=STARTER001"
            pytest_section = f'''[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "{addopts}"
filterwarnings = ["ignore::pytest.PytestReturnNotNoneWarning"]
'''
        else:
            pytest_section = """[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
filterwarnings = ["ignore::pytest.PytestReturnNotNoneWarning"]
"""
        pyproject_content = f'''[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "litmus-test>=0.1.0",
    "pytest>=8.0",
]

{pytest_section}
[tool.uv.sources]
# Override with a local editable install during development:
# litmus-test = {{ path = "../litmus", editable = true }}
'''
        pyproject_path.write_text(pyproject_content)
        created_files.append("pyproject.toml")

    # Create conftest.py
    conftest_path = path / "tests" / "conftest.py"
    if not conftest_path.exists():
        if tier == "bringup":
            conftest_content = '''"""Bench-bringup conftest — instrument fixtures defined directly.

Tier 0/1 escape hatch: no station / catalog / product YAML needed.
Swap ``MagicMock`` for a real driver (PyVISA / PyMeasure / vendor lib)
when you\'re ready for the bench. Graduate to Tier 2 by moving driver
resolution into a ``stations/<id>.yaml`` and deleting these fixtures —
test bodies don\'t change.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def dmm() -> MagicMock:
    """Bench DMM. Replace MagicMock with a real driver."""
    inst = MagicMock()
    inst.measure_dc_voltage.return_value = 3.3
    return inst


@pytest.fixture
def psu() -> MagicMock:
    """Bench PSU. Replace MagicMock with a real driver."""
    inst = MagicMock()
    inst.measure_voltage.return_value = 5.0
    inst.measure_current.return_value = 0.1
    return inst
'''
        elif starter:
            conftest_content = '''"""pytest configuration for Litmus tests.

Instrument fixtures (psu, dmm) are AUTO-REGISTERED from station config.
No boilerplate needed - just use them in your tests.

To OVERRIDE an auto-registered fixture with custom setup/teardown:

    @pytest.fixture(scope="session")
    def psu(instruments):
        inst = instruments.get("psu")
        inst.set_voltage(5.0)       # custom default
        yield inst
        inst.disable_output()       # custom teardown

For PIN-BASED fixtures with traceability (measurement -> pin -> instrument):

    @pytest.fixture(scope="session")
    def output_dmm(pins):
        return pins.get("TP_VOUT")  # measurement includes dut_pin
"""
'''
        else:
            conftest_content = '''"""Pytest configuration for Litmus tests.

The litmus pytest plugin auto-registers fixtures for each instrument role
defined in your station config. For example, if your station has:

    instruments:
      dmm: keithley_2000
      psu: keysight_e36313a

Then `dmm` and `psu` fixtures are automatically available in your tests.
No manual fixture definitions needed here.

Run with --mock-instruments for hardware-free testing:

    pytest tests/ --mock-instruments --dut-serial=TEST001
"""

# Add project-specific fixtures below if needed.
# Do NOT define fixtures for instrument roles (dmm, psu, etc.) —
# they are auto-registered by the litmus plugin from station config.
'''
        conftest_path.write_text(conftest_content)
        created_files.append("tests/conftest.py")

    # Create litmus.yaml
    litmus_yaml_path = path / "litmus.yaml"
    if not litmus_yaml_path.exists():
        from litmus.models.project import ProjectConfig
        from litmus.store import dump_yaml

        proj_data: dict[str, Any] = {"name": project_name}
        if starter:
            # Starter projects write to a project-local ``data/`` dir so
            # learning runs (mock instruments, example_product, etc.) don't
            # pollute the global platformdirs store users will share across
            # projects on this machine. When the user is ready, ``litmus
            # data promote`` migrates non-starter runs into the global
            # store and removes this override.
            proj_data.update(
                {
                    "data_dir": "data",
                    "default_station": "starter_station",
                    "default_fixture": "example_fixture",
                    "mock_instruments": True,
                }
            )
        proj = ProjectConfig(**proj_data)
        litmus_yaml_path.write_text(dump_yaml(proj.model_dump()))
        created_files.append("litmus.yaml")

    # Create .gitignore
    gitignore_path = path / ".gitignore"
    if not gitignore_path.exists():
        gitignore_content = """# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/

# Litmus — local outputs (kept out of version control)
#   data/    runtime parquet + event log + channels (starter projects only;
#            after `litmus data promote` this dir is no longer used)
#   reports/ generated HTML / PDF / CSV / JSON reports
data/
reports/

# Source-of-truth Litmus config IS in git on purpose:
#   stations/, products/, fixtures/, tests/, instruments/, profiles/
# Do NOT add those above — losing them silently is exactly the bug
# this template used to ship.

# IDE
.idea/

# uv
.python-version
uv.lock
"""
        gitignore_path.write_text(gitignore_content)
        created_files.append(".gitignore")

    # Create README.md for the new project
    readme_path = path / "README.md"
    if not readme_path.exists():
        readme_content = f"""# {project_name}

A [Litmus](https://github.com/pragmatest-dev/litmus) hardware test project.

## Project Structure

| Folder | Contents |
|--------|----------|
| `products/` | Product specifications (YAML) |
| `stations/` | Station configurations (YAML) |
| `fixtures/` | Test fixture definitions (YAML) |
| `tests/` | Test code (Python) |
| `instruments/` | Custom instrument definitions (YAML) |
| `data/` | Local test output (starter only; gitignored). See `litmus data promote`. |
| `reports/` | Generated HTML / PDF / CSV / JSON reports (gitignored) |
"""
        readme_path.write_text(readme_content)
        created_files.append("README.md")

    # Create .vscode/ with YAML schema validation. Generates one
    # JSON schema per Litmus config type (sidecar, profile, project,
    # station, fixture, product, catalog, instrument_asset) from the
    # backing Pydantic models, plus a settings.json that wires each
    # YAML glob (tests/**, profiles/**, etc.) to the right schema —
    # users get autocomplete + inline validation in VS Code via the
    # Red Hat YAML extension as soon as they open the project.
    vscode_dir = path / ".vscode"
    settings_path = vscode_dir / "settings.json"
    if not settings_path.exists():
        import json

        from litmus.schema_export import export_schemas, vscode_yaml_schemas

        schemas_dir = vscode_dir / "schemas"
        try:
            schema_paths = export_schemas(schemas_dir)
            settings = {"yaml.schemas": vscode_yaml_schemas()}
            vscode_dir.mkdir(exist_ok=True)
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            created_files.append(".vscode/settings.json")
            for sp in schema_paths:
                created_files.append(str(sp.relative_to(path)))
        except (ImportError, OSError) as exc:
            warnings.append(f"Failed to generate VS Code YAML schemas: {exc}")

    # Write station file if instruments were discovered
    if station and station.get("instruments"):
        from litmus.models.station import StationConfig, StationInstrumentConfig
        from litmus.store import dump_yaml

        stations_dir = path / "stations"
        stations_dir.mkdir(exist_ok=True)
        station_file = stations_dir / "station.yaml"
        if not station_file.exists():
            instruments = {
                role: StationInstrumentConfig(
                    type=role,
                    resource=data.get("resource"),
                )
                for role, data in station["instruments"].items()
            }
            sc = StationConfig(
                id="station",
                name="Default Station",
                instruments=instruments,
            )
            station_file.write_text(dump_yaml(sc.model_dump(exclude_none=True)))
            created_files.append("stations/station.yaml")

    # Create starter files if requested
    if tier == "bringup":
        created_files.extend(_create_bringup_files(path))
    elif starter:
        starter_files = _create_starter_files(path)
        created_files.extend(starter_files)

    # Initialize git repository (skip if already in a repo)
    git_initialized = False
    if git and not (path / ".git").exists():
        if check_command("git"):
            try:
                subprocess.run(
                    ["git", "init"],
                    cwd=str(path),
                    capture_output=True,
                    check=True,
                )
                git_initialized = True
            except subprocess.CalledProcessError:
                warnings.append("Failed to initialize git repository")
        else:
            warnings.append("git not found, skipping repository initialization")

    return {
        "created_dirs": created_dirs,
        "created_files": created_files,
        "warnings": warnings,
        "git_initialized": git_initialized,
    }


def get_project_contents(path: Path) -> list[dict[str, str]]:
    """List the contents of a project directory."""
    contents = []
    for item in sorted(path.iterdir()):
        if not item.name.startswith(".") or item.name == ".gitignore":
            contents.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                }
            )
    return contents


def _create_starter_files(path: Path) -> list[str]:
    """Create starter example files for a new project.

    Args:
        path: Project root directory.

    Returns:
        List of created file paths (relative to project root).
    """
    from litmus.store import dump_yaml

    created_files: list[str] = []

    # Create stations/starter_station.yaml
    station_file = path / "stations" / "starter_station.yaml"
    if not station_file.exists():
        station_content = {
            "id": "starter_station",
            "name": "Starter Station",
            "description": "Auto-generated starter station with mock instruments",
            "instruments": {
                "psu": {
                    "type": "psu",
                    "resource": "TCPIP::192.168.1.100::INSTR",
                    "mock": True,
                    "mock_config": {
                        "set_voltage": None,
                        "enable_output": None,
                        "measure_voltage": 5.0,
                        "measure_current": 0.25,
                    },
                },
                "dmm": {
                    "type": "dmm",
                    "resource": "TCPIP::192.168.1.101::INSTR",
                    "mock": True,
                    "mock_config": {
                        "measure_dc_voltage": 3.3,
                    },
                },
            },
        }
        comment_header = (
            "# Starter station — mock instruments for getting started.\n"
            "#\n"
            "# To connect real instruments:\n"
            "#   1. litmus discover           — find instruments on your bench\n"
            "#   2. litmus station init       — create a real station config\n"
            "#   3. Keep mock_config sections — they're used by --mock-instruments for CI\n"
            "#\n"
            "# See: docs/tutorial/from-mocks-to-hardware.md\n\n"
        )
        station_file.write_text(comment_header + dump_yaml(station_content))
        created_files.append("stations/starter_station.yaml")

    # Create products/example_product.yaml
    product_file = path / "products" / "example_product.yaml"
    if not product_file.exists():
        product_content = {
            "id": "example_product",
            "name": "Example Product",
            "description": "Auto-generated example product specification",
            "pins": {
                "TP_VOUT": {
                    "name": "TP1",
                    "net": "VOUT_3V3",
                    "description": "Output voltage test point",
                },
            },
            "characteristics": {
                "output_voltage": {
                    "function": "dc_voltage",
                    "direction": "output",
                    "units": "V",
                    "pin": "TP_VOUT",
                    "bands": [
                        {
                            "value": 3.3,
                            "accuracy": {
                                "pct_reading": 2.0,
                            },
                        },
                    ],
                },
            },
        }
        product_file.write_text(dump_yaml(product_content))
        created_files.append("products/example_product.yaml")

    # Create fixtures/example_fixture.yaml
    fixture_file = path / "fixtures" / "example_fixture.yaml"
    if not fixture_file.exists():
        fixture_content = {
            "id": "example_fixture",
            "name": "Example Fixture",
            "description": "Maps DUT pins to station instrument channels",
            "product_id": "example_product",
            "connections": {
                "vout_measure": {
                    "name": "vout_measure",
                    "dut_pin": "TP_VOUT",
                    "instrument": "dmm",
                    "instrument_channel": "ch1",
                    "instrument_terminal": "hi",
                    "function": "dc_voltage",
                    "description": "Output voltage at TP_VOUT",
                },
            },
        }
        comment_header = (
            "# Example fixture — wires DUT pins through station instrument\n"
            "# channels. Each connection has a name, a dut_pin (matches a\n"
            "# pin in the active product YAML), and an instrument role\n"
            "# (matches a key in the active station's instruments: dict).\n"
            "#\n"
            "# Tests iterate connections via ``ctx.connections`` and call\n"
            "# the matching instrument fixture (`dmm`, `psu`, …) with the\n"
            "# resolved channel.\n\n"
        )
        fixture_file.write_text(comment_header + dump_yaml(fixture_content))
        created_files.append("fixtures/example_fixture.yaml")

    # Create instruments/ asset files
    for role, instrument_id in [
        ("psu", "generic_psu_001"),
        ("dmm", "generic_dmm_001"),
    ]:
        inst_file = path / "instruments" / f"{instrument_id}.yaml"
        if not inst_file.exists():
            inst_content = {
                "id": instrument_id,
                "protocol": "visa",
                "driver": "litmus.instruments.visa.VisaInstrument",
                "resource": f"TCPIP::192.168.1.{100 if role == 'psu' else 101}::INSTR",
                "info": {
                    "manufacturer": "Generic",
                    "model": role.upper(),
                    "serial": f"SIM-{role.upper()}-001",
                },
                "calibration": {
                    "due_date": "2027-01-01",
                    "last_calibrated": "2026-01-01",
                    "certificate": f"CAL-{role.upper()}-2026-001",
                    "lab": "In-house",
                },
            }
            inst_file.write_text(dump_yaml(inst_content))
            created_files.append(f"instruments/{instrument_id}.yaml")

    # Create tests/test_example.py
    test_file = path / "tests" / "test_example.py"
    if not test_file.exists():
        test_content = '''"""Example test demonstrating Litmus basics.

The test code focuses on WHAT to do. The vector sweep and limit live
in the sidecar ``test_example.yaml`` next to this file — change them
without touching code.

Run with: pytest
(All defaults configured in pyproject.toml)

Instrument fixtures (psu, dmm) are auto-registered from station config.
The ``context`` and ``verify`` fixtures are provided by the Litmus
pytest plugin.
"""


def test_output_voltage(context, psu, dmm, verify) -> None:
    """Verify output voltage is within spec."""
    vin = context.get_param("vin", 5.0)
    psu.set_voltage(vin)
    psu.enable_output()
    verify("output_voltage", float(dmm.measure_dc_voltage()))
'''
        test_file.write_text(test_content)
        created_files.append("tests/test_example.py")

    # Create tests/test_example.yaml (sidecar referenced from the test docstring)
    sidecar_file = path / "tests" / "test_example.yaml"
    if not sidecar_file.exists():
        sidecar_text = (
            "# Sidecar for tests/test_example.py.\n"
            "#\n"
            "# Each top-level key (``limits``, ``sweeps``, …) is a Litmus\n"
            "# marker applied to every test in this module. Per-test\n"
            "# overrides go under the ``tests:`` tree.\n"
            "#\n"
            "# Graduate to spec-driven limits by replacing low/high/units\n"
            "# with ``characteristic: output_voltage, tolerance_pct: 2``\n"
            "# — the resolver reads the band from\n"
            "# products/example_product.yaml. Doing so also auto-derives\n"
            "# fixture connections, so the test body must then iterate\n"
            "# ``ctx.connections`` instead of calling the dmm directly.\n"
            "\n"
            "tests:\n"
            "  test_output_voltage:\n"
            "    limits:\n"
            "      output_voltage:\n"
            "        low: 3.234\n"
            "        high: 3.366\n"
            "        units: V\n"
        )
        sidecar_file.write_text(sidecar_text)
        created_files.append("tests/test_example.yaml")

    return created_files


def _create_bringup_files(path: Path) -> list[str]:
    """Create Tier 0/1 bringup scaffold: one test, one sidecar, no YAML layers.

    Matches ``examples/01-bringup/`` shape. The conftest (mock instrument
    fixtures) is written by the main ``init_project`` flow; this
    function only adds the test + sidecar.
    """
    created_files: list[str] = []

    test_file = path / "tests" / "test_smoke.py"
    if not test_file.exists():
        test_file.write_text('''"""Tier 0/1 smoke tests for a brand-new board.

Bringup scaffold: no station / product / fixture YAML. Limits live
inline or in a same-named sidecar (``test_smoke.yaml``). When you
graduate to Tier 2 (add a station + product), the test bodies here
are unchanged — you just swap the sidecar shape.

Run::

    pytest -v
"""

from __future__ import annotations

from litmus.models.test_config import Limit



def test_rail_inline(dmm, verify) -> None:
    """No YAML. Limit lives in the test source."""
    verify(
        "v_rail",
        float(dmm.measure_dc_voltage()),
        limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    )


def test_rail_sidecar(dmm, verify) -> None:
    """Same measurement, limit now lives in ``test_smoke.yaml``."""
    verify("v_rail_sidecar", float(dmm.measure_dc_voltage()))


def test_current_draw(psu, verify) -> None:
    """A second measurement sharing the same sidecar."""
    verify("i_in", float(psu.measure_current()))
''')
        created_files.append("tests/test_smoke.py")

    sidecar = path / "tests" / "test_smoke.yaml"
    if not sidecar.exists():
        sidecar.write_text(
            "# Tier 1 sidecar — absolute bounds only. No product, no characteristic.\n"
            "# Graduate to Tier 2 by swapping ``low/high`` for\n"
            "# ``characteristic: <id>`` + ``tolerance_pct: N``.\n"
            "limits:\n"
            "  v_rail_sidecar:\n"
            "    low: 3.2\n"
            "    high: 3.4\n"
            "    nominal: 3.3\n"
            "    units: V\n"
            "  i_in:\n"
            "    low: 0.0\n"
            "    high: 0.5\n"
            "    units: A\n"
        )
        created_files.append("tests/test_smoke.yaml")

    return created_files
