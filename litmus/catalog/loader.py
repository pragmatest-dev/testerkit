"""YAML loading for instrument catalog entries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from litmus.catalog.models import InstrumentCatalogEntry
from litmus.config.models import (
    AccuracySpec,
    ChannelTopology,
    CompareMode,
    ConnectorType,
    Direction,
    FunctionCapability,
    GroundTopology,
    MeasurementFunction,
    ParameterRole,
    RangeSpec,
    ResolutionSpec,
    SignalParameter,
    SpecBand,
    TerminalRole,
)


def load_catalog_entry(
    path: Path, catalog_dir: Path | None = None
) -> InstrumentCatalogEntry:
    """Load a single catalog entry from a YAML file, resolving inheritance.

    If the entry has a ``base`` field, the base entry's YAML is loaded and
    merged at section level before Pydantic parsing.  Sections present in the
    variant completely replace the base's version (no deep merge).

    Args:
        path: Path to the catalog YAML file.
        catalog_dir: Directory to search for base entries.  Defaults to
            the parent of *path*.

    Returns:
        InstrumentCatalogEntry with parsed capabilities.
    """
    if catalog_dir is None:
        catalog_dir = path.parent
    data = _load_with_inheritance(path, catalog_dir, seen=set(), depth=0)
    return _build_entry(data, path)


_MAX_INHERIT_DEPTH = 5


def _load_with_inheritance(
    path: Path,
    catalog_dir: Path,
    seen: set[str],
    depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base entries.

    Merge semantics are *section-level override*: if the variant provides
    ``capabilities:`` or ``catalog_entry.channels:``, those replace the
    base's version entirely.  Header fields (manufacturer, type,
    name, description) are inherited when absent in the variant.

    Args:
        path: YAML file to load.
        catalog_dir: Directory containing catalog YAML files.
        seen: Set of entry IDs already in the chain (cycle detection).
        depth: Current recursion depth.

    Returns:
        Merged raw dict ready for ``_build_entry``.

    Raises:
        ValueError: On circular inheritance or missing base.
    """
    if depth > _MAX_INHERIT_DEPTH:
        raise ValueError(
            f"Catalog inheritance depth exceeds {_MAX_INHERIT_DEPTH} for {path}"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    entry_data = data.get("catalog_entry", {})
    entry_id = entry_data.get("id", path.stem)
    base_ref = entry_data.get("base")

    if not base_ref:
        return data

    # Cycle detection
    if entry_id in seen:
        raise ValueError(
            f"Circular catalog inheritance: {entry_id!r} already in chain {seen}"
        )
    seen.add(entry_id)

    # Locate base file
    base_path = catalog_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        raise ValueError(
            f"Base catalog entry {base_ref!r} not found "
            f"(referenced by {entry_id!r} in {path})"
        )

    base_data = _load_with_inheritance(base_path, catalog_dir, seen, depth + 1)
    return _merge_catalog_data(base_data, data)


def _merge_catalog_data(
    base: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    """Merge base and variant YAML dicts with section-level override.

    Rules:
    - ``capabilities:`` in variant replaces base's entirely.
    - ``catalog_entry.channels:`` in variant replaces base's entirely.
    - Header fields (manufacturer, type, name, description)
      are inherited from base when absent in variant.
    - ``id``, ``model``, ``base`` always come from the variant.
    """
    base_entry = dict(base.get("catalog_entry", {}))
    variant_entry = dict(variant.get("catalog_entry", {}))

    merged_entry: dict[str, Any] = {}

    # Inherit header fields from base
    for key in ("manufacturer", "type", "name", "description"):
        if key in base_entry:
            merged_entry[key] = base_entry[key]

    # Variant overrides everything it provides
    merged_entry.update(variant_entry)

    # Channels: variant replaces if present, else inherit base
    if "channels" not in variant_entry and "channels" in base_entry:
        merged_entry["channels"] = base_entry["channels"]

    merged: dict[str, Any] = {"catalog_entry": merged_entry}

    # Capabilities: variant replaces if present, else inherit base
    if "capabilities" in variant:
        merged["capabilities"] = variant["capabilities"]
    elif "capabilities" in base:
        merged["capabilities"] = base["capabilities"]

    return merged


def _build_entry(data: dict[str, Any], path: Path) -> InstrumentCatalogEntry:
    """Build an InstrumentCatalogEntry from merged raw YAML data."""
    entry_data = data.get("catalog_entry", {})
    capabilities = _parse_capabilities(data.get("capabilities", []))
    channels = _parse_channels(entry_data.get("channels", {}))

    return InstrumentCatalogEntry(
        id=entry_data.get("id", path.stem),
        manufacturer=entry_data.get("manufacturer", ""),
        model=str(entry_data.get("model", "")),
        name=entry_data.get("name", path.stem),
        description=entry_data.get("description"),
        type=entry_data.get("type", ""),
        base=entry_data.get("base"),
        channels=channels,
        capabilities=capabilities,
    )


def load_catalog_from_directory(catalog_dir: Path) -> dict[str, InstrumentCatalogEntry]:
    """Load all catalog entries from a directory.

    Args:
        catalog_dir: Path to catalog/ directory.

    Returns:
        Dict mapping catalog entry ID to InstrumentCatalogEntry.
    """
    if not catalog_dir.exists():
        return {}

    entries: dict[str, InstrumentCatalogEntry] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            entry = load_catalog_entry(path, catalog_dir=catalog_dir)
            entries[entry.id] = entry
        except Exception:
            continue

    return entries


def find_catalog_dirs() -> list[Path]:
    """Find catalog directories by searching standard locations."""
    dirs = []
    for candidate in [Path.cwd() / "catalog", Path.cwd() / "demo" / "catalog"]:
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def resolve_catalog_ref(catalog_ref: str) -> InstrumentCatalogEntry | None:
    """Resolve a catalog reference ID to a catalog entry.

    Searches all catalog directories for a matching entry.

    Args:
        catalog_ref: Catalog entry ID (e.g., "keysight_34461a").

    Returns:
        InstrumentCatalogEntry or None if not found.
    """
    for cat_dir in find_catalog_dirs():
        # Try direct filename match first
        direct_path = cat_dir / f"{catalog_ref}.yaml"
        if direct_path.exists():
            try:
                return load_catalog_entry(direct_path, catalog_dir=cat_dir)
            except Exception:
                pass

        # Fallback: search all files for matching ID
        for path in cat_dir.glob("*.yaml"):
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
                if entry.id == catalog_ref:
                    return entry
            except Exception:
                continue

    return None


def _parse_channels(raw: Any) -> dict[str, ChannelTopology]:
    """Parse channels from YAML into structured dict.

    Supports:
    - Structured dict: {"1": {terminals: [hi, lo], ...}} → pass through
    - Legacy list: ["1", "2"] → default topology for each
    - Legacy range string: "1:3" → default topology for each expanded name
    - None/empty → empty dict
    """
    if not raw:
        return {}

    # Already a structured dict
    if isinstance(raw, dict):
        result: dict[str, ChannelTopology] = {}
        for key, value in raw.items():
            key_str = str(key)
            if isinstance(value, dict):
                result[key_str] = _parse_channel_topology(value)
            else:
                # Bare key with no topology data
                result[key_str] = ChannelTopology()
        return result

    # Legacy formats: convert to dict with default topology
    from litmus.utils.ranges import expand_range

    names = expand_range(raw)
    return {name: ChannelTopology() for name in names}


def _parse_channel_topology(data: dict[str, Any]) -> ChannelTopology:
    """Parse a single ChannelTopology from dict data."""
    terminals = []
    for t in data.get("terminals", ["hi", "lo"]):
        try:
            terminals.append(TerminalRole(t))
        except ValueError:
            pass

    connector = None
    if "connector" in data:
        try:
            connector = ConnectorType(data["connector"])
        except ValueError:
            pass

    ground = GroundTopology.SHARED
    if "ground" in data:
        try:
            ground = GroundTopology(data["ground"])
        except ValueError:
            pass

    return ChannelTopology(
        label=data.get("label"),
        terminals=terminals or [TerminalRole.HI, TerminalRole.LO],
        connector=connector,
        ground=ground,
    )


def _parse_capabilities(caps_data: list[dict[str, Any]]) -> list[FunctionCapability]:
    """Parse capabilities list from YAML data."""
    capabilities = []
    for cap_data in caps_data:
        try:
            cap = _parse_capability(cap_data)
            capabilities.append(cap)
        except (ValueError, KeyError):
            continue
    return capabilities


def _parse_capability(data: dict[str, Any]) -> FunctionCapability:
    """Parse a single FunctionCapability from YAML data."""
    function = MeasurementFunction(data["function"])
    direction = Direction(data["direction"])

    parameters: dict[str, SignalParameter] = {}
    for param_name, param_data in data.get("parameters", {}).items():
        parameters[param_name] = _parse_signal_parameter(param_data)

    channels = _normalize_channels(data.get("channels"))
    readback = bool(data.get("readback", False))

    return FunctionCapability(
        function=function,
        direction=direction,
        parameters=parameters,
        channels=channels,
        modes=data.get("modes", []),
        readback=readback,
    )


def _normalize_channels(raw: Any) -> list[str]:
    """Normalize channel data from YAML to list[str]."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(ch) for ch in raw]
    if isinstance(raw, str):
        from litmus.utils.ranges import expand_range
        return expand_range(raw)
    if isinstance(raw, int):
        return [str(i + 1) for i in range(raw)]
    return []


def _parse_signal_parameter(data: dict[str, Any]) -> SignalParameter:
    """Parse a SignalParameter from YAML data."""
    range_spec = None
    if "range" in data:
        r = data["range"]
        range_spec = RangeSpec(
            min=r.get("min"),
            max=r.get("max"),
            units=r.get("units", ""),
        )

    accuracy_spec = None
    if "accuracy" in data:
        a = data["accuracy"]
        accuracy_spec = AccuracySpec(
            pct_reading=a.get("pct_reading"),
            pct_range=a.get("pct_range"),
            absolute=a.get("absolute"),
        )

    resolution_spec = None
    if "resolution" in data:
        r = data["resolution"]
        resolution_spec = ResolutionSpec(
            bits=r.get("bits"),
            digits=r.get("digits"),
            value=r.get("value"),
            units=r.get("units"),
        )

    role = ParameterRole.CONTROLLABLE
    if "role" in data:
        role = ParameterRole(data["role"])

    specs = None
    if "specs" in data:
        specs = [_parse_spec_band(s) for s in data["specs"]]

    compare = None
    if "compare" in data:
        compare = CompareMode(data["compare"])

    return SignalParameter(
        range=range_spec,
        accuracy=accuracy_spec,
        resolution=resolution_spec,
        value=data.get("value"),
        units=data.get("units"),
        role=role,
        specs=specs,
        compare=compare,
    )


def _parse_spec_band(data: dict[str, Any]) -> SpecBand:
    """Parse a single SpecBand from YAML data."""
    when: dict[str, RangeSpec] = {}
    for key, val in data.get("when", {}).items():
        when[key] = RangeSpec(
            min=val.get("min"), max=val.get("max"), units=val.get("units", "")
        )

    accuracy = None
    if "accuracy" in data:
        a = data["accuracy"]
        accuracy = AccuracySpec(
            pct_reading=a.get("pct_reading"),
            pct_range=a.get("pct_range"),
            absolute=a.get("absolute"),
        )

    resolution = None
    if "resolution" in data:
        r = data["resolution"]
        resolution = ResolutionSpec(
            bits=r.get("bits"),
            digits=r.get("digits"),
            value=r.get("value"),
            units=r.get("units"),
        )

    return SpecBand(
        when=when,
        value=data.get("value"),
        accuracy=accuracy,
        resolution=resolution,
    )
