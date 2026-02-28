"""Instrument and station configuration loader.

Loads instrument definitions from YAML files in instruments/ directory
and station configurations from stations/ directory.

Folder structure:
    instruments/                    # Instrument inventory (metadata + calibration)
      keithley_dmm_001.yaml
      keysight_psu_bench.yaml

    stations/                       # Station assignments (which instruments, where)
      bench_01.yaml
      production_line_01.yaml
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from litmus.instruments.models import CalibrationInfo, InstrumentInfo, InstrumentRecord
from litmus.loaders import load_instrument_asset
from litmus.schemas import InstrumentAssetFile


def load_instrument_file(path: Path) -> InstrumentAssetFile:
    """Load a single instrument YAML file.

    Args:
        path: Path to instrument YAML file

    Returns:
        Validated InstrumentAssetFile model.
    """
    return load_instrument_asset(path)


def load_instrument_files(instruments_dir: str | Path) -> dict[str, InstrumentAssetFile]:
    """Load all instrument files from a directory.

    Args:
        instruments_dir: Path to instruments/ directory

    Returns:
        Dict mapping instrument ID to validated InstrumentAssetFile model.
    """
    instruments_dir = Path(instruments_dir)
    if not instruments_dir.exists():
        return {}

    instruments: dict[str, InstrumentAssetFile] = {}
    for path in instruments_dir.glob("*.yaml"):
        try:
            asset = load_instrument_asset(path)
            instruments[asset.id] = asset
        except Exception:
            # Skip invalid files
            pass

    return instruments


def load_station_file(path: Path) -> dict[str, Any]:
    """Load a station configuration file.

    Args:
        path: Path to station YAML file

    Returns:
        Dict with station configuration (validated via StationFile model).
    """
    from litmus.loaders import load_station

    station = load_station(path)
    return station.model_dump()


def resolve_station_instruments(
    station_config: dict[str, Any],
    instrument_files: dict[str, InstrumentAssetFile],
) -> dict[str, InstrumentRecord]:
    """Resolve station instrument references to full records.

    Takes a station config (with role->instrument_id mappings and resources)
    and instrument files, and produces complete InstrumentRecord objects
    for each role.

    Args:
        station_config: Station configuration dict with 'instruments' and 'resources'
        instrument_files: Dict mapping instrument ID to InstrumentAssetFile

    Returns:
        Dict mapping role to InstrumentRecord
    """
    records: dict[str, InstrumentRecord] = {}

    instruments_mapping = station_config.get("instruments", {})
    resources_mapping = station_config.get("resources", {})

    for role, instrument_ref in instruments_mapping.items():
        if isinstance(instrument_ref, dict):
            # Inline instrument config in station file
            inst_config = instrument_ref
            instrument_id = inst_config.get("id", role)
            resource = inst_config.get("resource", "")
            protocol = inst_config.get("protocol", "visa")
            driver = inst_config.get("driver")

            # Parse inline info/calibration
            info_data = inst_config.get("info", inst_config.get("identity", {}))
            cal_data = inst_config.get("calibration", {})
            info = InstrumentInfo.model_validate(info_data) if info_data else InstrumentInfo()
            calibration = (
                CalibrationInfo.model_validate(cal_data) if cal_data else CalibrationInfo()
            )
            catalog_ref = inst_config.get("catalog_ref")
        else:
            # Reference to instrument file by ID
            instrument_id = instrument_ref
            resource = resources_mapping.get(instrument_id, "")

            # Look up instrument file
            asset = instrument_files.get(instrument_id)
            if asset:
                protocol = asset.protocol
                driver = asset.driver
                info = asset.info
                calibration = asset.calibration
                catalog_ref = asset.catalog_ref
            else:
                protocol = "visa"
                driver = None
                info = InstrumentInfo()
                calibration = CalibrationInfo()
                catalog_ref = None

        records[role] = InstrumentRecord(
            role=role,
            instrument_id=instrument_id,
            resource=resource,
            protocol=protocol,
            info=info,
            calibration=calibration,
            driver=driver,
            catalog_ref=catalog_ref,
        )

    return records


def find_instruments_dir(start_path: Path | None = None) -> Path | None:
    """Find instruments/ directory by searching up from start path.

    Args:
        start_path: Starting directory (defaults to cwd)

    Returns:
        Path to instruments/ directory, or None if not found
    """
    if start_path is None:
        start_path = Path.cwd()

    # Check current directory and parents
    current = start_path.resolve()
    while current != current.parent:
        instruments_dir = current / "instruments"
        if instruments_dir.is_dir():
            return instruments_dir
        current = current.parent

    return None


def find_stations_dir(start_path: Path | None = None) -> Path | None:
    """Find stations/ directory by searching up from start path.

    Args:
        start_path: Starting directory (defaults to cwd)

    Returns:
        Path to stations/ directory, or None if not found
    """
    if start_path is None:
        start_path = Path.cwd()

    # Check current directory and parents
    current = start_path.resolve()
    while current != current.parent:
        stations_dir = current / "stations"
        if stations_dir.is_dir():
            return stations_dir
        current = current.parent

    return None
