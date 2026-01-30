"""MCP tool implementations - 8 consolidated tools.

These functions implement the actual logic for MCP tools. The tools are
designed to be generic and composable, reducing cognitive load for AI agents.

Tools:
- discover: Scan for VISA instruments
- list: List entities of any type
- get: Get entity details
- save: Create/update entity
- match: Check compatibility
- run: Execute test sequence
- status: Get run status
- open_ui: Get URL to view/edit in browser
"""

from pathlib import Path
from typing import Any

import yaml

# =============================================================================
# Entity type constants
# =============================================================================

ENTITY_TYPES = ["station", "product", "fixture", "sequence", "instrument", "run"]


# =============================================================================
# Tool 1: discover
# =============================================================================


def discover_tool() -> dict[str, Any]:
    """Scan for connected VISA instruments.

    Returns a list of discovered resources with their addresses and
    any identification information available.

    Returns:
        Dict with list of discovered resources and their info.
    """
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

            # Try to query *IDN? for identification
            try:
                inst = rm.open_resource(resource)
                inst.timeout = 2000  # 2 second timeout
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
            "message": f"Found {len(discovered)} VISA resource(s)",
        }

    except ImportError:
        return {
            "success": False,
            "error": "PyVISA not installed. Install with: pip install pyvisa",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to discover resources: {str(e)}",
        }


def _classify_visa_resource(resource: str) -> str:
    """Classify a VISA resource string by connection type."""
    resource_upper = resource.upper()
    if resource_upper.startswith("TCPIP"):
        return "tcp"
    elif resource_upper.startswith("USB"):
        return "usb"
    elif resource_upper.startswith("GPIB"):
        return "gpib"
    elif resource_upper.startswith("ASRL") or "COM" in resource_upper:
        return "serial"
    elif "SIM" in resource_upper:
        return "simulated"
    else:
        return "unknown"


def _suggest_instrument_type(idn: str) -> str | None:
    """Suggest an instrument type based on *IDN? response."""
    idn_lower = idn.lower()

    if any(x in idn_lower for x in ["34401", "34461", "34465", "dmm", "multimeter"]):
        return "dmm"
    if any(x in idn_lower for x in ["e36", "n67", "power supply", "psu", "dp8"]):
        return "psu"
    if any(x in idn_lower for x in ["dso", "mso", "scope", "oscilloscope", "tds", "rtb"]):
        return "scope"
    if any(x in idn_lower for x in ["load", "n33", "el3"]):
        return "eload"

    return None


# =============================================================================
# Tool 2: list
# =============================================================================


def list_tool(entity_type: str) -> list[dict[str, Any]] | dict[str, Any]:
    """List entities of a given type.

    Args:
        entity_type: One of: station, product, fixture, sequence, instrument, run

    Returns:
        List of entities with id, name, and basic info.
    """
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown entity_type '{entity_type}'. Valid: {ENTITY_TYPES}"}

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
    from litmus.matching.service import list_stations
    return list_stations()


def _list_products() -> list[dict[str, Any]]:
    """List all product specifications.

    Searches both legacy specs/ directories and new products/ folder structure.
    """
    from litmus.products.folder import ProductFolder

    products = []
    seen_ids = set()

    # First, check products/ folder structure (new style)
    products_paths = [
        Path.cwd() / "products",
        Path.cwd() / "demo" / "products",
    ]

    for products_dir in products_paths:
        for folder in ProductFolder.list_all(products_dir):
            if folder.product_id in seen_ids:
                continue
            seen_ids.add(folder.product_id)

            product_info = {
                "id": folder.product_id,
                "name": folder.name,
                "description": folder.manifest.description,
                "current_step": folder.current_step.value if folder.current_step else None,
                "completed_steps": [s.value for s in folder.manifest.completed_steps],
            }

            # Try to load spec for more details
            spec = folder.load_spec()
            if spec:
                product_info["revision"] = spec.revision
                product_info["characteristics_count"] = len(spec.characteristics)
                product_info["test_requirements_count"] = len(spec.test_requirements)

            products.append(product_info)

    # Also check legacy specs/ directories
    from litmus.matching.service import list_products as legacy_list_products
    for product in legacy_list_products():
        if product["id"] not in seen_ids:
            products.append(product)

    return products


def _list_fixtures() -> list[dict[str, Any]]:
    """List all fixture configurations."""
    fixtures = []
    search_paths = [
        Path.cwd() / "fixtures",
        Path.cwd() / "demo" / "fixtures",
    ]

    for fixtures_dir in search_paths:
        if not fixtures_dir.exists():
            continue
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
                            "product_family": fixture_info.get("product_family"),
                            "point_count": len(points),
                        })
            except Exception:
                continue

    return fixtures


def _list_sequences() -> list[dict[str, Any]]:
    """List available test sequences."""
    sequences = []
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    for seq_dir in search_paths:
        if not seq_dir.exists():
            continue
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
        Path.cwd() / "instruments",
        Path.cwd() / "demo" / "instruments",
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
                            "description": inst.get("description", ""),
                            "capabilities": [c.get("name", "") for c in capabilities],
                        })
            except Exception:
                continue

    return types


def _list_runs() -> list[dict[str, Any]]:
    """List recent test runs."""
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir="results")
    return backend.list_runs(limit=50)


# =============================================================================
# Tool 3: get
# =============================================================================


def get_tool(entity_type: str, id: str) -> dict[str, Any]:
    """Get full details of an entity.

    Args:
        entity_type: One of: station, product, fixture, sequence, instrument, run
        id: The entity ID

    Returns:
        Full entity details.
    """
    if entity_type not in ENTITY_TYPES:
        return {"error": f"Unknown entity_type '{entity_type}'. Valid: {ENTITY_TYPES}"}

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
    from litmus.matching.service import load_station_config

    config = load_station_config(station_id)
    if not config:
        return {"error": f"Station '{station_id}' not found"}
    return config


def _get_product(product_id: str) -> dict[str, Any]:
    """Get product specification.

    Searches both new products/ folder structure and legacy specs/ directories.
    """
    from litmus.products.folder import ProductFolder

    # First, try products/ folder structure (new style)
    products_paths = [
        Path.cwd() / "products",
        Path.cwd() / "demo" / "products",
    ]

    for products_dir in products_paths:
        folder_path = products_dir / product_id
        if folder_path.exists() and (folder_path / "manifest.yaml").exists():
            try:
                folder = ProductFolder.load(folder_path)
                result = {
                    "product": {
                        "id": folder.product_id,
                        "name": folder.name,
                        "description": folder.manifest.description,
                    },
                    "workflow": {
                        "current_step": folder.current_step.value if folder.current_step else None,
                        "completed_steps": [s.value for s in folder.manifest.completed_steps],
                    },
                    "files": folder.manifest.files.model_dump(exclude_none=True),
                }

                # Load spec if available
                spec = folder.load_spec()
                if spec:
                    result["product"]["revision"] = spec.revision
                    result["characteristics"] = {
                        name: {
                            "direction": char.direction.value,
                            "domain": char.domain.value,
                            "signal_types": [st.value for st in char.signal_types],
                            "units": char.units,
                            "conditions": [
                                {
                                    **cond.condition_params,
                                    "nominal": str(cond.nominal) if cond.nominal else None,
                                    "tolerance_pct": (
                                        str(cond.tolerance_pct) if cond.tolerance_pct else None
                                    ),
                                    "limit_low": str(cond.limit_low) if cond.limit_low else None,
                                    "limit_high": (
                                        str(cond.limit_high) if cond.limit_high else None
                                    ),
                                }
                                for cond in char.conditions
                            ],
                        }
                        for name, char in spec.characteristics.items()
                    }
                    result["test_requirements"] = {
                        name: {
                            "characteristic_ref": req.characteristic_ref,
                            "conditions": req.conditions,
                            "guardband_pct": str(req.guardband_pct),
                            "description": req.description,
                        }
                        for name, req in spec.test_requirements.items()
                    }

                return result
            except Exception:
                pass

    # Fall back to legacy specs/ directories
    from litmus.matching.service import load_product_by_id

    product = load_product_by_id(product_id)
    if not product:
        return {"error": f"Product '{product_id}' not found"}

    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "revision": product.revision,
        },
        "characteristics": {
            name: {
                "direction": char.direction.value,
                "domain": char.domain.value,
                "signal_types": [st.value for st in char.signal_types],
                "units": char.units,
                "conditions": [
                    {
                        **cond.condition_params,
                        "nominal": str(cond.nominal) if cond.nominal else None,
                        "tolerance_pct": str(cond.tolerance_pct) if cond.tolerance_pct else None,
                        "limit_low": str(cond.limit_low) if cond.limit_low else None,
                        "limit_high": str(cond.limit_high) if cond.limit_high else None,
                    }
                    for cond in char.conditions
                ],
            }
            for name, char in product.characteristics.items()
        },
        "test_requirements": {
            name: {
                "characteristic_ref": req.characteristic_ref,
                "conditions": req.conditions,
                "guardband_pct": str(req.guardband_pct),
                "description": req.description,
            }
            for name, req in product.test_requirements.items()
        },
    }


def _get_fixture(fixture_id: str) -> dict[str, Any]:
    """Get fixture configuration."""
    search_paths = [
        Path.cwd() / "fixtures",
        Path.cwd() / "demo" / "fixtures",
    ]

    for fixtures_dir in search_paths:
        yaml_file = fixtures_dir / f"{fixture_id}.yaml"
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
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    for seq_dir in search_paths:
        yaml_file = seq_dir / f"{sequence_id}.yaml"
        if yaml_file.exists():
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data:
                    return data

    return {"error": f"Sequence '{sequence_id}' not found"}


def _get_instrument(instrument_type: str) -> dict[str, Any]:
    """Get instrument library definition."""
    from litmus.matching.service import load_instrument_library

    library = load_instrument_library(instrument_type)
    if not library:
        return {"error": f"Instrument type '{instrument_type}' not found"}
    return library


def _get_run(run_id: str) -> dict[str, Any]:
    """Get test run details."""
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir="results")
    run = backend.get_run(run_id)

    if not run:
        return {"error": f"Run '{run_id}' not found"}

    # Include measurements
    measurements = backend.get_measurements(run_id)
    run["measurements"] = measurements

    return run


# =============================================================================
# Tool 4: save
# =============================================================================


def save_tool(entity_type: str, id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Validate and save an entity.

    Args:
        entity_type: One of: station, product, fixture, sequence, instrument, test
        id: The entity ID (used as filename)
        content: The entity content to save

    Returns:
        Result with path to saved file or errors.
    """
    valid_types = ["station", "product", "fixture", "sequence", "instrument", "test"]
    if entity_type not in valid_types:
        return {"error": f"Unknown entity_type '{entity_type}'. Valid: {valid_types}"}

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
    stations_dir = Path.cwd() / "stations"
    stations_dir.mkdir(parents=True, exist_ok=True)

    filepath = stations_dir / f"{station_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_product(product_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Validate and save product specification using ProductFolder.

    Creates or updates a product folder with the specification.
    """
    from litmus.products.folder import ProductFolder
    from litmus.products.manifest import WorkflowStep

    errors = []

    # Validate
    if "product" not in content:
        errors.append("Missing 'product' section")
    else:
        product_data = content["product"]
        if "id" not in product_data:
            errors.append("product.id is required")
        if "name" not in product_data:
            errors.append("product.name is required")

    if "characteristics" in content:
        for name, char in content["characteristics"].items():
            if "direction" not in char:
                errors.append(f"characteristics.{name}.direction is required")
            if "domain" not in char:
                errors.append(f"characteristics.{name}.domain is required")

    if errors:
        return {"success": False, "errors": errors}

    # Use products/ folder structure
    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id

    try:
        if folder_path.exists() and (folder_path / "manifest.yaml").exists():
            # Load existing folder
            folder = ProductFolder.load(folder_path)
        else:
            # Create new folder
            folder = ProductFolder.create(
                base_path=products_dir,
                product_id=product_id,
                name=content["product"].get("name", product_id),
                description=content["product"].get("description"),
            )

        # Save the spec file directly (ProductFolder.save_spec expects a Product model)
        spec_path = folder.path / "spec.yaml"
        with open(spec_path, "w") as f:
            yaml.dump(content, f, default_flow_style=False, sort_keys=False)

        folder.manifest.files.spec = "spec.yaml"

        # Mark parse_datasheet step complete if we're saving a spec
        if folder.current_step == WorkflowStep.PARSE_DATASHEET:
            folder.manifest.complete_step(WorkflowStep.PARSE_DATASHEET)

        folder.save_manifest()

        return {
            "success": True,
            "path": str(folder.path),
            "spec_path": str(spec_path),
            "current_step": folder.current_step.value if folder.current_step else None,
            "completed_steps": [s.value for s in folder.manifest.completed_steps],
        }

    except Exception as e:
        return {"success": False, "errors": [str(e)]}


def _save_fixture(fixture_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Validate and save fixture configuration."""
    errors = []

    if "fixture" not in content:
        errors.append("Missing 'fixture' section")
    else:
        fixture = content["fixture"]
        if "id" not in fixture:
            errors.append("fixture.id is required")

    if "points" in content:
        for name, point in content["points"].items():
            if not isinstance(point, dict):
                errors.append(f"points.{name} must be a dict")
                continue
            if not point.get("instrument"):
                errors.append(f"points.{name}.instrument is required")

    if errors:
        return {"success": False, "errors": errors}

    fixtures_dir = Path.cwd() / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    filepath = fixtures_dir / f"{fixture_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_sequence(sequence_id: str, content: dict[str, Any]) -> dict[str, Any]:
    """Validate and save test sequence."""
    errors = []

    if "sequence" not in content:
        errors.append("Missing 'sequence' section")
    else:
        seq = content["sequence"]
        if "id" not in seq:
            errors.append("sequence.id is required")
        if "steps" not in seq:
            errors.append("sequence.steps is required")
        elif not isinstance(seq["steps"], list):
            errors.append("sequence.steps must be a list")
        elif len(seq["steps"]) == 0:
            errors.append("sequence.steps cannot be empty")

    if errors:
        return {"success": False, "errors": errors}

    sequences_dir = Path.cwd() / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    filepath = sequences_dir / f"{sequence_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_instrument(instrument_type: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save instrument library definition."""
    instruments_dir = Path.cwd() / "instruments"
    instruments_dir.mkdir(parents=True, exist_ok=True)

    filepath = instruments_dir / f"{instrument_type}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(content, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def _save_test(path: str, content: dict[str, Any]) -> dict[str, Any]:
    """Save a Python test file.

    Args:
        path: Relative path under tests/
        content: Dict with 'code' key containing Python source
    """
    if "code" not in content:
        return {"success": False, "errors": ["content.code is required"]}

    tests_dir = Path.cwd() / "tests"
    filepath = tests_dir / path

    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w") as f:
        f.write(content["code"])

    return {"success": True, "path": str(filepath)}


# =============================================================================
# Tool 5: match
# =============================================================================


def match_tool(
    product_id: str | None = None,
    station_id: str | None = None,
    fixture_id: str | None = None,
) -> dict[str, Any]:
    """Check compatibility between products, stations, and fixtures.

    Args:
        product_id: Product to check (finds compatible stations if alone)
        station_id: Station to check (detailed check if with product_id)
        fixture_id: Fixture to check (finds stations with required instruments)

    Returns:
        Compatibility results.
    """
    from litmus.matching.service import (
        check_station_compatibility,
        find_compatible_stations,
        get_required_capabilities,
        load_product_by_id,
    )

    # Just product_id: find compatible stations and derive requirements
    if product_id and not station_id and not fixture_id:
        product = load_product_by_id(product_id)
        if not product:
            return {"error": f"Product '{product_id}' not found"}

        # Get required capabilities
        requirements = get_required_capabilities(product)
        req_list = [
            {
                "characteristic": req.characteristic_name,
                "direction": req.direction.value,
                "domain": req.domain.value,
                "signal_types": [st.value for st in req.signal_types],
            }
            for req in requirements
        ]

        # Find compatible stations
        matches = find_compatible_stations(product)
        stations = [
            {
                "station_id": m.station_id,
                "station_name": m.station_name,
                "compatible": m.compatible,
                "satisfied_count": len([x for x in m.match_result.matches if x.satisfied]),
                "missing_count": len(m.match_result.missing),
            }
            for m in matches
        ]

        return {
            "product_id": product_id,
            "required_capabilities": req_list,
            "compatible_stations": stations,
        }

    # Product + station: detailed compatibility check
    if product_id and station_id:
        result = check_station_compatibility(product_id, station_id)
        if not result:
            return {"error": f"Product '{product_id}' or station '{station_id}' not found"}
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

        if not required_instruments:
            return {"error": "Fixture has no instrument references"}

        # Check each station
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
                    "station_name": station.get("name", sid),
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
# Tool 6: run
# =============================================================================


def run_tool(sequence_id: str, station_id: str, dut_serial: str) -> dict[str, Any]:
    """Start a test sequence run.

    Executes pytest with the specified sequence/tests and returns results.

    Args:
        sequence_id: The sequence to run (or test file path like "demo/tests/test_tps54302.py")
        station_id: Which station to run on
        dut_serial: Serial number of device under test

    Returns:
        Run info with outcome and summary.
    """
    import subprocess
    from datetime import datetime

    # Determine test target - direct path or find by product/sequence name
    if sequence_id.endswith(".py") or "::" in sequence_id:
        # Direct test path
        test_targets = [sequence_id]
    else:
        # Try exact name, then strip common suffixes (_datasheet, _validation, _smoke, etc.)
        names_to_try = [sequence_id]
        for suffix in ["_datasheet", "_validation", "_smoke", "_full", "_quick"]:
            if sequence_id.endswith(suffix):
                names_to_try.append(sequence_id[:-len(suffix)])
                break

        test_targets = []
        search_paths = []
        for name in names_to_try:
            paths = [
                Path.cwd() / "tests" / f"test_{name}.py",
                Path.cwd() / "demo" / "tests" / f"test_{name}.py",
            ]
            search_paths.extend(paths)
            for test_path in paths:
                if test_path.exists():
                    test_targets = [str(test_path)]
                    break
            if test_targets:
                break

        if not test_targets:
            return {
                "error": f"Test file not found for: {sequence_id}",
                "searched": [str(p) for p in search_paths],
            }

    # Build pytest command - use pytest from same venv as litmus
    import sys
    pytest_path = Path(sys.executable).parent / "pytest"
    cmd = [
        str(pytest_path),
        *test_targets,  # Spread test targets as separate args
        f"--dut-serial={dut_serial}",
        f"--station={station_id}",
        "--results-dir=results",
        "-v", "--tb=short",
        "--simulate",  # Use simulation mode for MCP
    ]

    started_at = datetime.now()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=str(Path.cwd()),
        )

        # Parse output for summary
        output_lines = result.stdout.split("\n")
        summary_line = ""
        for line in reversed(output_lines):
            if "passed" in line or "failed" in line or "error" in line:
                summary_line = line.strip()
                break

        # Determine outcome
        if result.returncode == 0:
            status = "passed"
        elif result.returncode == 1:
            status = "failed"
        else:
            status = "error"

        # Try to get run_id from results
        from litmus.data.backends.parquet import ParquetBackend
        backend = ParquetBackend(results_dir="results")
        recent_runs = backend.list_runs(limit=1)
        run_id = recent_runs[0].get("test_run_id", "unknown") if recent_runs else "unknown"

        return {
            "run_id": run_id,
            "status": status,
            "returncode": result.returncode,
            "summary": summary_line,
            "test_targets": test_targets,
            "dut_serial": dut_serial,
            "station_id": station_id,
            "started_at": started_at.isoformat(),
            "output": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        }

    except subprocess.TimeoutExpired:
        return {
            "error": "Test execution timed out after 5 minutes",
            "test_targets": test_targets,
        }
    except Exception as e:
        return {
            "error": f"Failed to run tests: {e}",
            "test_targets": test_targets,
        }


# =============================================================================
# Tool 7: status
# =============================================================================


def status_tool(run_id: str) -> dict[str, Any]:
    """Get status of a test run.

    Args:
        run_id: The run ID from run tool

    Returns:
        Run status, outcome, and measurements.
    """
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir="results")
    run = backend.get_run(run_id)

    if not run:
        return {
            "run_id": run_id,
            "status": "not_found",
            "message": f"Run '{run_id}' not found in results",
        }

    return {
        "run_id": run_id,
        "status": "completed" if run.get("ended_at") else "running",
        "outcome": run.get("outcome"),
        "dut_serial": run.get("dut_serial"),
        "station_id": run.get("station_id"),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "total_steps": run.get("total_steps", 0),
        "failed_steps": run.get("failed_steps", 0),
    }


# =============================================================================
# Tool 8: open_ui
# =============================================================================


def open_ui_tool(
    entity_type: str, id: str, base_url: str = "http://localhost:8000"
) -> dict[str, Any]:
    """Get URL to open entity in browser UI.

    Args:
        entity_type: Type of entity (product, station, run, fixture)
        id: Entity ID
        base_url: UI server URL

    Returns:
        URL to open in browser.
    """
    routes = {
        "product": f"/products/{id}",
        "station": f"/stations/{id}",
        "run": f"/results/{id}",
        "fixture": f"/fixtures/{id}",
        "sequence": f"/sequences/{id}",
    }

    if entity_type not in routes:
        return {
            "success": False,
            "error": f"Unknown entity_type '{entity_type}'. Valid: {list(routes.keys())}",
        }

    url = f"{base_url}{routes[entity_type]}"

    return {
        "success": True,
        "url": url,
        "message": f"Open {url} to view/edit {entity_type} '{id}'",
    }


# =============================================================================
# Tool 9: read (project files)
# =============================================================================


TEST_TEMPLATE = '''"""Tests for {product_name}.

Generated from product specification.
"""

from litmus.execution import litmus_test
from litmus.instruments import DMM, PSU


@pytest.fixture
def psu():
    """Power supply for DUT input."""
    with PSU("SIM::PSU1", simulated=True) as psu:
        psu.set_voltage(5.0)
        psu.enable()
        yield psu
        psu.disable()


@pytest.fixture
def dmm():
    """DMM for output measurement."""
    with DMM("SIM::DMM1", simulated=True, sim_values={{"voltage": 3.3}}) as dmm:
        yield dmm


@litmus_test
def test_output_voltage(vector, dmm):
    """Measure output voltage - limits from config.yaml."""
    return dmm.measure_dc_voltage()


@litmus_test
def test_efficiency(vector, psu, dmm):
    """Calculate efficiency."""
    v_in = psu.measure_voltage()
    i_in = psu.measure_current()
    v_out = dmm.measure_dc_voltage()
    i_out = vector.get("load_current", 0.1)

    efficiency = (v_out * i_out) / (v_in * i_in) * 100
    return efficiency
'''


def read_tool(path: str) -> dict[str, Any]:
    """Read a file from the project directory.

    Can read datasheets, specs, configs, and other project files.
    Paths are relative to the project root.

    Special paths:
    - "template:test" - Get test file template using @litmus_test decorator

    Common paths:
    - products/{id}/ - Product folders (new structure)
    - products/{id}/datasheet.md - Product datasheet
    - products/{id}/spec.yaml - Product specification
    - products/{id}/tests/ - Product tests
    - demo/products/{id}/ - Example product folders
    - demo/stations/*.yaml - Station configurations
    - demo/tests/*.py - Test files (use test_power_board.py as example)

    Args:
        path: Relative path to file (e.g., "demo/products/tps54302/spec.yaml")

    Returns:
        File contents or error.
    """
    # Special template paths
    if path == "template:test":
        return {
            "type": "template",
            "name": "test",
            "description": "Test file template using @litmus_test decorator",
            "content": TEST_TEMPLATE.format(product_name="ProductName"),
            "notes": [
                "@litmus_test decorator handles vectors, limits, and logging",
                "Just return the measured value - limits come from config.yaml",
                "See demo/tests/test_power_board.py for complete example",
            ],
        }
    # Security: only allow reading from project directory
    cwd = Path.cwd()
    filepath = cwd / path

    # Resolve to absolute and check it's under cwd
    try:
        filepath = filepath.resolve()
        if not str(filepath).startswith(str(cwd.resolve())):
            return {"error": "Path must be within project directory"}
    except Exception:
        return {"error": f"Invalid path: {path}"}

    if not filepath.exists():
        # Try to suggest alternatives
        suggestions = []
        parent = filepath.parent
        if parent.exists():
            suggestions = [f.name for f in parent.glob("*") if f.is_file()][:5]

        return {
            "error": f"File not found: {path}",
            "suggestions": suggestions if suggestions else None,
        }

    if filepath.is_dir():
        # List directory contents
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

    # Read file
    try:
        content = filepath.read_text()
        return {
            "type": "file",
            "path": path,
            "content": content,
        }
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}
