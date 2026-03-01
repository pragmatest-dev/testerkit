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
    station: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Initialize a new Litmus project.

    Args:
        path: Directory path to initialize (must already exist).
        git: Whether to initialize a git repository.
        station: Optional station data to write.  Dict with
            ``instruments`` mapping role names to dicts with
            ``resource`` and optional ``info`` keys.

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
    conftest_path = path / "tests" / "conftest.py"
    if not conftest_path.exists():
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
        from litmus.config.fmt import dump_yaml
        from litmus.schemas import ProjectConfig

        proj = ProjectConfig(name=project_name)
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

# Litmus
results/
reports/
stations/

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

A [Litmus](https://github.com/anthropics/litmus) hardware test project.

## Project Structure

| Folder | Contents |
|--------|----------|
| `products/` | Product specifications (YAML) |
| `stations/` | Station configurations (YAML) |
| `fixtures/` | Test fixture definitions (YAML) |
| `sequences/` | Test sequences (YAML) |
| `tests/` | Test code (Python) |
| `instruments/` | Custom instrument definitions (YAML) |
| `results/` | Test output (gitignored) |
"""
        readme_path.write_text(readme_content)
        created_files.append("README.md")

    # Create .vscode/ with YAML schema validation
    vscode_dir = path / ".vscode"
    settings_path = vscode_dir / "settings.json"
    if not settings_path.exists():
        import json

        from litmus.schemas import export_schemas

        schemas_dir = vscode_dir / "schemas"
        try:
            export_schemas(schemas_dir)
            settings = {
                "yaml.schemas": {
                    ".vscode/schemas/product.schema.json": "products/*/spec.yaml",
                    ".vscode/schemas/catalog.schema.json": "catalog/**/*.yaml",
                },
            }
            vscode_dir.mkdir(exist_ok=True)
            settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            created_files.append(".vscode/settings.json")
            created_files.append(".vscode/schemas/product.schema.json")
            created_files.append(".vscode/schemas/catalog.schema.json")
        except Exception:
            warnings.append("Failed to generate VS Code YAML schemas")

    # Write station file if instruments were discovered
    if station and station.get("instruments"):
        from litmus.config.fmt import dump_yaml
        from litmus.schemas import StationConfig

        stations_dir = path / "stations"
        stations_dir.mkdir(exist_ok=True)
        station_file = stations_dir / "station.yaml"
        if not station_file.exists():
            instruments = {}
            for role, data in station["instruments"].items():
                instruments[role] = role
            resources = {
                role: data["resource"] for role, data in station["instruments"].items()
            }

            sc = StationConfig(id="station", name="Default Station")
            sc_data = sc.model_dump(exclude_none=True)
            sc_data["instruments"] = instruments
            sc_data["resources"] = resources
            station_file.write_text(dump_yaml(sc_data))
            created_files.append("stations/station.yaml")

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
