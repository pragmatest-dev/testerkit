"""Normalization utilities for configuration values.

Instrument type is a freeform string — the catalog is the living registry
of known types. We normalize on write (lowercase) and soft-warn if the
type doesn't appear in any catalog entry.
"""

from __future__ import annotations


def normalize_instrument_type(raw: str) -> str:
    """Lowercase and strip whitespace."""
    return raw.strip().lower()


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
    known = _known_catalog_types()

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
        if known and config["type"] not in known:
            warnings.append(
                f"instruments.{name}: Type '{config['type']}' "
                "not found in any catalog entry — this is fine "
                "for new instrument types, but check for typos."
            )

    return instruments, warnings


def _known_catalog_types() -> set[str]:
    """Collect instrument types present in loaded catalog entries."""
    try:
        from litmus.catalog.loader import find_catalog_dirs, load_catalog_from_directory

        types: set[str] = set()
        for cat_dir in find_catalog_dirs():
            for entry in load_catalog_from_directory(cat_dir).values():
                if entry.type:
                    types.add(entry.type.lower())
        return types
    except Exception:
        return set()
