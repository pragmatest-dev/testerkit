"""Catalog datasheet generation: structured YAML → formatted HTML/PDF."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment

from testerkit.models.catalog import InstrumentCatalogEntry
from testerkit.store import load_catalog_entry

# SI prefixes for formatting
_SI_PREFIXES = [
    (1e-15, "f"),
    (1e-12, "p"),
    (1e-9, "n"),
    (1e-6, "\u00b5"),
    (1e-3, "m"),
    (1, ""),
    (1e3, "k"),
    (1e6, "M"),
    (1e9, "G"),
    (1e12, "T"),
]

# Units that use SI prefixes (Hz, V, A, W, Ohm, s, etc.)
_SI_UNITS = {"Hz", "V", "A", "W", "Ohm", "s", "S", "F", "H", "m", "g", "B"}


def fmt_si(value: float | int | None, unit: str = "") -> str:
    """Format a numeric value with SI prefix.

    Examples:
        fmt_si(1000000, "Hz") → "1 MHz"
        fmt_si(0.001, "V") → "1 mV"
        fmt_si(54000000000, "Hz") → "54 GHz"
        fmt_si(250000, "Hz") → "250 kHz"
    """
    if value is None:
        return "—"

    # Only apply SI prefixes for known SI-compatible unit
    base_unit = (unit[:-1] if unit.endswith("s") else unit) if unit else ""
    if base_unit not in _SI_UNITS and unit not in _SI_UNITS:
        if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
            return f"{int(value)} {unit}".strip()
        return f"{value} {unit}".strip()

    abs_val = abs(value)
    if abs_val == 0:
        return f"0 {unit}".strip()

    # Find best prefix
    for threshold, prefix in _SI_PREFIXES:
        if abs_val < threshold * 1000:
            scaled = value / threshold
            # Clean up floating point: use int if close
            if abs(scaled - round(scaled)) < 1e-9:
                scaled = int(round(scaled))
            elif abs(scaled * 10 - round(scaled * 10)) < 1e-6:
                scaled = round(scaled, 1)
            elif abs(scaled * 100 - round(scaled * 100)) < 1e-4:
                scaled = round(scaled, 2)
            else:
                scaled = round(scaled, 3)
            return f"{scaled} {prefix}{unit}".strip()

    # Fallback for very large values
    return f"{value} {unit}".strip()


def fmt_accuracy(acc: dict[str, Any] | None) -> str:
    """Format an AccuracySpec dict as a readable string.

    Examples:
        {"pct_reading": 0.05, "pct_range": 0.01} → "±0.05% rdg + 0.01% rng"
        {"absolute": 0.6, "unit": "dB"} → "±0.6 dB"
    """
    if not acc:
        return "—"

    parts = []
    if acc.get("pct_reading") is not None:
        parts.append(f"{acc['pct_reading']}% rdg")
    if acc.get("pct_range") is not None:
        parts.append(f"{acc['pct_range']}% rng")
    if acc.get("absolute") is not None:
        unit = acc.get("unit") or ""
        parts.append(f"{acc['absolute']} {unit}".strip())

    if not parts:
        return "—"

    return "\u00b1(" + " + ".join(parts) + ")"


def fmt_range(rng: dict[str, Any] | None, use_si: bool = True) -> str:
    """Format a RangeSpec dict as a readable string.

    Examples:
        {"min": 0.1, "max": 1000, "unit": "V"} → "0.1 – 1000 V"
        {"min": 250000, "max": 20000000000, "unit": "Hz"} → "250 kHz – 20 GHz"
    """
    if not rng:
        return "—"

    lo = rng.get("min")
    hi = rng.get("max")
    unit = rng.get("unit", "")

    if lo is None and hi is None:
        return "—"

    if use_si and unit:
        if lo is not None and hi is not None:
            return f"{fmt_si(lo, unit)} – {fmt_si(hi, unit)}"
        if lo is not None:
            return f"≥ {fmt_si(lo, unit)}"
        return f"≤ {fmt_si(hi, unit)}"

    if lo is not None and hi is not None:
        return f"{_fmt_num(lo)} – {_fmt_num(hi)} {unit}".strip()
    if lo is not None:
        return f"≥ {_fmt_num(lo)} {unit}".strip()
    return f"≤ {_fmt_num(hi)} {unit}".strip()


def _fmt_num(v: float | int | None) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def fmt_when_value(v: Any, key: str = "") -> str:
    """Format a single when-clause value as a human-readable label.

    If key is provided, infers SI unit from the key name for numeric values.
    """
    if v is None:
        return "—"
    if isinstance(v, dict):
        # Point value: {value: X} or {value: X, unit: Y}
        if "value" in v:
            unit = v.get("unit") or (_infer_unit(key) if key else "")
            val = v["value"]
            # Try parsing string-encoded numbers (e.g. "2.4e9" from YAML)
            if isinstance(val, str):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    return f"{val} {unit}".strip() if unit else str(val)
            if isinstance(val, (int, float)) and unit:
                return fmt_si(val, unit)
            return _fmt_num(val) if isinstance(val, (int, float)) else str(val)
        # Range dict: {min, max, unit}
        # Also handle string-encoded min/max
        rng = dict(v)
        for k in ("min", "max"):
            if isinstance(rng.get(k), str):
                try:
                    rng[k] = float(rng[k])
                except (ValueError, TypeError):
                    pass
        return fmt_range(rng)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, (int, float)):
        unit = _infer_unit(key) if key else ""
        if unit:
            return fmt_si(v, unit)
        return _fmt_num(v)
    return str(v)


# Key name patterns → SI unit for when-clause scalar values
_KEY_UNIT_MAP = {
    "frequency": "Hz",
    "offset_frequency": "Hz",
    "power": "dBm",
    "voltage": "V",
    "current": "A",
    "resistance": "Ohm",
    "capacitance": "F",
    "time": "s",
    "delay": "s",
    "rate": "Hz",
}


def _infer_unit(key: str) -> str:
    """Infer SI unit from a when-clause key name."""
    k = key.lower()
    for pattern, unit in _KEY_UNIT_MAP.items():
        if pattern in k:
            return unit
    return ""


def fmt_key(k: str) -> str:
    """Format a when-clause key as a human-readable column/row header."""
    return k.replace("_", " ").title()


def fmt_resolution(res: dict[str, Any] | None) -> str:
    """Format a ResolutionSpec dict as a readable string."""
    if not res:
        return "—"
    if res.get("digits") is not None:
        return f"{res['digits']} digits"
    if res.get("value") is not None:
        unit = res.get("unit", "")
        if unit:
            return fmt_si(res["value"], unit)
        return _fmt_num(res["value"])
    return "—"


def _fmt_value(v: Any, unit: str = "") -> str:
    """Format a value with optional SI prefix for numeric types."""
    if isinstance(v, (int, float)) and unit:
        return fmt_si(v, unit)
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    return f"{v} {unit}".strip() if unit else str(v)


def fmt_attr(attr: dict[str, Any] | None) -> str:
    """Format an Attribute dict as a readable string with SI formatting."""
    if not attr:
        return "—"
    if attr.get("value") is not None:
        return _fmt_value(attr["value"], attr.get("unit", ""))
    if attr.get("range"):
        return fmt_range(attr["range"])
    if attr.get("options"):
        return ", ".join(str(o) for o in attr["options"])
    return "—"


def _fmt_attr_band_value(band: dict[str, Any], parent_unit: str = "") -> str:
    """Format an attribute spec band's value, inheriting unit from parent."""
    v = band.get("value")
    if v is None:
        if band.get("range"):
            return fmt_range(band["range"])
        return "—"
    return _fmt_value(v, band.get("unit") or parent_unit)


def _when_keys(bands: list[dict[str, Any]]) -> list[str]:
    """Get the union of all when-clause keys across bands."""
    keys: dict[str, int] = {}
    for band in bands:
        for k in band.get("when", {}):
            if k not in keys:
                keys[k] = 0
            keys[k] += 1
    return list(keys.keys())


def _unique_values(bands: list[dict[str, Any]], key: str) -> list[Any]:
    """Get unique values for a when-clause key, preserving order."""
    seen = []
    for band in bands:
        v = band.get("when", {}).get(key)
        if v is not None and v not in seen:
            seen.append(v)
    return seen


def _fmt_output_cell(band: dict[str, Any], field: str) -> str:
    """Format the output value of a band for its field."""
    val = band.get(field)
    if field == "range":
        return fmt_range(val)
    if field == "accuracy":
        return fmt_accuracy(val)
    if field == "resolution":
        return fmt_resolution(val)
    return "—"


def _field_title(field: str, sig_name: str) -> str:
    """Generate a table title from output field and signal name."""
    sig_label = sig_name.replace("_", " ").title()
    field_label = field.replace("_", " ").title()
    return f"{sig_label} {field_label}"


def _has_output_field(band: dict[str, Any], field: str) -> bool:
    """Check if a band has a non-empty output field."""
    val = band.get(field)
    if val is None:
        return False
    if isinstance(val, dict) and all(v is None for v in val.values()):
        return False
    return True


def _cluster_by_key_overlap(
    bands: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Cluster bands whose when-key sets overlap (subset/superset).

    Bands with disjoint key sets go into separate clusters.
    """
    # Sub-group by exact key signature
    sig_groups: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for band in bands:
        ks = frozenset(band.get("when", {}).keys())
        sig_groups[ks].append(band)

    # Merge groups with subset/superset relationships
    sigs = list(sig_groups.keys())
    clusters: list[set[int]] = [{i} for i in range(len(sigs))]

    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            if sigs[i] & sigs[j]:  # any key overlap → merge
                # Find which clusters i and j belong to
                ci = next(c for c in clusters if i in c)
                cj = next(c for c in clusters if j in c)
                if ci is not cj:
                    ci.update(cj)
                    clusters.remove(cj)

    result = []
    for cluster in clusters:
        merged = []
        for idx in cluster:
            merged.extend(sig_groups[sigs[idx]])
        result.append(merged)
    return result


# Use 2D matrix layout if grid is at least this fraction populated
_GRID_DENSITY_THRESHOLD = 0.5


def _emit_table(
    tables: list[dict[str, Any]],
    bands: list[dict[str, Any]],
    keys: list[str],
    present_fields: list[str],
    sig_name: str,
) -> None:
    """Build and append the appropriate table type for a band cluster."""
    # 2D matrix: exactly 2 keys, 1 output field, dense grid
    if len(keys) == 2 and len(present_fields) == 1:
        v0 = _unique_values(bands, keys[0])
        v1 = _unique_values(bands, keys[1])
        grid_size = len(v0) * len(v1)
        if grid_size > 0 and len(bands) / grid_size >= _GRID_DENSITY_THRESHOLD:

            def cell_fn(b, f=present_fields[0]):
                return _fmt_output_cell(b, f)

            tbl = _build_2d_generic(bands, keys, sig_name, cell_fn)
            tbl["title"] = f"{fmt_key(sig_name)} {fmt_key(present_fields[0])}"
            tables.append(tbl)
            return

    # Single output field with 1 key → 1D table
    if len(keys) == 1 and len(present_fields) == 1:
        key = keys[0]
        field = present_fields[0]
        rows = []
        for band in bands:
            label = fmt_when_value(band.get("when", {}).get(key), key)
            rows.append({"label": label, "value": _fmt_output_cell(band, field)})
        tables.append(
            {
                "kind": "1d",
                "title": _field_title(field, sig_name),
                "row_key": fmt_key(key),
                "value_label": fmt_key(field),
                "rows": rows,
            }
        )
        return

    # Multi-column: condition columns + output field columns
    tables.append(
        _build_multi_col_table(
            bands,
            keys,
            sig_name,
            output_fields=present_fields,
        )
    )


def build_signal_render(sig_name: str, sig: dict[str, Any]) -> dict[str, Any]:
    """Build render structures for a signal's spec bands.

    Groups bands by their when-key signature and produces one table per group,
    with columns for all output fields present in that group.

    Returns a dict with:
      - headline: {range, accuracy, resolution} formatted strings for the main row
      - tables: list of render table dicts (2d or multi_col)
    """
    headline = {
        "range": fmt_range(sig.get("range")),
        "accuracy": fmt_accuracy(sig.get("accuracy")),
        "resolution": fmt_resolution(sig.get("resolution")),
    }

    bands = sig.get("bands") or []
    if not bands:
        return {"headline": headline, "tables": []}

    # Split unconditional bands (no when) from conditional bands
    unconditional = []
    conditional = []
    for band in bands:
        if not band.get("when"):
            unconditional.append(band)
        else:
            conditional.append(band)

    # Unconditional — merge into headline
    for band in unconditional:
        for field in ("range", "accuracy", "resolution"):
            if _has_output_field(band, field):
                headline[field] = _fmt_output_cell(band, field)

    tables = []
    if not conditional:
        return {"headline": headline, "tables": tables}

    # Group by output field signature first
    output_groups: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for band in conditional:
        out_sig = frozenset(
            f for f in ("range", "accuracy", "resolution") if _has_output_field(band, f)
        )
        output_groups[out_sig].append(band)

    for out_sig, bands in output_groups.items():
        present_fields = [f for f in ("range", "accuracy", "resolution") if f in out_sig]
        if not present_fields:
            continue

        # Within each output group, cluster bands by when-key overlap.
        # Merge groups with subset/superset key relationships; keep disjoint groups separate.
        for cluster in _cluster_by_key_overlap(bands):
            keys = _when_keys(cluster)
            _emit_table(tables, cluster, keys, present_fields, sig_name)

    return {"headline": headline, "tables": tables}


def _hashable(v: Any) -> Any:
    """Make a value hashable for use as dict key."""
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    if isinstance(v, list):
        return tuple(v)
    return v


def build_attr_render(attr_name: str, attr: dict[str, Any]) -> dict[str, Any]:
    """Build render structures for an attribute's spec bands.

    Returns a dict with:
      - headline: formatted string for the main value
      - tables: list of 1d/2d/grouped render table dicts
    """
    headline = fmt_attr(attr)
    parent_unit = attr.get("unit", "") or ""

    bands = attr.get("bands") or []
    if not bands:
        return {"headline": headline, "tables": []}

    # All attribute bands share the same output type: value
    # Build a cell formatter that inherits parent unit
    def _cell(band: dict[str, Any]) -> str:
        return _fmt_attr_band_value(band, parent_unit)

    tables = _build_tables_from_bands(bands, attr_name, "value", _cell)

    return {"headline": headline, "tables": tables}


def _build_tables_from_bands(
    bands: list[dict[str, Any]],
    name: str,
    value_label: str,
    cell_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    """Generic table builder for spec bands (used by both signals and attrs)."""
    tables = []

    # Cluster bands by when-key overlap so disjoint groups get separate tables
    for cluster in _cluster_by_key_overlap(bands):
        keys = _when_keys(cluster)
        ndim = len(keys)

        if ndim == 1:
            key = keys[0]
            rows = []
            for band in cluster:
                label = fmt_when_value(band.get("when", {}).get(key), key)
                rows.append({"label": label, "value": cell_fn(band)})
            tables.append(
                {
                    "kind": "1d",
                    "title": fmt_key(name),
                    "row_key": fmt_key(key),
                    "value_label": fmt_key(value_label),
                    "rows": rows,
                }
            )
        elif ndim == 2:
            v0 = _unique_values(cluster, keys[0])
            v1 = _unique_values(cluster, keys[1])
            grid_size = len(v0) * len(v1)
            if grid_size > 0 and len(cluster) / grid_size >= _GRID_DENSITY_THRESHOLD:
                tables.append(_build_2d_generic(cluster, keys, name, cell_fn))
            else:
                tables.append(
                    _build_multi_col_table(
                        cluster,
                        keys,
                        name,
                        value_label=value_label,
                        cell_fn=cell_fn,
                    )
                )
        else:
            tables.append(
                _build_multi_col_table(
                    cluster,
                    keys,
                    name,
                    value_label=value_label,
                    cell_fn=cell_fn,
                )
            )

    return tables


def _build_2d_generic(
    bands: list[dict[str, Any]],
    keys: list[str],
    name: str,
    cell_fn: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Build a 2D matrix using a generic cell formatter."""
    k0, k1 = keys[0], keys[1]
    v0 = _unique_values(bands, k0)
    v1 = _unique_values(bands, k1)

    if len(v0) >= len(v1):
        row_key, col_key = k0, k1
        row_vals, col_vals = v0, v1
    else:
        row_key, col_key = k1, k0
        row_vals, col_vals = v1, v0

    lookup: dict[tuple[Any, Any], str] = {}
    for band in bands:
        rv = band.get("when", {}).get(row_key)
        cv = band.get("when", {}).get(col_key)
        lookup[(_hashable(rv), _hashable(cv))] = cell_fn(band)

    return {
        "kind": "2d",
        "title": fmt_key(name),
        "row_key": fmt_key(row_key),
        "col_key": fmt_key(col_key),
        "col_headers": [fmt_when_value(cv, col_key) for cv in col_vals],
        "rows": [
            {
                "label": fmt_when_value(rv, row_key),
                "cells": [lookup.get((_hashable(rv), _hashable(cv)), "—") for cv in col_vals],
            }
            for rv in row_vals
        ],
    }


def _build_multi_col_table(
    bands: list[dict[str, Any]],
    keys: list[str],
    name: str,
    output_fields: list[str] | None = None,
    value_label: str | None = None,
    cell_fn: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Build a multi-column table: condition columns + value columns.

    Supports two modes:
    - Signal mode: output_fields=["range", "accuracy"] → multiple value columns
    - Attribute mode: value_label + cell_fn → single value column (legacy)
    """
    col_keys = [fmt_key(k) for k in keys]

    if output_fields is not None:
        # Signal mode: multiple output field columns
        value_cols = [fmt_key(f) for f in output_fields]
        rows = []
        for band in bands:
            condition_cells = [fmt_when_value(band.get("when", {}).get(k), k) for k in keys]
            values = [_fmt_output_cell(band, f) for f in output_fields]
            rows.append({"conditions": condition_cells, "cells": values})
        return {
            "kind": "multi_col",
            "title": fmt_key(name),
            "col_keys": col_keys,
            "value_cols": value_cols,
            "rows": rows,
        }
    else:
        # Attribute mode: single value column
        rows = []
        for band in bands:
            condition_cells = [fmt_when_value(band.get("when", {}).get(k), k) for k in keys]
            rows.append(
                {
                    "conditions": condition_cells,
                    "cells": [cell_fn(band) if cell_fn else ""],
                }
            )
        return {
            "kind": "multi_col",
            "title": fmt_key(name),
            "col_keys": col_keys,
            "value_cols": [fmt_key(value_label or "Value")],
            "rows": rows,
        }


def _visible_fields(items: dict[str, dict[str, Any]], fields: list[str]) -> dict[str, bool]:
    """Determine which optional fields have at least one non-None value across items."""
    return {f: any(item.get(f) is not None for item in items.values()) for f in fields}


def preprocess_capabilities(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preprocess capabilities for template rendering.

    Adds 'signal_renders', 'attr_renders', and 'visible_*' dicts to each capability.
    """
    for cap in caps:
        sig_renders = {}
        for sig_name, sig in (cap.get("signals") or {}).items():
            sig_renders[sig_name] = build_signal_render(sig_name, sig)
        cap["signal_renders"] = sig_renders

        attr_renders = {}
        for attr_name, attr in (cap.get("attributes") or {}).items():
            attr_renders[attr_name] = build_attr_render(attr_name, attr)
        cap["attr_renders"] = attr_renders

        # Which optional columns have data?
        if cap.get("signals"):
            cap["visible_signals"] = _visible_fields(cap["signals"], ["qualifier"])
        if cap.get("controls"):
            cap["visible_controls"] = _visible_fields(cap["controls"], ["resolution", "default"])
        if cap.get("attributes"):
            # For constant attrs (no spec bands), check qualifier
            const_attrs = {
                k: v
                for k, v in cap["attributes"].items()
                if not attr_renders.get(k, {}).get("tables")
            }
            cap["visible_attrs"] = _visible_fields(const_attrs, ["qualifier"])
    return caps


@dataclass
class DatasheetSummary:
    capability_count: int
    channel_count: int
    type: str


@dataclass
class DatasheetData:
    entry: InstrumentCatalogEntry
    summary: DatasheetSummary
    # Render-time state per capability (signal_renders, attr_renders, visible_*).
    # Kept separate from the model because these fields don't belong on the entity.
    cap_renders: list[dict[str, Any]]


def load_datasheet_data(path: Path) -> DatasheetData:
    """Load a catalog YAML and organize it for template rendering."""
    entry = load_catalog_entry(path, catalog_dir=path.parent)

    # Preprocess capabilities into render-time dicts (augmented with signal/attr renders).
    caps_as_dicts = [c.model_dump() for c in entry.capabilities]
    cap_renders = preprocess_capabilities(caps_as_dicts)

    return DatasheetData(
        entry=entry,
        summary=DatasheetSummary(
            capability_count=len(entry.capabilities),
            channel_count=len(entry.channels),
            type=entry.type,
        ),
        cap_renders=cap_renders,
    )


def _find_variant_files(base_path: Path) -> list[Path]:
    """Find variant YAML files that inherit from this base entry."""
    from testerkit.store import _find_catalog_variants

    return _find_catalog_variants(base_path)


def _render_datasheet(
    data: DatasheetData,
    output: Path,
    fmt: str,
    related: list[dict[str, str]] | None = None,
) -> Path:
    """Render a single datasheet to file.

    Args:
        data: Output of load_datasheet_data().
        output: Output file path.
        fmt: "html" or "pdf".
        related: Optional list of {label, name, model, href} for related links.
                 Links to missing files are hidden client-side via JS.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    template_path = Path(__file__).parent / "templates" / "datasheet.html"
    template_str = template_path.read_text()

    env = Environment(autoescape=True)
    env.filters["fmt_si"] = lambda v, u="": fmt_si(v, u)
    env.filters["fmt_accuracy"] = fmt_accuracy
    env.filters["fmt_range"] = fmt_range
    env.filters["fmt_num"] = _fmt_num
    env.filters["fmt_resolution"] = fmt_resolution
    env.filters["fmt_attr"] = fmt_attr
    env.filters["fmt_key"] = fmt_key

    tmpl = env.from_string(template_str)
    html = tmpl.render(
        data=data.entry,
        cap_renders=data.cap_renders,
        summary=data.summary,
        related=related or [],
    )

    if fmt == "pdf":
        try:
            from weasyprint import HTML
        except ImportError:
            raise ImportError(
                "PDF reports require weasyprint. Install with: pip install 'testerkit[pdf]'"
            )
        HTML(string=html).write_pdf(str(output))
    else:
        output.write_text(html)

    return output


def generate_datasheet(
    path: Path,
    output: Path | None = None,
    fmt: str = "html",
) -> Path:
    """Generate a formatted datasheet from a catalog YAML file.

    If the entry is a base with variants, also generates a datasheet for
    each variant file and includes links in the base datasheet.

    Args:
        path: Path to catalog YAML file.
        output: Output file path. Defaults to <model>.html in current dir.
        fmt: Output format — "html" or "pdf".

    Returns:
        Path to generated base file.
    """
    data = load_datasheet_data(path)
    entry = data.entry
    ext = "pdf" if fmt == "pdf" else "html"

    if output is None:
        output = Path(f"{entry.id}.{ext}")
    else:
        output = Path(output)

    # Check for variants
    variant_files = _find_variant_files(path)
    variant_links: list[dict[str, str]] = []

    for vpath in variant_files:
        vdata = load_datasheet_data(vpath)
        ventry = vdata.entry
        vout = output.parent / f"{ventry.id}.{ext}"
        variant_links.append(
            {
                "label": "Variant",
                "name": ventry.name or ventry.id or "",
                "model": ventry.model,
                "href": vout.name,
            }
        )

    base_link = {
        "label": "Base",
        "name": entry.name or entry.id or "",
        "model": entry.model,
        "href": output.name,
    }

    # Render base first so variants can link back to it
    _render_datasheet(data, output, fmt, related=variant_links)

    # Render variants with link back to base
    for vpath in variant_files:
        vdata = load_datasheet_data(vpath)
        vout = output.parent / f"{vdata.entry.id}.{ext}"
        _render_datasheet(vdata, vout, fmt, related=[base_link])

    return output
