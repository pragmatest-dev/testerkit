"""Project scaffolding for litmus init.

Shared between CLI and MCP tool for consistent project initialization.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any


def check_command(cmd: str) -> bool:
    """Check if a command is available on the system."""
    return shutil.which(cmd) is not None


def init_project(
    path: Path,
    git: bool = True,
) -> dict[str, Any]:
    """Initialize a new Litmus project.

    Args:
        path: Directory path to initialize (must already exist).
        git: Whether to initialize a git repository.

    Returns:
        Dict with created_dirs, created_files, warnings, and git_initialized.
    """
    created_dirs: list[str] = []
    created_files: list[str] = []
    warnings: list[str] = []

    # Create directories
    subdirs = [
        "products", "stations", "sequences", "fixtures",
        "instruments", "tests", "results", "reports",
    ]
    for subdir in subdirs:
        dir_path = path / subdir
        if not dir_path.exists():
            dir_path.mkdir()
            created_dirs.append(subdir)

    project_name = path.name.replace("-", "_").replace(" ", "_")

    # Create pyproject.toml
    pyproject_path = path / "pyproject.toml"
    if not pyproject_path.exists():
        pyproject_content = f'''[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    # Install from local path during development:
    # "litmus @ file:///path/to/litmus"
    # Or from git:
    # "litmus @ git+https://github.com/your-org/litmus"
    # Or from PyPI when available:
    # "litmus-hw",
    "pytest>=8.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]

[tool.uv.sources]
# Uncomment and adjust path for local development:
# litmus = {{ path = "../litmus", editable = true }}
'''
        pyproject_path.write_text(pyproject_content)
        created_files.append("pyproject.toml")

    # Create conftest.py
    conftest_path = path / "conftest.py"
    if not conftest_path.exists():
        conftest_content = '''"""Pytest configuration for Litmus tests.

Instruments come from station config via the `instruments` fixture.
Run with --mock-instruments for hardware-free testing.

Example:
    pytest tests/ --station=test_bench --mock-instruments --dut-serial=TEST001
"""

import pytest


@pytest.fixture
def dmm(instruments):
    """DMM from station config."""
    return instruments.get("dmm")


@pytest.fixture
def psu(instruments):
    """PSU from station config."""
    return instruments.get("psu")


@pytest.fixture
def eload(instruments):
    """Electronic load from station config."""
    return instruments.get("eload")
'''
        conftest_path.write_text(conftest_content)
        created_files.append("conftest.py")

    # Create litmus.yaml
    litmus_yaml_path = path / "litmus.yaml"
    if not litmus_yaml_path.exists():
        litmus_yaml_content = f'''# Litmus project configuration
project:
  name: "{project_name}"

results_dir: results

reports:
  auto: false          # Auto-generate reports after each test run
  format: html         # Default format: html, pdf, json, csv
  template: default    # Jinja2 template name
  output_dir: reports  # Where to save generated reports
'''
        litmus_yaml_path.write_text(litmus_yaml_content)
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

# Litmus
results/
reports/

# IDE
.idea/
.vscode/

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

A Litmus hardware test project.

## Getting Started

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Define your test station in `stations/`:
   ```yaml
   # stations/my_station.yaml
   station:
     id: my_station
     name: My Test Station

   instruments:
     dmm:
       type: dmm
       resource: TCPIP::192.168.1.100::INSTR
       mock_config:
         voltage: 5.0
     psu:
       type: psu
       resource: TCPIP::192.168.1.101::INSTR
       mock_config:
         voltage: 12.0
         current: 0.1
   ```

3. Create a product spec in `products/`:
   ```yaml
   # products/my_product/spec.yaml
   product:
     id: my_product
     name: My Product
     revision: "1.0"

   specs:
     output_voltage:
       nominal: 5.0
       tolerance_pct: 5
       units: V
   ```

4. Write your first test in `tests/`:
   ```python
   # tests/test_basic.py
   from litmus.execution import litmus_test

   @litmus_test
   def test_voltage(psu, dmm):
       psu.set_voltage(5.0)
       psu.enable_output()
       return dmm.measure_dc_voltage()
   ```

5. Create a test config in `tests/config.yaml`:
   ```yaml
   test_voltage:
     _mock:
       dmm.measure_dc_voltage: 5.0
     limits:
       test_voltage:
         low: 4.75
         high: 5.25
         nominal: 5.0
         units: V
   ```

6. Run tests:
   ```bash
   pytest tests/ --station=my_station --mock-instruments --dut-serial=TEST001
   ```

## Project Structure

- `products/` - Product specifications
- `stations/` - Station configurations
- `fixtures/` - Test fixture definitions
- `sequences/` - Test sequences
- `tests/` - Test code
- `results/` - Test output (gitignored)
- `instruments/` - Custom instrument definitions

## Documentation

See [Litmus Documentation](https://github.com/your-org/litmus) for full details.
"""
        readme_path.write_text(readme_content)
        created_files.append("README.md")

    # Initialize git repository
    git_initialized = False
    if git:
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
