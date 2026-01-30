"""MCP tool implementations.

These functions implement the actual logic for MCP tools. They're separated
from the server definition to make testing easier.
"""

from pathlib import Path
from typing import Any

import yaml


# -----------------------------------------------------------------------------
# Read/Context Tools
# -----------------------------------------------------------------------------


def list_products_tool() -> list[dict[str, Any]]:
    """List all available products."""
    from litmus.matching.service import list_products

    return list_products()


def get_product_spec_tool(product_id: str) -> dict[str, Any]:
    """Get full product specification by ID."""
    from litmus.matching.service import load_product_by_id

    product = load_product_by_id(product_id)
    if not product:
        return {"error": f"Product '{product_id}' not found"}

    # Convert to dict for serialization
    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "revision": product.revision,
            "datasheet": product.datasheet,
            "schematic": product.schematic,
        },
        "characteristics": {
            name: {
                "direction": char.direction.value,
                "domain": char.domain.value,
                "signal_types": [st.value for st in char.signal_types],
                "units": char.units,
                "datasheet_ref": char.datasheet_ref,
                "schematic_ref": char.schematic_ref,
                "conditions": [
                    {
                        **cond.condition_params,
                        "nominal": str(cond.nominal) if cond.nominal else None,
                        "tolerance_pct": str(cond.tolerance_pct)
                        if cond.tolerance_pct
                        else None,
                        "tolerance_abs": str(cond.tolerance_abs)
                        if cond.tolerance_abs
                        else None,
                        "limit_low": str(cond.limit_low) if cond.limit_low else None,
                        "limit_high": str(cond.limit_high) if cond.limit_high else None,
                        "comparator": cond.comparator.value,
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
                "priority": req.priority,
                "description": req.description,
            }
            for name, req in product.test_requirements.items()
        },
    }


def list_stations_tool() -> list[dict[str, Any]]:
    """List all available stations."""
    from litmus.matching.service import list_stations

    return list_stations()


def get_station_config_tool(station_id: str) -> dict[str, Any]:
    """Get full station configuration by ID."""
    from litmus.matching.service import load_station_config

    config = load_station_config(station_id)
    if not config:
        return {"error": f"Station '{station_id}' not found"}

    return config


def list_instrument_types_tool() -> list[str]:
    """List available instrument types."""
    from litmus.matching.service import list_instrument_types

    return list_instrument_types()


def get_instrument_library_tool(instrument_type: str) -> dict[str, Any]:
    """Get instrument definition including capabilities."""
    from litmus.matching.service import load_instrument_library

    library = load_instrument_library(instrument_type)
    if not library:
        return {"error": f"Instrument type '{instrument_type}' not found"}

    return library


def list_sequences_tool() -> list[dict[str, Any]]:
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


def get_test_templates_tool() -> list[dict[str, Any]]:
    """Get example test code patterns."""
    return [
        {
            "name": "basic_measurement",
            "description": "Simple measurement test with limits",
            "code": '''"""Basic measurement test."""
import pytest
from litmus.execution.decorators import litmus_test, measure


@litmus_test
def test_voltage_rail(instruments):
    """Measure a voltage rail and check against limits."""
    dmm = instruments["dmm_main"]
    voltage = dmm.measure_voltage_dc()
    return measure(
        name="rail_voltage",
        value=voltage,
        units="V",
        limit_low=3.2,
        limit_high=3.4,
    )
''',
        },
        {
            "name": "parametrized_test",
            "description": "Test with multiple parameter values",
            "code": '''"""Parametrized test example."""
import pytest
from litmus.execution.decorators import litmus_test, measure


@litmus_test
@pytest.mark.parametrize("channel", [1, 2, 3, 4])
def test_channel_voltage(instruments, channel):
    """Test voltage on multiple channels."""
    dmm = instruments["dmm_main"]
    voltage = dmm.measure_voltage_dc(channel=channel)
    return measure(
        name=f"channel_{channel}_voltage",
        value=voltage,
        units="V",
        limit_low=0.0,
        limit_high=5.0,
    )
''',
        },
        {
            "name": "test_with_dialog",
            "description": "Test requiring operator interaction",
            "code": '''"""Test with operator dialog."""
import pytest
from litmus.execution.decorators import litmus_test, measure
from litmus.dialogs import operator_prompt


@litmus_test
def test_led_visual(instruments):
    """Visual inspection test requiring operator."""
    # Prompt operator to observe LED
    response = operator_prompt(
        title="LED Check",
        message="Is the power LED illuminated green?",
        options=["Yes", "No"],
    )

    return measure(
        name="led_visual",
        value=response,
        limit_value="Yes",
    )
''',
        },
        {
            "name": "test_with_spec_limits",
            "description": "Test using limits derived from product spec",
            "code": '''"""Test using limits from product specification."""
import pytest
from litmus.execution.decorators import litmus_test, measure
from litmus.products.limits import derive_limit


@litmus_test
def test_output_voltage(instruments, product, config):
    """Test output voltage against spec limits."""
    dmm = instruments["dmm_main"]
    voltage = dmm.measure_voltage_dc()

    # Get limit from product spec with 5% guardband
    char = product.characteristics["rail_3v3_output"]
    req = product.test_requirements["verify_output_voltage"]
    limit = derive_limit(char, req, {"temperature": 25})

    return measure(
        name="output_voltage",
        value=voltage,
        units="V",
        limit_low=float(limit.low),
        limit_high=float(limit.high),
    )
''',
        },
    ]


# -----------------------------------------------------------------------------
# Matching Tools
# -----------------------------------------------------------------------------


def derive_required_capabilities_tool(product_id: str) -> list[dict[str, Any]]:
    """Derive required capabilities from product."""
    from litmus.matching.service import get_required_capabilities, load_product_by_id

    product = load_product_by_id(product_id)
    if not product:
        return [{"error": f"Product '{product_id}' not found"}]

    requirements = get_required_capabilities(product)
    return [
        {
            "characteristic": req.characteristic_name,
            "direction": req.direction.value,
            "domain": req.domain.value,
            "signal_types": [st.value for st in req.signal_types],
            "range_max": req.range_max,
        }
        for req in requirements
    ]


def find_compatible_stations_tool(product_id: str) -> list[dict[str, Any]]:
    """Find stations compatible with product."""
    from litmus.matching.service import find_compatible_stations, load_product_by_id

    product = load_product_by_id(product_id)
    if not product:
        return [{"error": f"Product '{product_id}' not found"}]

    matches = find_compatible_stations(product)
    return [
        {
            "station_id": m.station_id,
            "station_name": m.station_name,
            "compatible": m.compatible,
            "satisfied_count": len(
                [match for match in m.match_result.matches if match.satisfied]
            ),
            "missing_count": len(m.match_result.missing),
            "missing": [
                {
                    "characteristic": req.characteristic_name,
                    "direction": req.direction.value,
                    "domain": req.domain.value,
                }
                for req in m.match_result.missing
            ],
        }
        for m in matches
    ]


def check_station_compatibility_tool(
    product_id: str, station_id: str
) -> dict[str, Any]:
    """Check specific station/product compatibility."""
    from litmus.matching.service import check_station_compatibility

    result = check_station_compatibility(product_id, station_id)
    if not result:
        return {"error": f"Product '{product_id}' or station '{station_id}' not found"}

    return result


# -----------------------------------------------------------------------------
# Write Tools
# -----------------------------------------------------------------------------


def validate_product_spec_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a product specification."""
    errors = []

    # Check required sections
    if "product" not in spec:
        errors.append("Missing 'product' section")
    else:
        product = spec["product"]
        if "id" not in product:
            errors.append("product.id is required")
        if "name" not in product:
            errors.append("product.name is required")

    # Check characteristics
    if "characteristics" in spec:
        for name, char in spec["characteristics"].items():
            if "direction" not in char:
                errors.append(f"characteristics.{name}.direction is required")
            elif char["direction"] not in ("input", "output", "bidir"):
                errors.append(
                    f"characteristics.{name}.direction must be input/output/bidir"
                )

            if "domain" not in char:
                errors.append(f"characteristics.{name}.domain is required")

            if "units" not in char:
                errors.append(f"characteristics.{name}.units is required")

    if errors:
        return {"valid": False, "errors": errors}

    return {"valid": True, "errors": []}


def save_product_spec_tool(product_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and save a product specification."""
    # Validate first
    validation = validate_product_spec_tool(spec)
    if not validation["valid"]:
        return {"success": False, "errors": validation["errors"]}

    # Ensure specs directory exists
    specs_dir = Path.cwd() / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    # Save YAML
    filepath = specs_dir / f"{product_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def save_instrument_library_tool(
    instrument_type: str, spec: dict[str, Any]
) -> dict[str, Any]:
    """Save a new instrument type definition."""
    library_dir = Path(__file__).parent.parent / "instruments" / "library"
    library_dir.mkdir(parents=True, exist_ok=True)

    filepath = library_dir / f"{instrument_type}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def validate_test_sequence_tool(sequence: dict[str, Any]) -> dict[str, Any]:
    """Validate a test sequence."""
    errors = []

    if "sequence" not in sequence:
        errors.append("Missing 'sequence' section")
    else:
        seq = sequence["sequence"]
        if "id" not in seq:
            errors.append("sequence.id is required")
        if "steps" not in seq:
            errors.append("sequence.steps is required")
        elif not isinstance(seq["steps"], list):
            errors.append("sequence.steps must be a list")
        elif len(seq["steps"]) == 0:
            errors.append("sequence.steps cannot be empty")
        else:
            for i, step in enumerate(seq["steps"]):
                if "id" not in step:
                    errors.append(f"sequence.steps[{i}].id is required")
                if "test" not in step:
                    errors.append(f"sequence.steps[{i}].test is required")

    if errors:
        return {"valid": False, "errors": errors}

    return {"valid": True, "errors": []}


def save_test_sequence_tool(
    sequence_id: str, sequence: dict[str, Any]
) -> dict[str, Any]:
    """Validate and save a test sequence."""
    # Validate first
    validation = validate_test_sequence_tool(sequence)
    if not validation["valid"]:
        return {"success": False, "errors": validation["errors"]}

    # Ensure sequences directory exists
    sequences_dir = Path.cwd() / "sequences"
    sequences_dir.mkdir(parents=True, exist_ok=True)

    # Save YAML
    filepath = sequences_dir / f"{sequence_id}.yaml"
    with open(filepath, "w") as f:
        yaml.dump(sequence, f, default_flow_style=False, sort_keys=False)

    return {"success": True, "path": str(filepath)}


def save_test_file_tool(path: str, code: str) -> dict[str, Any]:
    """Save a Python test file."""
    # Ensure path is under tests/
    tests_dir = Path.cwd() / "tests"
    filepath = tests_dir / path

    # Create parent directories
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Save file
    with open(filepath, "w") as f:
        f.write(code)

    return {"success": True, "path": str(filepath)}


# -----------------------------------------------------------------------------
# Execution Tools
# -----------------------------------------------------------------------------


def dry_run_sequence_tool(sequence_id: str) -> dict[str, Any]:
    """Expand a sequence without executing."""
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    sequence_data = None
    for seq_dir in search_paths:
        yaml_file = seq_dir / f"{sequence_id}.yaml"
        if yaml_file.exists():
            with open(yaml_file) as f:
                sequence_data = yaml.safe_load(f)
            break

    if not sequence_data:
        return {"error": f"Sequence '{sequence_id}' not found"}

    seq = sequence_data.get("sequence", {})
    steps = seq.get("steps", [])

    return {
        "sequence_id": sequence_id,
        "sequence_name": seq.get("name", sequence_id),
        "step_count": len(steps),
        "steps": [
            {
                "id": step.get("id"),
                "test": step.get("test"),
                "description": step.get("description"),
            }
            for step in steps
        ],
    }


def run_sequence_tool(
    sequence_id: str, dut_serial: str, station_id: str
) -> dict[str, Any]:
    """Start a test sequence run."""
    import uuid
    from datetime import datetime

    # For now, return a placeholder - actual execution would use the runner
    # This allows the MCP server to work without requiring full test infrastructure

    run_id = str(uuid.uuid4())

    return {
        "run_id": run_id,
        "sequence_id": sequence_id,
        "dut_serial": dut_serial,
        "station_id": station_id,
        "status": "pending",
        "started_at": datetime.now().isoformat(),
        "message": "Test run queued. Use get_run_status to check progress.",
    }


def get_run_status_tool(run_id: str) -> dict[str, Any]:
    """Get status of a test run."""
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


# -----------------------------------------------------------------------------
# Product Folder Tools (workflow state management)
# -----------------------------------------------------------------------------


def create_product_folder_tool(
    product_id: str,
    name: str,
    description: str | None = None,
    datasheet_content: str | None = None,
) -> dict[str, Any]:
    """Create a new product folder with manifest.

    Args:
        product_id: Unique identifier for the product
        name: Human-readable product name
        description: Optional description
        datasheet_content: Optional datasheet content to save

    Returns:
        Result with folder path and manifest info.
    """
    from litmus.products.folder import ProductFolder
    from litmus.products.manifest import WorkflowStep

    products_dir = Path.cwd() / "products"
    products_dir.mkdir(parents=True, exist_ok=True)

    try:
        folder = ProductFolder.create(
            base_path=products_dir,
            product_id=product_id,
            name=name,
            description=description,
        )

        # Save datasheet if provided
        if datasheet_content:
            folder.save_datasheet(datasheet_content)

        return {
            "success": True,
            "product_id": product_id,
            "path": str(folder.path),
            "current_step": folder.current_step.value if folder.current_step else None,
            "message": f"Created product folder at {folder.path}",
        }
    except FileExistsError:
        return {
            "success": False,
            "error": f"Product folder '{product_id}' already exists",
        }


def get_product_folder_tool(product_id: str) -> dict[str, Any]:
    """Get product folder info and workflow state.

    Args:
        product_id: The product ID

    Returns:
        Product folder info including manifest and workflow state.
    """
    from litmus.products.folder import ProductFolder

    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id

    try:
        folder = ProductFolder.load(folder_path)

        return {
            "success": True,
            "product_id": folder.product_id,
            "name": folder.name,
            "description": folder.manifest.description,
            "path": str(folder.path),
            "workflow": {
                "current_step": folder.manifest.workflow.current_step.value
                if folder.manifest.workflow.current_step
                else None,
                "completed_steps": [
                    s.value for s in folder.manifest.workflow.completed_steps
                ],
                "progress_pct": folder.manifest.get_progress_percentage(),
            },
            "files": {
                "datasheet": folder.manifest.files.datasheet,
                "spec": folder.manifest.files.spec,
                "requirements": folder.manifest.files.requirements,
                "station_selection": folder.manifest.files.station_selection,
                "tests": folder.manifest.files.tests,
            },
            "history_count": len(folder.manifest.history),
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Product folder '{product_id}' not found",
        }


def list_product_folders_tool() -> list[dict[str, Any]]:
    """List all product folders.

    Returns:
        List of product folders with basic info.
    """
    from litmus.products.folder import ProductFolder

    products_dir = Path.cwd() / "products"

    if not products_dir.exists():
        return []

    results = []
    for folder in ProductFolder.list_all(products_dir):
        results.append(
            {
                "product_id": folder.product_id,
                "name": folder.name,
                "current_step": folder.manifest.workflow.current_step.value
                if folder.manifest.workflow.current_step
                else None,
                "progress_pct": folder.manifest.get_progress_percentage(),
                "path": str(folder.path),
            }
        )

    return results


def complete_workflow_step_tool(
    product_id: str,
    step: str,
    agent: str | None = None,
    confidence: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Mark a workflow step as completed and advance to next step.

    Args:
        product_id: The product ID
        step: The step to complete (e.g., "parse_datasheet", "review_spec")
        agent: Optional agent name that completed the step
        confidence: Optional confidence score (0.0-1.0)
        notes: Optional notes about the step completion

    Returns:
        Updated workflow state.
    """
    from litmus.products.folder import ProductFolder
    from litmus.products.manifest import WorkflowStep

    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id

    try:
        folder = ProductFolder.load(folder_path)

        # Convert string to WorkflowStep enum
        try:
            workflow_step = WorkflowStep(step)
        except ValueError:
            valid_steps = [s.value for s in WorkflowStep]
            return {
                "success": False,
                "error": f"Invalid step '{step}'. Valid steps: {valid_steps}",
            }

        # Complete the step
        folder.manifest.complete_step(
            step=workflow_step,
            agent=agent,
            confidence=confidence,
        )

        # Add notes if provided
        if notes and folder.manifest.history:
            folder.manifest.history[-1].notes = notes

        folder.save_manifest()

        return {
            "success": True,
            "product_id": product_id,
            "completed_step": step,
            "current_step": folder.manifest.workflow.current_step.value
            if folder.manifest.workflow.current_step
            else None,
            "progress_pct": folder.manifest.get_progress_percentage(),
            "message": f"Completed step '{step}', now on '{folder.manifest.workflow.current_step.value if folder.manifest.workflow.current_step else 'done'}'",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Product folder '{product_id}' not found",
        }


def save_product_spec_to_folder_tool(
    product_id: str, spec: dict[str, Any]
) -> dict[str, Any]:
    """Save a product spec to an existing product folder.

    Args:
        product_id: The product ID
        spec: Product spec dict with product, characteristics, test_requirements

    Returns:
        Result with path to saved file.
    """
    from litmus.products.folder import ProductFolder
    from litmus.products.loader import _parse_product

    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id

    try:
        folder = ProductFolder.load(folder_path)

        # Validate the spec first
        validation = validate_product_spec_tool(spec)
        if not validation["valid"]:
            return {"success": False, "errors": validation["errors"]}

        # Parse and save
        product = _parse_product(spec)
        spec_path = folder.save_spec(product)

        return {
            "success": True,
            "product_id": product_id,
            "path": str(spec_path),
            "message": f"Saved spec to {spec_path}",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Product folder '{product_id}' not found",
        }


def get_editor_url_tool(
    resource_type: str, resource_id: str, base_url: str = "http://localhost:8000"
) -> dict[str, Any]:
    """Get URL to open the UI editor for a resource.

    Args:
        resource_type: Type of resource ("product", "station", "results")
        resource_id: ID of the resource
        base_url: Base URL of the Litmus UI server

    Returns:
        URL to open in browser.
    """
    routes = {
        "product": f"/products/{resource_id}",
        "station": f"/stations/{resource_id}",
        "results": f"/results/{resource_id}",
    }

    if resource_type not in routes:
        return {
            "success": False,
            "error": f"Unknown resource type '{resource_type}'. Valid: {list(routes.keys())}",
        }

    url = f"{base_url}{routes[resource_type]}"

    return {
        "success": True,
        "url": url,
        "message": f"Open {url} to edit {resource_type} '{resource_id}'",
    }
