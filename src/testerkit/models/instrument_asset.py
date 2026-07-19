"""Instrument asset file schema.

Schema for ``instruments/*.yaml`` asset files — per-device identity and
calibration, referencing a catalog entry.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from testerkit.models.instrument import CalibrationInfo, InstrumentInfo


class InstrumentAssetFile(BaseModel):
    """Schema for instruments/*.yaml asset files (per-device identity + calibration)."""

    model_config = {"extra": "forbid"}

    id: str
    protocol: str = "visa"
    driver: str | None = None
    resource: str | None = None
    catalog_ref: str | None = None
    info: InstrumentInfo = Field(default_factory=InstrumentInfo)
    calibration: CalibrationInfo = Field(default_factory=CalibrationInfo)
