"""Shared helpers for cloud transports."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litmus.models.project import OutputConfig


def build_blob_name(config: OutputConfig, local_path: Path) -> str:
    """Build the remote blob/key name from prefix + local filename."""
    prefix = config.extras.get("prefix", "")
    return f"{prefix}{local_path.name}"


def require_extra(config: OutputConfig, key: str, transport_name: str) -> str:
    """Extract a required key from config.extras with a clear error message."""
    try:
        return config.extras[key]
    except KeyError:
        raise ValueError(
            f"{transport_name} transport requires '{key}' in config (set it in litmus.yaml outputs)"
        ) from None
