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

Instrument file format:
    id: keithley_dmm_001
    protocol: visa
    driver: pymeasure.instruments.keithley.Keithley2000

    info:                           # Verified against device at runtime
      manufacturer: Keithley
      model: "2000"
      serial: "ABC123"

    calibration:                    # Config-only, tracked by organization
      due_date: 2025-06-15
      last_cal: 2024-06-15
      certificate: "CAL-2024-001"
      lab: "Acme Calibration Services"

Station file format:
    station:
      id: bench_01
      name: "Engineering Bench 1"
      location: "Lab 101"

    instruments:                    # Role -> instrument ID
      dmm: keithley_dmm_001
      psu: keysight_psu_bench

    resources:                      # Where each instrument is connected at THIS station
      keithley_dmm_001: "GPIB::16::INSTR"
      keysight_psu_bench: "GPIB::17::INSTR"
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from litmus.instruments.models import CalibrationInfo, InstrumentInfo, InstrumentRecord


def _parse_date(value: Any) -> date | None:
    """Parse date from various formats."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        # Try ISO format first
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
        # Try common formats
        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"]:
            try:
                from datetime import datetime

                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
    return None


def load_instrument_info(data: dict[str, Any]) -> InstrumentInfo:
    """Load InstrumentInfo from config dict.

    Args:
        data: Dict with optional keys: manufacturer, model, serial, firmware

    Returns:
        InstrumentInfo instance
    """
    return InstrumentInfo(
        manufacturer=data.get("manufacturer"),
        model=str(data.get("model")) if data.get("model") is not None else None,
        serial=str(data.get("serial")) if data.get("serial") is not None else None,
        firmware=str(data.get("firmware")) if data.get("firmware") is not None else None,
    )


def load_calibration_info(data: dict[str, Any]) -> CalibrationInfo:
    """Load CalibrationInfo from config dict.

    Args:
        data: Dict with optional keys: due_date, last_cal, certificate, lab

    Returns:
        CalibrationInfo instance
    """
    return CalibrationInfo(
        due_date=_parse_date(data.get("due_date")),
        last_cal=_parse_date(data.get("last_cal")),
        certificate=data.get("certificate"),
        lab=data.get("lab"),
    )


def load_instrument_file(path: Path) -> dict[str, Any]:
    """Load a single instrument YAML file.

    Args:
        path: Path to instrument YAML file

    Returns:
        Dict with instrument configuration including parsed info and calibration
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    # Parse info and calibration sections
    info_data = data.get("info", {})
    cal_data = data.get("calibration", {})

    result = dict(data)
    result["_info"] = load_instrument_info(info_data)
    result["_calibration"] = load_calibration_info(cal_data)
    result["_path"] = str(path)

    return result


def load_instrument_files(instruments_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Load all instrument files from a directory.

    Args:
        instruments_dir: Path to instruments/ directory

    Returns:
        Dict mapping instrument ID to configuration dict
    """
    instruments_dir = Path(instruments_dir)
    if not instruments_dir.exists():
        return {}

    instruments: dict[str, dict[str, Any]] = {}
    for path in instruments_dir.glob("*.yaml"):
        try:
            data = load_instrument_file(path)
            inst_id = data.get("id", path.stem)
            instruments[inst_id] = data
        except Exception:
            # Skip invalid files
            pass

    return instruments


def load_station_file(path: Path) -> dict[str, Any]:
    """Load a station configuration file.

    Args:
        path: Path to station YAML file

    Returns:
        Dict with station configuration
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    return data


def resolve_station_instruments(
    station_config: dict[str, Any],
    instrument_files: dict[str, dict[str, Any]],
) -> dict[str, InstrumentRecord]:
    """Resolve station instrument references to full records.

    Takes a station config (with role->instrument_id mappings and resources)
    and instrument files, and produces complete InstrumentRecord objects
    for each role.

    Args:
        station_config: Station configuration dict with 'instruments' and 'resources'
        instrument_files: Dict mapping instrument ID to instrument config

    Returns:
        Dict mapping role to InstrumentRecord

    Example:
        station_config = {
            "instruments": {"dmm": "keithley_dmm_001"},
            "resources": {"keithley_dmm_001": "GPIB::16::INSTR"}
        }
        instrument_files = {"keithley_dmm_001": {...}}

        records = resolve_station_instruments(station_config, instrument_files)
        # records["dmm"] = InstrumentRecord(role="dmm", instrument_id="keithley_dmm_001", ...)
    """
    records: dict[str, InstrumentRecord] = {}

    instruments_mapping = station_config.get("instruments", {})
    resources_mapping = station_config.get("resources", {})

    for role, instrument_id in instruments_mapping.items():
        # Handle both new format (role -> instrument_id string) and
        # legacy format (role -> {driver, resource, ...} dict)
        if isinstance(instrument_id, dict):
            # Legacy format - inline instrument config
            inst_config = instrument_id
            instrument_id = inst_config.get("id", role)
            resource = inst_config.get("resource", "")
            protocol = inst_config.get("protocol", "visa")
            driver = inst_config.get("driver")

            # Parse inline info/calibration
            info_data = inst_config.get("info", inst_config.get("identity", {}))
            cal_data = inst_config.get("calibration", {})
            info = load_instrument_info(info_data)
            calibration = load_calibration_info(cal_data)
        else:
            # New format - reference to instrument file
            resource = resources_mapping.get(instrument_id, "")

            # Look up instrument file
            inst_config = instrument_files.get(instrument_id, {})
            protocol = inst_config.get("protocol", "visa")
            driver = inst_config.get("driver")
            info = inst_config.get("_info", InstrumentInfo())
            calibration = inst_config.get("_calibration", CalibrationInfo())

        catalog_ref = inst_config.get("catalog_ref") if isinstance(inst_config, dict) else None

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
