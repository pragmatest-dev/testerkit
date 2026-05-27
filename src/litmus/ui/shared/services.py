"""Data services for UI - business logic wrappers over litmus.store.

NO direct yaml.safe_load or Path I/O here — all persistence goes through litmus.store.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import RunSummary
from litmus.instruments.loader import resolve_station_instruments
from litmus.matching import service as matching_service
from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.product import Product
from litmus.models.station import StationConfig, StationType
from litmus.models.test_config import FixtureConfig
from litmus.products.folder import ProductFolder
from litmus.store import (
    create_catalog_entry as store_create_catalog_entry,
)
from litmus.store import (
    create_fixture as store_create_fixture,
)
from litmus.store import (
    create_product as store_create_product,
)
from litmus.store import (
    create_station as store_create_station,
)
from litmus.store import (
    find_catalog_dirs,
    load_catalog_from_directory,
    load_instrument_files,
    load_product,
    load_project_config,
    normalize_and_check_instrument_types,
)
from litmus.store import (
    get_catalog_entry as store_get_catalog_entry,
)
from litmus.store import (
    get_fixture as store_get_fixture,
)
from litmus.store import (
    get_instrument_asset as store_get_instrument_asset,
)
from litmus.store import (
    get_product as store_get_product,
)
from litmus.store import (
    get_station as store_get_station,
)
from litmus.store import (
    list_fixtures as store_list_fixtures,
)
from litmus.store import (
    list_instrument_assets as store_list_instrument_assets,
)
from litmus.store import (
    list_stations as store_list_stations,
)
from litmus.store import (
    load_station_type as store_load_station_type,
)
from litmus.store import (
    save_catalog_entry as store_save_catalog_entry,
)
from litmus.store import (
    save_fixture as store_save_fixture,
)
from litmus.store import (
    save_product as store_save_product,
)
from litmus.store import (
    save_station as store_save_station,
)
from litmus.store import (
    save_station_type as store_save_station_type,
)
from litmus.utils.paths import get_instrument_paths

# -----------------------------------------------------------------------------
# Product Services
# -----------------------------------------------------------------------------


def discover_products() -> list[dict]:
    """Discover products from the products/ directory.

    Flat files (products/id.yaml) are the canonical convention.
    Manifest-based folders and other nested layouts are also supported via rglob.
    """
    products = []
    seen_ids: set[str] = set()

    products_dirs = [Path.cwd() / "products"]

    for products_dir in products_dirs:
        if not products_dir.exists():
            continue

        # 1. Check manifest-based folders (full workflow with tracking)
        for folder in ProductFolder.list_all(products_dir):
            spec = folder.load_spec()
            product_id = folder.product_id

            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            if spec:
                products.append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "description": spec.description or "",
                        "revision": spec.revision or "",
                        "pins": None,
                        "characteristics": {
                            name: char.model_dump() for name, char in spec.characteristics.items()
                        },
                        "file": (
                            str(folder.path / folder.manifest.files.spec)
                            if folder.manifest.files.spec
                            else None
                        ),
                        "folder_path": str(folder.path),
                        "workflow_step": folder.current_step.value if folder.current_step else None,
                        "completed_steps": [s.value for s in folder.manifest.completed_steps],
                        "files": folder.manifest.files.model_dump(),
                    }
                )
            else:
                products.append(
                    {
                        "id": product_id,
                        "name": folder.name,
                        "description": folder.manifest.description or "",
                        "revision": "",
                        "pins": None,
                        "characteristics": {},
                        "file": None,
                        "folder_path": str(folder.path),
                        "workflow_step": folder.current_step.value if folder.current_step else None,
                        "completed_steps": [s.value for s in folder.manifest.completed_steps],
                        "files": folder.manifest.files.model_dump(),
                    }
                )

        # 2. Discover all YAML files (flat and nested, no manifest required)
        for yaml_file in sorted(products_dir.rglob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                p = load_product(yaml_file)
            except (OSError, ValueError, KeyError):
                continue
            if p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            products.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description or "",
                    "revision": p.revision or "",
                    "pins": None,
                    "characteristics": {
                        name: char.model_dump() for name, char in p.characteristics.items()
                    },
                    "file": str(yaml_file),
                    "folder_path": str(yaml_file.parent),
                    "workflow_step": None,
                    "completed_steps": [],
                    "files": {},
                }
            )

    return products


class ProductRow(BaseModel):
    """One row in the merged products list (YAML-configured + parquet-observed).

    Mirrors :class:`StationRow`'s shape (provenance values + run counts)
    so the entity-observed-view pages stay structurally identical.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str = ""
    revision: str = ""
    characteristics: int = 0
    runs: int = 0
    passed: int = 0
    failed: int = 0
    last_run: datetime | None = None
    provenance: Literal["configured", "observed_only"]


def products_with_provenance() -> list[ProductRow]:
    """Union of YAML-configured products and products observed in runs.

    Two passes: every YAML product becomes a row tagged ``configured``
    or ``in_use`` depending on whether any runs reference its id; any
    ``product_id`` present in run history without a matching YAML file
    becomes an ``observed_only`` row.
    """
    configured = {p["id"]: p for p in discover_products()}
    usage = usage_stats_by("product_id")

    rows: list[ProductRow] = []
    for product_id, product in configured.items():
        stats = usage.get(product_id, {})
        runs = stats.get("runs", 0)
        rows.append(
            ProductRow(
                id=product_id,
                name=product.get("name", "") or "",
                revision=product.get("revision", "") or "",
                characteristics=len(product.get("characteristics", {}) or {}),
                runs=runs,
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="configured",
            )
        )

    for product_id, stats in usage.items():
        if product_id in configured:
            continue
        rows.append(
            ProductRow(
                id=product_id,
                runs=stats.get("runs", 0),
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="observed_only",
            )
        )

    return rows


def load_product_model(product_id: str):
    """Load a Product model by ID."""
    return store_get_product(product_id)


def create_product(product_id: str, name: str, description: str = "") -> dict | None:
    """Create a new product folder.

    Returns dict with product info if successful, None if product already exists.
    """
    product = store_create_product(product_id, name, description)
    if product is None:
        return None

    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description or "",
        "folder_path": str(folder_path),
    }


def get_required_capabilities(product) -> list[dict]:
    """Get required instrument capabilities for a product."""
    if not product:
        return []

    capabilities = []
    for char_name, char in product.characteristics.items():
        capabilities.append(
            {
                "characteristic": char_name,
                "direction": char.direction.value,
                "function": char.function.value,
                "signals": ", ".join(char.signals.keys()) if char.signals else "",
            }
        )
    return capabilities


def get_compatible_stations_for_product(product_id: str) -> list[dict]:
    """Get stations that have instruments satisfying product requirements."""
    product = store_get_product(product_id)
    if not product:
        return []

    matches = matching_service.find_compatible_stations(product)
    return [
        {"id": m.station_id, "name": m.station_name, "location": m.station_name}
        for m in matches
        if m.compatible
    ]


def get_partial_stations_for_product(product_id: str) -> list[dict]:
    """Get stations with partial capability coverage for a product."""
    product = store_get_product(product_id)
    if not product:
        return []

    partial_matches = matching_service.find_partial_stations(product)
    return [
        {
            "id": m.station_id,
            "name": m.station_name,
            "location": m.location,
            "coverage": m.coverage_pct,
            "missing": m.missing,
        }
        for m in partial_matches
    ]


def get_all_station_matches_for_product(product_id: str) -> dict[str, list]:
    """Get all stations categorized by compatibility level."""
    product = store_get_product(product_id)
    if not product:
        return {"compatible": [], "partial": [], "incompatible": []}

    return matching_service.find_all_station_matches(product)


def save_product(product_id: str, product_data: dict) -> None:
    """Save product specification to YAML file."""
    product_dict = {
        "id": product_data.get("id", product_id),
        "name": product_data.get("name", ""),
        "description": product_data.get("description", ""),
        "characteristics": product_data.get("characteristics", {}),
    }
    if product_data.get("revision"):
        product_dict["revision"] = product_data["revision"]
    if product_data.get("pins"):
        product_dict["pins"] = product_data["pins"]

    product = Product.model_validate(product_dict)
    store_save_product(product)


# -----------------------------------------------------------------------------
# Station Services
# -----------------------------------------------------------------------------


def discover_stations():
    """Discover station configurations from YAML files."""
    return store_list_stations()


class StationRow(BaseModel):
    """One row in the merged stations list (YAML-configured + parquet-observed).

    `provenance` carries the config-vs-data relationship for the row:

    - ``configured`` — YAML exists (with or without recorded runs)
    - ``observed_only`` — appears in run history with no YAML counterpart

    Activity for ``configured`` rows lives in the Runs column, not the
    chip — splitting "in use" into its own chip duplicated the Runs
    column without adding precision.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str = ""
    location: str = ""
    instruments: int = 0
    runs: int = 0
    passed: int = 0
    failed: int = 0
    last_run: datetime | None = None
    provenance: Literal["configured", "observed_only"]


def stations_with_provenance() -> list[StationRow]:
    """Union of YAML-configured stations and stations observed in runs.

    Two passes: (1) every YAML station becomes a row tagged ``configured``
    or ``in_use`` depending on whether any runs reference its id;
    (2) any station id present in run history without a matching YAML
    file becomes an ``observed_only`` row. Run stats come from the
    existing ``usage_stats_by`` SQL aggregation — no extra query.
    """
    configured = {s.id: s for s in discover_stations()}
    usage = usage_stats_by("station_id")

    rows: list[StationRow] = []
    for station_id, station in configured.items():
        stats = usage.get(station_id, {})
        runs = stats.get("runs", 0)
        rows.append(
            StationRow(
                id=station_id,
                name=station.name or "",
                location=station.location or "",
                instruments=len(station.instruments or {}),
                runs=runs,
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="configured",
            )
        )

    for station_id, stats in usage.items():
        if station_id in configured:
            continue
        rows.append(
            StationRow(
                id=station_id,
                runs=stats.get("runs", 0),
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="observed_only",
            )
        )

    return rows


def load_station_config(station_id: str):
    """Load station configuration by ID."""
    return store_get_station(station_id)


def create_station(
    station_id: str,
    name: str,
    location: str = "",
    description: str = "",
):
    """Create a new station configuration file."""
    return store_create_station(station_id, name, location, description)


def save_station(_station_id: str, station_data: dict, instruments_data: dict) -> None:
    """Save station configuration to YAML file."""
    normalize_and_check_instrument_types(instruments_data)
    station_dict = {**station_data, "instruments": instruments_data}
    station = StationConfig.model_validate(station_dict)
    store_save_station(station)


def get_station_capabilities(config):
    """Get capabilities from all instruments in a station."""
    if not config:
        return []
    return matching_service.get_station_capabilities(config)


def station_compatible_with_product(station_config, product) -> bool:
    """Check if a station is compatible with a product."""
    if not station_config or not product:
        return False
    result = matching_service.check_station_compatibility(product.id, station_config.id)
    return result.get("compatible", False) if result else False


# -----------------------------------------------------------------------------
# Instrument Services
# -----------------------------------------------------------------------------


def discover_instrument_types():
    """Discover available instrument types from catalog entries.

    Returns one InstrumentCatalogEntry per unique type (first seen wins).
    """
    entries = []
    seen_types: set[str] = set()
    for cat_dir in find_catalog_dirs():
        for _, entry in load_catalog_from_directory(cat_dir).items():
            if entry.type in seen_types:
                continue
            seen_types.add(entry.type)
            entries.append(entry)
    return entries


def load_catalog_entry_by_type(instrument_type: str):
    """Load a catalog entry by type or ID."""
    return store_get_catalog_entry(instrument_type)


def save_catalog_entry(instrument_type: str, data: dict) -> None:
    """Save a catalog entry to catalog/."""
    inst = data.get("instrument", {})
    entry = InstrumentCatalogEntry.model_validate(
        {
            "id": inst.get("type", instrument_type),
            "type": inst.get("type", instrument_type),
            "manufacturer": inst.get("manufacturer", "User"),
            "model": inst.get("name", instrument_type),
            "name": inst.get("name", ""),
            "description": inst.get("description"),
            "capabilities": data.get("capabilities", []),
        }
    )
    store_save_catalog_entry(entry)


def discover_instrument_assets():
    """Discover per-device instrument asset files."""
    return store_list_instrument_assets()


class DUTRow(BaseModel):
    """One row in the DUTs list — purely observed from run history.

    DUTs are never declared in YAML by design (they're the unit under
    test, identified at runtime by serial). The DUTs page is the only
    entity list whose rows are all observed-only; no provenance chip
    is rendered.
    """

    model_config = {"extra": "forbid"}

    serial: str
    part_number: str = ""
    lot_number: str = ""
    runs: int = 0
    passed: int = 0
    failed: int = 0
    last_run: datetime | None = None


def duts_from_runs() -> list[DUTRow]:
    """Distinct DUTs observed in run history, with per-DUT run counts.

    Groups by ``dut_serial``; the part number and lot number are
    aggregated via ``MAX`` (expected constant per serial — taking ``MAX``
    is just a SQL-idiomatic way to surface one value when the GROUP BY
    key uniquely determines the row).
    """
    from litmus.analysis.runs_query import RunsQuery

    sql = """
        SELECT
            dut_serial AS serial,
            MAX(dut_part_number) AS part_number,
            MAX(dut_lot_number) AS lot_number,
            COUNT(*) AS runs,
            COUNT(*) FILTER (WHERE outcome = 'passed') AS passed,
            COUNT(*) FILTER (WHERE outcome = 'failed') AS failed,
            MAX(started_at) AS last_run
        FROM runs
        WHERE dut_serial IS NOT NULL AND dut_serial <> ''
        GROUP BY dut_serial
        ORDER BY last_run DESC NULLS LAST
    """
    try:
        with RunsQuery() as q:
            rows = q._query_dicts(sql)  # noqa: SLF001 — direct SQL is the documented escape hatch
    except (ValueError, Exception):  # noqa: BLE001 — query layer can raise broadly
        return []

    return [
        DUTRow(
            serial=r["serial"],
            part_number=r.get("part_number") or "",
            lot_number=r.get("lot_number") or "",
            runs=r.get("runs", 0),
            passed=r.get("passed", 0),
            failed=r.get("failed", 0),
            last_run=r.get("last_run"),
        )
        for r in rows
        if r.get("serial")
    ]


def _instrument_id_usage_stats() -> dict[str, dict[str, Any]]:
    """Run-count stats keyed by instrument id observed in ``step_instruments_id``.

    The runs parquet stores per-step instrument arrays — one DuckDB row
    per run carries ``step_instruments_id`` as a list. UNNESTing gives
    one row per (run, instrument) pair; DISTINCT inside an outer count
    would over-credit if the same instrument is used in multiple steps,
    so the outer aggregation is grouped after UNNEST and counts distinct
    ``run_id`` per instrument.
    """
    from litmus.analysis.runs_query import RunsQuery

    sql = """
        SELECT
            inst_id AS value,
            COUNT(DISTINCT run_id) AS runs,
            COUNT(DISTINCT run_id) FILTER (WHERE outcome = 'passed') AS pass_count,
            COUNT(DISTINCT run_id) FILTER (WHERE outcome = 'failed') AS fail_count,
            MAX(started_at) AS last_run
        FROM (
            SELECT UNNEST(step_instruments_id) AS inst_id, run_id, outcome, started_at
            FROM runs
            WHERE step_instruments_id IS NOT NULL
        )
        WHERE inst_id IS NOT NULL AND inst_id <> ''
        GROUP BY inst_id
        ORDER BY runs DESC
    """
    try:
        with RunsQuery() as q:
            rows = q._query_dicts(sql)  # noqa: SLF001 — direct SQL is the documented escape hatch
    except (ValueError, Exception):  # noqa: BLE001 — query layer can raise broadly
        return {}

    return {
        r["value"]: {
            "runs": r.get("runs", 0),
            "passed": r.get("pass_count", 0),
            "failed": r.get("fail_count", 0),
            "last_run": r.get("last_run"),
        }
        for r in rows
        if r.get("value")
    }


class InstrumentAssetRow(BaseModel):
    """One row in the merged instrument-inventory list.

    Mirrors :class:`StationRow` / :class:`ProductRow` / :class:`FixtureRow`.
    ``identity`` is the joined manufacturer + model display string from
    the asset YAML (empty for observed-only rows).
    """

    model_config = {"extra": "forbid"}

    id: str
    driver: str = ""
    identity: str = ""
    serial: str = ""
    cal_due: str = ""
    cal_lab: str = ""
    runs: int = 0
    passed: int = 0
    failed: int = 0
    last_run: datetime | None = None
    provenance: Literal["configured", "observed_only"]


def instrument_assets_with_provenance() -> list[InstrumentAssetRow]:
    """Union of YAML-configured instrument assets and assets observed in runs.

    Observed side comes from :func:`_instrument_id_usage_stats` which
    UNNESTs the per-run ``step_instruments_id`` array — an instrument
    id that appears in any run but has no asset YAML is rendered as
    ``observed_only``.
    """
    from datetime import date as _date

    configured = {a.id: a for a in discover_instrument_assets()}
    usage = _instrument_id_usage_stats()

    rows: list[InstrumentAssetRow] = []
    for asset_id, asset in configured.items():
        stats = usage.get(asset_id, {})
        runs = stats.get("runs", 0)
        mfr = asset.info.manufacturer or ""
        model = asset.info.model or ""
        identity = f"{mfr} {model}".strip()

        cal_due = asset.calibration.due_date
        if cal_due:
            cal_str = cal_due.isoformat() if isinstance(cal_due, _date) else str(cal_due)
        else:
            cal_str = ""

        rows.append(
            InstrumentAssetRow(
                id=asset_id,
                driver=asset.driver or "",
                identity=identity,
                serial=str(asset.info.serial or ""),
                cal_due=cal_str,
                cal_lab=asset.calibration.lab or "",
                runs=runs,
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="configured",
            )
        )

    for asset_id, stats in usage.items():
        if asset_id in configured:
            continue
        rows.append(
            InstrumentAssetRow(
                id=asset_id,
                runs=stats.get("runs", 0),
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="observed_only",
            )
        )

    return rows


def load_instrument_asset_by_id(instrument_id: str):
    """Load a single instrument asset file by ID."""
    return store_get_instrument_asset(instrument_id)


def resolve_station_instrument_records(station_id: str) -> dict:
    """Resolve a station's instruments to InstrumentRecord objects."""
    config = store_get_station(station_id)
    if not config:
        return {}

    all_instrument_files: dict = {}
    for instruments_dir in get_instrument_paths():
        all_instrument_files.update(load_instrument_files(instruments_dir))

    return resolve_station_instruments(config, all_instrument_files)


def create_catalog_entry(
    instrument_type: str,
    name: str,
    description: str = "",
):
    """Create a new catalog entry in catalog/."""
    return store_create_catalog_entry(instrument_type, name, description)


# -----------------------------------------------------------------------------
# Test & Sequence Services
# -----------------------------------------------------------------------------


def discover_tests() -> list[dict]:
    """Discover available test directories.

    Legacy directory-grouped list — kept for callers that just want the
    set of folders (e.g. the Launch Test form's Test Path dropdown).
    Prefer :func:`walk_test_files` for the operator-UI list page which
    needs per-file structure.
    """
    tests = []
    search_paths = [Path.cwd() / "tests"]

    for tests_dir in search_paths:
        if not tests_dir.exists():
            continue
        for test_file in tests_dir.rglob("test_*.py"):
            test_dir = test_file.parent
            cwd = Path.cwd()
            relative = test_dir.relative_to(cwd) if test_dir.is_relative_to(cwd) else test_dir
            test_entry = {"path": str(relative), "name": test_dir.name}
            if test_entry not in tests:
                tests.append(test_entry)
    return tests


class TestFunctionRow(BaseModel):
    """One test function found by AST walk."""

    model_config = {"extra": "forbid"}

    name: str  # bare function name (``test_foo``)
    class_name: str | None = None  # parent ``TestX`` class if applicable
    markers: list[str] = Field(default_factory=list)  # decorator names sans ``pytest.mark.``
    parametrize_count: int = 0  # rough vector count from @parametrize / @litmus_sweeps
    has_sidecar_entry: bool = False


class TestModuleRow(BaseModel):
    """One test module — a ``test_*.py`` file with its tests, sidecar, markers."""

    model_config = {"extra": "forbid"}

    path: str  # relative to cwd
    directory: str  # parent directory relative to cwd
    name: str  # filename
    tests: list[TestFunctionRow] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    has_sidecar: bool = False
    parse_error: str | None = None  # set when AST parse fails


def _decorator_marker_name(node: Any) -> str | None:
    """Extract the marker name from a decorator node.

    Returns ``litmus_sweeps`` for ``@pytest.mark.litmus_sweeps(...)`` /
    ``@litmus.mark.litmus_sweeps(...)`` /
    plain ``@litmus_sweeps``. Returns ``None`` for decorators that
    don't look like marker references.
    """
    import ast

    target = node
    if isinstance(node, ast.Call):
        target = node.func
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _estimate_vector_count(node: Any) -> int:
    """Best-effort vector count from a parametrize / litmus_sweeps decorator.

    ``@pytest.mark.parametrize("x", [1, 2, 3])`` → 3.
    ``@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0]}])`` →
    sums the inner-list lengths for a rough cross-product upper bound.

    Returns ``0`` when the decorator shape isn't recognised — the
    overview is a sanity gauge, not a contract.
    """
    import ast

    if not isinstance(node, ast.Call) or not node.args:
        return 0
    name = _decorator_marker_name(node) or ""
    if name == "parametrize":
        # second arg is the values list
        if len(node.args) >= 2 and isinstance(node.args[1], (ast.List, ast.Tuple)):
            return len(node.args[1].elts)
        return 0
    if name in ("litmus_sweeps", "litmus_mocks", "litmus_characteristics"):
        # list-of-dicts form: each dict's values are lists; product across keys
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)):
            count = 0
            for elt in first.elts:
                if isinstance(elt, ast.Dict):
                    product = 1
                    for v in elt.values:
                        if isinstance(v, (ast.List, ast.Tuple)):
                            product *= max(len(v.elts), 1)
                    count += product
            return count
    return 0


def _function_to_row(node: Any, class_name: str | None, sidecar_names: set[str]) -> TestFunctionRow:
    import ast

    markers: list[str] = []
    vector_count = 0
    for d in node.decorator_list:  # type: ignore[attr-defined]
        marker_name = _decorator_marker_name(d)
        if marker_name and not isinstance(d, ast.Name):
            markers.append(marker_name)
        elif marker_name:
            markers.append(marker_name)
        if isinstance(d, ast.Call):
            vector_count += _estimate_vector_count(d)
    has_entry = node.name in sidecar_names or (
        class_name is not None and class_name in sidecar_names
    )
    return TestFunctionRow(
        name=node.name,
        class_name=class_name,
        markers=sorted(set(markers)),
        parametrize_count=vector_count,
        has_sidecar_entry=has_entry,
    )


def _sidecar_test_names(yaml_path: Path) -> set[str]:
    """Return the set of test/class names keyed in a sidecar's ``tests:`` block."""
    if not yaml_path.exists():
        return set()
    try:
        import yaml as _yaml

        data = _yaml.safe_load(yaml_path.read_text()) or {}
    except (OSError, ValueError):
        return set()
    tests_block = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(tests_block, dict):
        return set()
    return set(tests_block.keys())


def walk_test_module(py_path: Path) -> TestModuleRow:
    """AST-walk one test module, populate a :class:`TestModuleRow`.

    Picks up: top-level ``def test_*`` and ``class Test*`` (with their
    ``def test_*`` methods), decorators on each, and the sidecar's
    ``tests:`` block (if a sibling ``.yaml`` exists) to flag per-test
    sidecar coverage. Survives parse errors — the row gets a
    ``parse_error`` and an empty test list.
    """
    import ast

    cwd = Path.cwd()
    rel = py_path.relative_to(cwd) if py_path.is_relative_to(cwd) else py_path
    parent = rel.parent
    yaml_path = py_path.with_suffix(".yaml")
    sidecar_names = _sidecar_test_names(yaml_path)
    row = TestModuleRow(
        path=str(rel),
        directory=str(parent) or ".",
        name=py_path.name,
        has_sidecar=yaml_path.exists(),
    )

    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        row.parse_error = str(exc)
        return row

    classes: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            classes.append(node.name)
            for item in node.body:
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and item.name.startswith("test_"):
                    row.tests.append(_function_to_row(item, node.name, sidecar_names))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            row.tests.append(_function_to_row(node, None, sidecar_names))

    row.classes = classes
    return row


def walk_test_files(project_root: Path | None = None) -> list[TestModuleRow]:
    """AST-walk every ``test_*.py`` under ``tests/``, sorted by path."""
    root = project_root or Path.cwd()
    tests_dir = root / "tests"
    if not tests_dir.exists():
        return []
    return [walk_test_module(p) for p in sorted(tests_dir.rglob("test_*.py"))]


# -----------------------------------------------------------------------------
# Fixture Services
# -----------------------------------------------------------------------------


def discover_fixtures():
    """Discover fixture configurations from YAML files."""
    return store_list_fixtures()


class ProfileRow(BaseModel):
    """One row in the profiles list.

    Profiles are config-only today — the runs parquet doesn't carry the
    profile that was active for a given run, so the merged-with-badge
    pattern doesn't apply. Every row is configured-only by definition.
    """

    model_config = {"extra": "forbid"}

    name: str
    station_type: str = ""
    fixture: str = ""
    extends: str = ""
    facets: str = ""
    tests_count: int = 0


def discover_profiles() -> list[ProfileRow]:
    """List configured profiles from the project's ``litmus.yaml`` +
    ``profiles/*.yaml`` files. Per-profile fields are extracted into a
    typed display row.
    """
    project = load_project_config()
    rows: list[ProfileRow] = []
    for name, profile in (project.profiles or {}).items():
        facets_str = ", ".join(f"{k}={v}" for k, v in (profile.facets or {}).items())
        rows.append(
            ProfileRow(
                name=name,
                station_type=profile.station_type or "",
                fixture=profile.fixture or "",
                extends=profile.extends or "",
                facets=facets_str,
                tests_count=len(profile.tests or {}),
            )
        )
    return rows


def load_profile_config(name: str):
    """Load a single profile's ProfileConfig (already merged from
    inline + per-file sources by ``load_project_config``).
    """
    project = load_project_config()
    return (project.profiles or {}).get(name)


class FixtureRow(BaseModel):
    """One row in the merged fixtures list (YAML-configured + parquet-observed).

    Mirrors :class:`StationRow` / :class:`ProductRow`. The optional
    ``product`` label is the display name of the fixture's product
    family (resolved via ``discover_products``); observed-only rows
    leave it empty.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str = ""
    product: str = ""
    revision: str = ""
    connections: int = 0
    runs: int = 0
    passed: int = 0
    failed: int = 0
    last_run: datetime | None = None
    provenance: Literal["configured", "observed_only"]


def fixtures_with_provenance() -> list[FixtureRow]:
    """Union of YAML-configured fixtures and fixtures observed in runs.

    Two passes mirroring :func:`stations_with_provenance`. The display
    ``product`` label is resolved against ``discover_products`` so the
    operator sees the product name, not just the id.
    """
    configured = {f.id: f for f in discover_fixtures()}
    products = {p["id"]: p for p in discover_products()}
    usage = usage_stats_by("fixture_id")

    rows: list[FixtureRow] = []
    for fixture_id, fixture in configured.items():
        stats = usage.get(fixture_id, {})
        runs = stats.get("runs", 0)
        product_id = fixture.product_id or fixture.product_family or ""
        product_label = (products.get(product_id) or {}).get("name") or product_id
        rows.append(
            FixtureRow(
                id=fixture_id,
                name=fixture.name or "",
                product=product_label,
                revision=fixture.product_revision or "",
                connections=len(fixture.connections or {}),
                runs=runs,
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="configured",
            )
        )

    for fixture_id, stats in usage.items():
        if fixture_id in configured:
            continue
        rows.append(
            FixtureRow(
                id=fixture_id,
                runs=stats.get("runs", 0),
                passed=stats.get("passed", 0),
                failed=stats.get("failed", 0),
                last_run=stats.get("last_run"),
                provenance="observed_only",
            )
        )

    return rows


def load_fixture_config(fixture_id: str):
    """Load fixture configuration by ID."""
    return store_get_fixture(fixture_id)


def create_fixture(
    fixture_id: str,
    name: str,
    product_id: str = "",
    product_revision: str = "",
    description: str = "",
):
    """Create a new fixture configuration file."""
    return store_create_fixture(fixture_id, name, product_id, product_revision, description)


def save_fixture(_fixture_id: str, fixture_data: dict, connections_data: dict) -> None:
    """Save fixture configuration to YAML file."""
    fixture_dict = {**fixture_data, "connections": connections_data}
    fixture = FixtureConfig.model_validate(fixture_dict)
    store_save_fixture(fixture)


# -----------------------------------------------------------------------------
# Station Type Services
# -----------------------------------------------------------------------------


def save_station_type(station_type: StationType) -> None:
    """Save station type YAML."""
    store_save_station_type(station_type)


def load_station_type(type_id: str) -> StationType | None:
    """Load station type by ID."""
    return store_load_station_type(type_id)


def get_instrument_channels_from_library(instrument_type: str) -> list[str]:
    """Get channel names from a catalog entry matching the given type."""
    entry = store_get_catalog_entry(instrument_type)
    if entry:
        if entry.channels:
            return list(entry.channels.keys())
        return ["1"]
    return ["1"]


def get_fixtures_for_product(product_family: str):
    """Get all fixtures for a product family."""
    all_fixtures = discover_fixtures()
    return [f for f in all_fixtures if (f.product_family or "") == product_family]


def get_compatible_stations_for_fixture(fixture_id: str):
    """Get stations that have all instruments referenced by a fixture."""
    fixture = load_fixture_config(fixture_id)
    if not fixture:
        return []

    required_instruments = {c.instrument for c in fixture.connections.values() if c.instrument}

    compatible = []
    for station in discover_stations():
        if not station.instruments:
            continue
        station_instruments = set(station.instruments.keys())
        if required_instruments <= station_instruments:
            compatible.append(station)

    return compatible


# -----------------------------------------------------------------------------
# Test Run Services
# -----------------------------------------------------------------------------


def _results_backend() -> ParquetBackend:
    """Build a ParquetBackend using the configured project data_dir."""
    project = load_project_config()
    return ParquetBackend(data_dir=project.data_dir)


def get_recent_runs(
    limit: int = 10,
    *,
    offset: int = 0,
    include_incomplete: bool = False,
    phase: str | list[str] | None = None,
    product: str | list[str] | None = None,
    station: str | list[str] | None = None,
    lot: str | list[str] | None = None,
    outcome: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[RunSummary]:
    """Return one page of recent runs as RunSummary rows.

    Same RunsQuery → RunSummary adapter as :func:`list_all_runs`.
    ``offset`` paginates per the Quasar server-side contract:
    ``offset = (page - 1) * rows_per_page``. Filter args narrow
    by phase / product / station / lot / outcome / since / until.

    ``include_incomplete=True`` surfaces in-flight runs (no
    ``ended_at``) — UI list pages opt in so operators see what's
    running. Default ``False`` keeps the legacy "completed only"
    behavior for callers that expect aggregates.
    """
    return list_all_runs(
        limit=limit,
        offset=offset,
        include_incomplete=include_incomplete,
        phase=phase,
        product=product,
        station=station,
        lot=lot,
        outcome=outcome,
        since=since,
        until=until,
    )


def count_recent_runs(
    *,
    include_incomplete: bool = False,
    phase: str | list[str] | None = None,
    product: str | list[str] | None = None,
    station: str | list[str] | None = None,
    lot: str | list[str] | None = None,
    outcome: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Total run count under the same filters as ``get_recent_runs``.

    Used by ``/results/list`` to render the absolute "of N" in the
    Quasar pagination footer and the Total Runs stats card. Filter
    args mirror ``get_recent_runs`` so the count stays consistent
    with the paginated rows.
    """
    from litmus.analysis.runs_query import RunsQuery

    with RunsQuery() as q:
        return q.count(
            include_incomplete=include_incomplete,
            phase=phase,
            product=product,
            station=station,
            lot=lot,
            outcome=outcome,
            since=since,
            until=until,
        )


def get_runs_filter_options() -> dict[str, list[str]]:
    """Return distinct filter values for the /results filter strip.

    Keys: ``test_phase``, ``dut_part_number``, ``station_hostname``,
    ``dut_lot_number``. Each maps to the sorted distinct values
    present in the runs table — so the dropdowns only show options
    that have at least one matching run.

    Per-column failure isolation lives in
    :meth:`RunsQuery.distinct_filter_values` (a missing column
    yields ``[]`` for that one filter without affecting the
    others). Wholesale daemon failure (transient connection
    error after retries) raises here; the page handler catches
    and renders a blank-options state.
    """
    from litmus.analysis.runs_query import RunsQuery

    with RunsQuery() as q:
        return q.distinct_filter_values()


def _run_row_to_summary(row: Any) -> RunSummary:
    """Adapt a daemon ``RunRow`` to the legacy ``RunSummary`` UI shape.

    Centralizes the field-by-field copy so every callsite that swaps
    ``backend.get_run`` for ``RunsQuery.get`` doesn't reinvent it. The
    UI consumes ``RunSummary``; the daemon emits ``RunRow``; this is
    the only place that names every field.
    """
    return RunSummary(
        test_run_id=row.run_id or "",
        session_id=row.session_id,
        slot_id=row.slot_id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        dut_serial=row.dut_serial,
        dut_part_number=row.dut_part_number,
        product_id=row.product_id,
        station_id=row.station_id,
        station_name=row.station_name,
        station_hostname=row.station_hostname,
        fixture_id=row.fixture_id,
        test_phase=row.test_phase,
        project_name=row.project_name,
        operator=row.operator_id,
        outcome=row.outcome,
        total_measurements=row.num_measurements or 0,
        total_steps=row.num_steps or 0,
        file_path=row.file_path,
    )


def get_run_detail(run_id: str):
    """Return ``(run, steps, measurements)`` for a run.

    Resolves the run through the daemon's typed ``RunsQuery`` rather
    than the parquet backend. The backend's ``get_run`` walks the
    parquet glob directly, which can silently miss runs whose unified
    parquet only has step-summary rows (no measurement rows). The
    daemon's index aggregates the unified parquet by ``run_id``, so
    any run reachable from ``/results`` resolves here.

    ``run`` is adapted to ``RunSummary`` (the legacy UI shape the
    detail page expects). ``steps`` is the typed ``list[StepRow]``
    from the daemon's ``steps`` table. ``measurements`` is the flat
    measurement rows when the run recorded any, or ``[]`` for runs
    that produced only step-summary rows.
    """
    from litmus.analysis.runs_query import RunsQuery
    from litmus.analysis.steps_query import StepsQuery

    backend = _results_backend()
    with RunsQuery(_data_dir=backend.data_dir) as rq:
        run_row = rq.get(run_id)
    if run_row is None:
        return None, [], []

    run = _run_row_to_summary(run_row)

    measurements: list[dict] = (
        backend.get_measurements(run_id, _file=run.file_path) if run.file_path else []
    )

    with StepsQuery(_data_dir=backend.data_dir) as q:
        steps = q.list_for_run(run_id, include_incomplete=True)

    return run, steps, measurements


def load_artifact_ref(run_id: str, uri: str):
    """Resolve a measurement-output ref URI to its in-memory payload.

    UI-side counterpart of ``GET /api/runs/{run_id}/ref``: avoids an
    extra HTTP round-trip when the page is rendered in the same
    Python process as the API.
    """
    from pathlib import Path
    from uuid import uuid4

    from litmus.data.backends.parquet import load_ref

    backend = _results_backend()
    run = backend.get_run(run_id)
    if run is None or not run.file_path:
        raise FileNotFoundError(f"Run {run_id!r} has no parquet file")

    channel_store = None
    if uri.startswith("channel://"):
        from litmus.data.channels.store import ChannelStore

        channel_store = ChannelStore(backend.data_dir, uuid4())

    return load_ref(uri, parquet_path=Path(run.file_path), channel_store=channel_store)


def get_session_steps(session_id: str):
    """Return every step row across the session's sibling runs (typed)."""
    from litmus.analysis.steps_query import StepsQuery

    with StepsQuery() as q:
        return q.list_for_session(session_id)


def list_all_runs(
    limit: int = 100,
    *,
    offset: int = 0,
    include_incomplete: bool = False,
    phase: str | list[str] | None = None,
    product: str | list[str] | None = None,
    station: str | list[str] | None = None,
    lot: str | list[str] | None = None,
    outcome: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[RunSummary]:
    """List a page of runs for cross-run views (DUT history etc.).

    Sources from ``RunsQuery`` (typed) and adapts each ``RunRow``
    to the legacy ``RunSummary`` shape the UI's run-list page
    consumes. Same data, no parquet read.

    ``offset`` skips that many rows before returning ``limit``;
    used by Quasar's server-side pagination. Filter args
    (``phase`` / ``product`` / ``station`` / ``lot`` / ``outcome`` /
    ``since`` / ``until``) pass through to :meth:`RunsQuery.list_recent`.
    """
    from litmus.analysis.runs_query import RunsQuery

    with RunsQuery() as q:
        rows = q.list_recent(
            limit=limit,
            offset=offset,
            include_incomplete=include_incomplete,
            phase=phase,
            product=product,
            station=station,
            lot=lot,
            outcome=outcome,
            since=since,
            until=until,
        )
    return [_run_row_to_summary(r) for r in rows]


def usage_stats_by(field: str) -> dict[str, dict[str, Any]]:
    """Aggregate run stats grouped by a ``RunRow`` field.

    Used by the configuration list pages (Stations, Products,
    Fixtures, Instruments, Tests) to show "how busy is this entity"
    columns next to each row. Returns
    ``{value: {runs, passed, failed, last_run}}`` keyed by the
    grouped field's value (e.g. ``station_id``).

    Skips runs where the grouped field is null. Aggregation is pushed
    into SQL so the daemon returns one row per distinct value — safe
    regardless of total run count.
    """
    from litmus.analysis.runs_query import RunsQuery

    try:
        with RunsQuery() as q:
            rows = q.usage_stats(field)
    except ValueError:
        return {}

    return {
        r["value"]: {
            "runs": r.get("runs", 0),
            "passed": r.get("pass_count", 0),
            "failed": r.get("fail_count", 0),
            "errored": r.get("errored_count", 0),
            "last_run": r.get("last_run"),
        }
        for r in rows
        if r.get("value")
    }


def aggregate_run_stats(steps: list, measurements: list[dict]) -> dict[str, Any]:
    """Compute run-level stats from typed steps + flat measurement rows.

    ``steps`` is the typed ``list[StepRow]`` from the daemon's
    ``steps`` table — total_steps and failed_steps come straight
    from there so measurement-less runs render correct counts.
    Measurement counts come from the flat measurement parquet rows.

    Returned keys: total_measurements, passed_measurements,
    failed_measurements, total_steps, failed_steps.
    """
    total_measurements = len(measurements)
    failed_measurements = sum(1 for m in measurements if m.get("outcome") == "failed")
    passed_measurements = sum(1 for m in measurements if m.get("outcome") == "passed")

    total_steps = len(steps)
    failed_steps = sum(1 for s in steps if s.outcome == "failed")

    return {
        "total_measurements": total_measurements,
        "passed_measurements": passed_measurements,
        "failed_measurements": failed_measurements,
        "total_steps": total_steps,
        "failed_steps": failed_steps,
    }


# -----------------------------------------------------------------------------
# Event / Channel Store Services
# -----------------------------------------------------------------------------
#
# Thin in-process adapters over the shared query implementations in
# ``litmus.mcp.tools``. The HTTP API uses the same functions, so the
# operator UI and external clients see the same shapes.


def _resolve_data_dir() -> Path | None:
    """Project-configured results dir (or None for the platformdirs default)."""
    project = load_project_config()
    return Path(project.data_dir) if project.data_dir else None


def query_events(
    *,
    session_id: str | None = None,
    event_type: str | None = None,
    role: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Browse the event log with optional filters.

    Returns ``{"events": [...], "count": int}``. See
    :func:`litmus.mcp.tools.events_query` for filter semantics.
    """
    from litmus.mcp.tools import events_query

    return events_query(
        session_id,
        event_type,
        role,
        since,
        limit,
        data_dir=_resolve_data_dir(),
    )


def query_sessions() -> dict[str, Any]:
    """List known sessions (``session.started`` events).

    Returns ``{"sessions": [...], "count": int}``. Each entry is a
    SessionStarted event dict — ``session_id``, ``client``,
    ``occurred_at``, ``station_hostname``, ``operator_name``, etc.
    See :func:`format_session_label` for the operator-readable
    one-liner the filter widgets render.
    """
    from litmus.mcp.tools import sessions_query

    return sessions_query(data_dir=_resolve_data_dir())


def list_channels() -> dict[str, Any]:
    """Return the channel registry as ``{"channels": {channel_id: {...}}}``."""
    from litmus.mcp.tools import channels_list_query

    return channels_list_query(data_dir=_resolve_data_dir())


def list_channels_recent(*, last_n: int = 50) -> dict[str, Any]:
    """Return the channel registry plus recent samples per channel.

    Used by the operator UI to render sparkline cells and a live-
    updating "Latest" column. ``last_n`` caps the per-channel sample
    count returned (default 50 — enough for a sparkline trace).
    """
    from litmus.mcp.tools import channels_recent_query

    return channels_recent_query(last_n=last_n, data_dir=_resolve_data_dir())


def query_channel(
    channel_id: str,
    *,
    session_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    last_n: int | None = None,
    max_points: int | None = None,
) -> dict[str, Any]:
    """Query data for one channel with optional filters / LTTB decimation.

    Returns ``{"channel_id": str, "data": [row, ...]}`` where each row is
    a dict matching the channel's Arrow schema (timestamp + value /
    samples + source_method + session_id).
    """
    from litmus.mcp.tools import channels_query

    return channels_query(
        channel_id,
        session_id=session_id,
        since=since,
        until=until,
        last_n=last_n,
        max_points=max_points,
        data_dir=_resolve_data_dir(),
    )


# -----------------------------------------------------------------------------
# Yield Services
# -----------------------------------------------------------------------------
