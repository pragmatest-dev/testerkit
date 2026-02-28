"""YAML loading for instrument catalog entries."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from litmus.catalog.models import InstrumentCatalogEntry


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

    # Locate base file: try sibling of variant first, then catalog_dir
    base_path = path.parent / f"{base_ref}.yaml"
    if not base_path.exists():
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
    """Merge base and variant YAML dicts.

    Rules:
    - Header fields (manufacturer, type, name, description)
      are inherited from base when absent in variant.
    - ``id``, ``model``, ``base`` always come from the variant.
    - ``channels``, ``attributes``, ``interfaces`` inherit from base
      when absent in variant; variant replaces if present.
    - ``capabilities`` are merged per-capability: matched by
      ``(function, direction)``.  Variant capability dicts are
      deep-merged into matching base capabilities (variant specs
      are appended, variant params override).  Unmatched base
      capabilities pass through; unmatched variant capabilities
      are appended.

    All fields live under ``catalog_entry:``.
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

    # Section-level inherit: variant replaces if present, else inherit base
    for section in ("channels", "attributes", "interfaces"):
        if section not in variant_entry and section in base_entry:
            merged_entry[section] = base_entry[section]

    # Per-capability merge
    base_caps = base_entry.get("capabilities") or []
    variant_caps = variant_entry.get("capabilities") or []

    if variant_caps and base_caps:
        merged_entry["capabilities"] = _merge_capabilities(base_caps, variant_caps)
    elif not variant_caps and base_caps:
        merged_entry["capabilities"] = base_caps

    return {"catalog_entry": merged_entry}


def _cap_key(cap: dict[str, Any]) -> tuple[str, str]:
    """Identity key for matching capabilities: (function, direction)."""
    return (cap.get("function", ""), cap.get("direction", ""))


def _merge_capabilities(
    base_caps: list[dict[str, Any]], variant_caps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge variant capabilities into base capabilities by (function, direction).

    For each variant capability, find the matching base capability and
    deep-merge param dicts (signals, conditions, controls, attributes):
    variant specs are appended to base specs, variant params override base.
    Unmatched base caps pass through; unmatched variant caps are appended.
    """
    # Index base caps by key; handle duplicates by keeping list order
    merged: list[dict[str, Any]] = [copy.deepcopy(c) for c in base_caps]
    base_index: dict[tuple[str, str], int] = {}
    for i, cap in enumerate(base_caps):
        key = _cap_key(cap)
        if key not in base_index:
            base_index[key] = i

    for vcap in variant_caps:
        key = _cap_key(vcap)
        if key in base_index:
            _deep_merge_cap(merged[base_index[key]], vcap)
        else:
            merged.append(copy.deepcopy(vcap))

    return merged


def _deep_merge_cap(base_cap: dict[str, Any], variant_cap: dict[str, Any]) -> None:
    """Deep-merge a variant capability into a base capability in place.

    For each param dict section (signals, conditions, controls, attributes):
    - If param exists in both, append variant specs to base specs
    - If param exists only in variant, add it to base
    """
    for section in ("signals", "conditions", "controls", "attributes"):
        v_section = variant_cap.get(section)
        if not v_section or not isinstance(v_section, dict):
            continue

        if section not in base_cap or not isinstance(base_cap.get(section), dict):
            base_cap[section] = copy.deepcopy(v_section)
            continue

        b_section = base_cap[section]
        for param_name, v_param in v_section.items():
            if param_name not in b_section:
                b_section[param_name] = copy.deepcopy(v_param)
                continue

            # Merge specs: append variant specs to base specs
            if isinstance(v_param, dict) and "specs" in v_param:
                b_param = b_section[param_name]
                if not isinstance(b_param, dict):
                    b_section[param_name] = copy.deepcopy(v_param)
                    continue
                b_specs = b_param.get("specs", [])
                b_param["specs"] = b_specs + copy.deepcopy(v_param["specs"])
                # Merge non-spec fields from variant (e.g. range, value overrides)
                for k, v in v_param.items():
                    if k != "specs":
                        b_param[k] = copy.deepcopy(v)
            else:
                b_section[param_name] = copy.deepcopy(v_param)


def _build_entry(data: dict[str, Any], path: Path) -> InstrumentCatalogEntry:
    """Build an InstrumentCatalogEntry from merged raw YAML data.

    All fields must live under ``catalog_entry:``.  A top-level
    ``capabilities:`` key is rejected to catch un-migrated files.
    """
    if "capabilities" in data and data["capabilities"]:
        raise ValueError(
            f"{path.name}: top-level 'capabilities:' is no longer supported — "
            f"move capabilities under 'catalog_entry:'"
        )

    entry_data = data.get("catalog_entry", {})
    parsed: dict[str, Any] = dict(entry_data)

    # Derived defaults for optional identity fields
    parsed.setdefault("id", path.stem)
    if not parsed.get("name"):
        mfr = parsed.get("manufacturer", "")
        model = parsed.get("model", "")
        parsed["name"] = f"{mfr} {model}".strip() or path.stem
    parsed["model"] = str(parsed.get("model", ""))

    return InstrumentCatalogEntry.model_validate(parsed)


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
    for path in sorted(catalog_dir.rglob("*.yaml")):
        if path.name.startswith("_") or ".variants." in path.name:
            continue
        try:
            entry = load_catalog_entry(path, catalog_dir=catalog_dir)
            entries[entry.id] = entry
        except Exception as exc:
            warnings.warn(
                f"catalog: failed to load {path.name}: {exc}",
                stacklevel=2,
            )
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
            except Exception as exc:
                warnings.warn(
                    f"catalog: failed to load {direct_path.name}: {exc}",
                    stacklevel=2,
                )
                return None

        # Fallback: search all files for matching ID
        for path in cat_dir.glob("*.yaml"):
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
                if entry.id == catalog_ref:
                    return entry
            except Exception as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
                continue

    return None


def find_by_model(
    manufacturer: str, model: str
) -> InstrumentCatalogEntry | None:
    """Find a catalog entry by manufacturer and model name.

    Searches all catalog directories for an entry whose manufacturer and
    model match (case-insensitive).  Used by ``litmus init --discover``
    to look up instrument type for automatic role naming.

    Args:
        manufacturer: Manufacturer name (e.g., "Keysight").
        model: Model number (e.g., "34461A").

    Returns:
        Matching InstrumentCatalogEntry, or None.
    """
    mfr_lower = manufacturer.lower()
    model_lower = model.lower()

    for cat_dir in find_catalog_dirs():
        for path in sorted(cat_dir.rglob("*.yaml")):
            if path.name.startswith("_") or ".variants." in path.name:
                continue
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
            except Exception:
                continue
            if (
                entry.manufacturer
                and entry.manufacturer.lower() == mfr_lower
                and entry.model
                and entry.model.lower() == model_lower
            ):
                return entry

    return None




