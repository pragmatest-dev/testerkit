"""Instrument-specific logic: resolving station instruments, finding directories.

Persistence (load/save) lives in litmus.store.
"""

from __future__ import annotations

from pathlib import Path

from litmus.models.instrument import CalibrationInfo, InstrumentInfo, InstrumentRecord
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.station import StationConfig


def resolve_station_instruments(
    station_config: StationConfig,
    instrument_files: dict[str, InstrumentAssetFile],
) -> dict[str, InstrumentRecord]:
    """Resolve station instruments to full records.

    Takes a StationConfig model and instrument asset files, and produces
    complete InstrumentRecord objects for each instrument role.
    """
    records: dict[str, InstrumentRecord] = {}
    instruments_mapping = station_config.instruments or {}

    for role, inst_config in instruments_mapping.items():
        resource = inst_config.resource or ""
        protocol = "visa"
        driver = inst_config.driver
        info = InstrumentInfo()
        calibration = CalibrationInfo()
        catalog_ref = inst_config.catalog_ref

        asset = instrument_files.get(role)
        if asset:
            protocol = asset.protocol or protocol
            driver = driver or asset.driver
            info = asset.info
            calibration = asset.calibration
            catalog_ref = catalog_ref or asset.catalog_ref

        records[role] = InstrumentRecord(
            role=role,
            instrument_id=role,
            resource=resource,
            protocol=protocol,
            info=info,
            calibration=calibration,
            driver=driver,
            catalog_ref=catalog_ref,
        )

    return records


def _find_directory(dirname: str, start_path: Path | None = None) -> Path | None:
    """Find a named directory by searching up from start path."""
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()
    while current != current.parent:
        candidate = current / dirname
        if candidate.is_dir():
            return candidate
        current = current.parent

    return None


def find_instruments_dir(start_path: Path | None = None) -> Path | None:
    """Find instruments/ directory by searching up from start path."""
    return _find_directory("instruments", start_path)


def find_stations_dir(start_path: Path | None = None) -> Path | None:
    """Find stations/ directory by searching up from start path."""
    return _find_directory("stations", start_path)
