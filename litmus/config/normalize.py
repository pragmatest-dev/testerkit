"""Normalization utilities for configuration values.

Instrument type uses the InstrumentType enum as canonical vocabulary.
We normalize on write (lowercase + alias resolution) and soft-warn if
the type doesn't appear in InstrumentType or any catalog entry.
"""

from __future__ import annotations

from litmus.config.enums import InstrumentType

# Aliases map old/informal names to canonical InstrumentType values
_TYPE_ALIASES: dict[str, str] = {
    "digital_multimeter": InstrumentType.DMM,
    "scope": InstrumentType.OSCILLOSCOPE,
    "power_supply": InstrumentType.PSU,
    "dc_power_supply": InstrumentType.PSU,
    "fgen": InstrumentType.FUNCTION_GENERATOR,
    "eload": InstrumentType.ELECTRONIC_LOAD,
    "rf_siggen": InstrumentType.RF_SIGNAL_GENERATOR,
    "signal_generator": InstrumentType.RF_SIGNAL_GENERATOR,
    "picoammeter": InstrumentType.ELECTROMETER,
    "optical_power_meter": InstrumentType.POWER_METER,
}

_KNOWN_TYPES = {t.value for t in InstrumentType}


def normalize_instrument_type(raw: str) -> str:
    """Lowercase, strip, and resolve aliases to canonical type."""
    normalized = raw.strip().lower()
    return _TYPE_ALIASES.get(normalized, normalized)


def check_instrument_types(
    instruments: dict[str, dict],
) -> tuple[dict[str, dict], list[str]]:
    """Normalize instrument types and warn about unknown ones.

    Args:
        instruments: Dict of instrument name → config dict.
            Each config must have a ``type`` key.

    Returns:
        (instruments with normalized types, list of warning strings)
    """
    warnings: list[str] = []

    for name, config in instruments.items():
        if "type" not in config:
            continue
        original = config["type"]
        config["type"] = normalize_instrument_type(original)
        if config["type"] != original:
            warnings.append(
                f"instruments.{name}: Normalized type "
                f"'{original}' → '{config['type']}'"
            )
        if config["type"] not in _KNOWN_TYPES:
            # Also check catalog entries for custom types
            known = _known_catalog_types()
            if known and config["type"] not in known:
                warnings.append(
                    f"instruments.{name}: Type '{config['type']}' "
                    "not in InstrumentType enum or any catalog entry — "
                    "this is fine for custom types, but check for typos."
                )

    return instruments, warnings


def _known_catalog_types() -> set[str]:
    """Collect instrument types present in loaded catalog entries."""
    try:
        from litmus.store import find_catalog_dirs, load_catalog_from_directory

        types: set[str] = set()
        for cat_dir in find_catalog_dirs():
            for entry in load_catalog_from_directory(cat_dir).values():
                if entry.type:
                    types.add(entry.type.lower())
        return types | _KNOWN_TYPES
    except (ImportError, OSError):
        return _KNOWN_TYPES
