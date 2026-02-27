"""Catalog datasheet generation: structured YAML → formatted HTML/PDF."""

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment

from litmus.catalog.loader import load_catalog_entry

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


def fmt_si(value: float | int | None, units: str = "") -> str:
    """Format a numeric value with SI prefix.

    Examples:
        fmt_si(1000000, "Hz") → "1 MHz"
        fmt_si(0.001, "V") → "1 mV"
        fmt_si(54000000000, "Hz") → "54 GHz"
        fmt_si(250000, "Hz") → "250 kHz"
    """
    if value is None:
        return "—"

    # Only apply SI prefixes for known SI-compatible units
    base_unit = units.rstrip("s") if units else ""  # strip plural
    if base_unit not in _SI_UNITS and units not in _SI_UNITS:
        if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
            return f"{int(value)} {units}".strip()
        return f"{value} {units}".strip()

    abs_val = abs(value)
    if abs_val == 0:
        return f"0 {units}".strip()

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
            return f"{scaled} {prefix}{units}".strip()

    # Fallback for very large values
    return f"{value} {units}".strip()


def fmt_accuracy(acc: dict[str, Any] | None) -> str:
    """Format an AccuracySpec dict as a readable string.

    Examples:
        {"pct_reading": 0.05, "pct_range": 0.01} → "±0.05% rdg + 0.01% rng"
        {"absolute": 0.6, "units": "dB"} → "±0.6 dB"
    """
    if not acc:
        return "—"

    parts = []
    if acc.get("pct_reading") is not None:
        parts.append(f"{acc['pct_reading']}% rdg")
    if acc.get("pct_range") is not None:
        parts.append(f"{acc['pct_range']}% rng")
    if acc.get("absolute") is not None:
        unit = acc.get("units") or ""
        parts.append(f"{acc['absolute']} {unit}".strip())

    if not parts:
        return "—"

    return "\u00b1(" + " + ".join(parts) + ")"


def fmt_range(rng: dict[str, Any] | None, use_si: bool = True) -> str:
    """Format a RangeSpec dict as a readable string.

    Examples:
        {"min": 0.1, "max": 1000, "units": "V"} → "0.1 – 1000 V"
        {"min": 250000, "max": 20000000000, "units": "Hz"} → "250 kHz – 20 GHz"
    """
    if not rng:
        return "—"

    lo = rng.get("min")
    hi = rng.get("max")
    units = rng.get("units", "")

    if lo is None and hi is None:
        return "—"

    if use_si and units:
        lo_str = fmt_si(lo, units) if lo is not None else "—"
        hi_str = fmt_si(hi, units) if hi is not None else "—"
        if lo is not None and hi is not None:
            # Show units only on the high value
            return f"{fmt_si(lo, units)} – {fmt_si(hi, units)}"
        return f"{lo_str} – {hi_str}"

    if lo is not None and hi is not None:
        return f"{_fmt_num(lo)} – {_fmt_num(hi)} {units}".strip()
    if lo is not None:
        return f"≥ {_fmt_num(lo)} {units}".strip()
    return f"≤ {_fmt_num(hi)} {units}".strip()


def _fmt_num(v: float | int | None) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def fmt_when_value(v: Any, key: str = "") -> str:
    """Format a single when-clause value as a human-readable label.

    If key is provided, infers SI units from the key name for numeric values.
    """
    if isinstance(v, dict):
        # Range dict: {min, max, units}
        return fmt_range(v)
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, (int, float)):
        units = _infer_units(key) if key else ""
        if units:
            return fmt_si(v, units)
        return _fmt_num(v)
    return str(v)


# Key name patterns → SI units for when-clause scalar values
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


def _infer_units(key: str) -> str:
    """Infer SI units from a when-clause key name."""
    k = key.lower()
    for pattern, units in _KEY_UNIT_MAP.items():
        if pattern in k:
            return units
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
        units = res.get("units", "")
        if units:
            return fmt_si(res["value"], units)
        return _fmt_num(res["value"])
    return "—"


def fmt_attr(attr: dict[str, Any] | None) -> str:
    """Format an Attribute dict as a readable string with SI formatting."""
    if not attr:
        return "—"
    if attr.get("value") is not None:
        units = attr.get("units", "")
        v = attr["value"]
        if isinstance(v, (int, float)) and units:
            return fmt_si(v, units)
        if isinstance(v, (int, float)):
            return _fmt_num(v)
        return f"{v} {units}".strip() if units else str(v)
    if attr.get("range"):
        return fmt_range(attr["range"])
    if attr.get("options"):
        return ", ".join(str(o) for o in attr["options"])
    return "—"


def _fmt_attr_band_value(band: dict[str, Any], parent_units: str = "") -> str:
    """Format an attribute spec band's value, inheriting units from parent."""
    v = band.get("value")
    if v is None:
        # Band might just constrain when this attr applies (e.g. frequency range)
        if band.get("range"):
            return fmt_range(band["range"])
        return "—"
    units = band.get("units") or parent_units
    if isinstance(v, (int, float)) and units:
        return fmt_si(v, units)
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    return f"{v} {units}".strip() if units else str(v)


def _output_field(band: dict[str, Any]) -> str | None:
    """Return which output field a spec band overrides, or None."""
    for field in ("range", "accuracy", "resolution"):
        val = band.get(field)
        if val is not None:
            # Check it's not an all-None dict
            if isinstance(val, dict) and all(v is None for v in val.values()):
                continue
            return field
    return None


def _split_by_output_field(
    specs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Split spec bands by which output field they override."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for band in specs:
        field = _output_field(band)
        if field:
            groups[field].append(band)
    return dict(groups)


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


def _format_output_cell(band: dict[str, Any], field: str) -> str:
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


def build_signal_render(
    sig_name: str, sig: dict[str, Any]
) -> dict[str, Any]:
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

    specs = sig.get("specs") or []
    if not specs:
        return {"headline": headline, "tables": []}

    # Group by when-key signature
    groups: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for band in specs:
        key_sig = frozenset((band.get("when") or {}).keys())
        groups[key_sig].append(band)

    tables = []
    for key_sig, bands in groups.items():
        keys = list(key_sig)
        # Stabilize key order from first band that has them
        if bands and bands[0].get("when"):
            keys = [k for k in bands[0]["when"] if k in key_sig]

        if not keys:
            # Unconditional — merge into headline
            for band in bands:
                for field in ("range", "accuracy", "resolution"):
                    if _has_output_field(band, field):
                        headline[field] = _format_output_cell(band, field)
            continue

        # Determine which output fields are present across group
        present_fields = [
            f for f in ("range", "accuracy", "resolution")
            if any(_has_output_field(b, f) for b in bands)
        ]
        if not present_fields:
            continue

        # 2D matrix: exactly 2 keys, 1 output field, dense grid
        if len(keys) == 2 and len(present_fields) == 1:
            v0 = _unique_values(bands, keys[0])
            v1 = _unique_values(bands, keys[1])
            grid_size = len(v0) * len(v1)
            if grid_size > 0 and len(bands) / grid_size >= 0.5:
                cell_fn = lambda b, f=present_fields[0]: _format_output_cell(b, f)
                tbl = _build_2d_generic(bands, keys, sig_name, cell_fn)
                tbl["title"] = f"{fmt_key(sig_name)} {fmt_key(present_fields[0])}"
                tables.append(tbl)
                continue

        # Single output field with 1 key → 1D table
        if len(keys) == 1 and len(present_fields) == 1:
            key = keys[0]
            field = present_fields[0]
            rows = []
            for band in bands:
                label = fmt_when_value(band.get("when", {}).get(key), key)
                rows.append({"label": label, "value": _format_output_cell(band, field)})
            tables.append({
                "kind": "1d",
                "title": _field_title(field, sig_name),
                "row_key": fmt_key(key),
                "value_label": fmt_key(field),
                "rows": rows,
            })
            continue

        # Multi-column: condition columns + output field columns
        tables.append(_build_multi_col_table(
            bands, keys, sig_name,
            output_fields=present_fields,
        ))

    return {"headline": headline, "tables": tables}


def _hashable(v: Any) -> Any:
    """Make a value hashable for use as dict key."""
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    if isinstance(v, list):
        return tuple(v)
    return v


def build_attr_render(
    attr_name: str, attr: dict[str, Any]
) -> dict[str, Any]:
    """Build render structures for an attribute's spec bands.

    Returns a dict with:
      - headline: formatted string for the main value
      - tables: list of 1d/2d/grouped render table dicts
    """
    headline = fmt_attr(attr)
    parent_units = attr.get("units", "") or ""

    specs = attr.get("specs") or []
    if not specs:
        return {"headline": headline, "tables": []}

    # All attribute bands share the same output type: value
    # Build a cell formatter that inherits parent units
    def _cell(band: dict[str, Any]) -> str:
        return _fmt_attr_band_value(band, parent_units)

    tables = _build_tables_from_bands(
        specs, attr_name, "value", _cell
    )

    return {"headline": headline, "tables": tables}


def _build_tables_from_bands(
    bands: list[dict[str, Any]],
    name: str,
    value_label: str,
    cell_fn,
) -> list[dict[str, Any]]:
    """Generic table builder for spec bands (used by both signals and attrs)."""
    keys = _when_keys(bands)
    ndim = len(keys)
    tables = []

    if ndim == 0:
        # Unconditional — nothing to table
        pass
    elif ndim == 1:
        key = keys[0]
        rows = []
        for band in bands:
            label = fmt_when_value(band["when"].get(key), key)
            rows.append({"label": label, "value": cell_fn(band)})
        tables.append({
            "kind": "1d",
            "title": fmt_key(name),
            "row_key": fmt_key(key),
            "value_label": fmt_key(value_label),
            "rows": rows,
        })
    elif ndim == 2:
        # Use 2D matrix only if grid is reasonably dense (>50% filled)
        v0 = _unique_values(bands, keys[0])
        v1 = _unique_values(bands, keys[1])
        grid_size = len(v0) * len(v1)
        if grid_size > 0 and len(bands) / grid_size >= 0.5:
            tables.append(_build_2d_generic(bands, keys, name, cell_fn))
        else:
            tables.append(_build_multi_col_table(bands, keys, name, value_label=value_label, cell_fn=cell_fn))
    else:
        # 3+ keys: flat multi-column table
        tables.append(_build_multi_col_table(bands, keys, name, value_label=value_label, cell_fn=cell_fn))

    return tables


def _build_2d_generic(
    bands: list[dict[str, Any]],
    keys: list[str],
    name: str,
    cell_fn,
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
        rv = band["when"].get(row_key)
        cv = band["when"].get(col_key)
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
    cell_fn=None,
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
            condition_cells = [
                fmt_when_value(band.get("when", {}).get(k), k) for k in keys
            ]
            values = [_format_output_cell(band, f) for f in output_fields]
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
            condition_cells = [
                fmt_when_value(band.get("when", {}).get(k), k) for k in keys
            ]
            rows.append({
                "conditions": condition_cells,
                "cells": [cell_fn(band)],
            })
        return {
            "kind": "multi_col",
            "title": fmt_key(name),
            "col_keys": col_keys,
            "value_cols": [fmt_key(value_label)],
            "rows": rows,
        }


def preprocess_capabilities(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preprocess capabilities for template rendering.

    Adds 'signal_renders' and 'attr_renders' dicts to each capability.
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
    return caps


def load_datasheet_data(path: Path) -> dict[str, Any]:
    """Load a catalog YAML and organize it for template rendering.

    Returns a dict with keys: entry, summary, capabilities.
    """
    entry = load_catalog_entry(path, catalog_dir=path.parent)
    data = entry.model_dump()

    # Preprocess capabilities for smart rendering
    preprocess_capabilities(data.get("capabilities", []))

    # Summary stats
    summary = {
        "capability_count": len(entry.capabilities),
        "channel_count": len(entry.channels),
        "type": entry.type,
    }

    return {
        "entry": data,
        "summary": summary,
    }


def generate_datasheet(
    path: Path,
    output: Path | None = None,
    fmt: str = "html",
) -> Path:
    """Generate a formatted datasheet from a catalog YAML file.

    Args:
        path: Path to catalog YAML file.
        output: Output file path. Defaults to <model>.html in current dir.
        fmt: Output format — "html" or "pdf".

    Returns:
        Path to generated file.
    """
    data = load_datasheet_data(path)
    entry = data["entry"]

    if output is None:
        ext = "pdf" if fmt == "pdf" else "html"
        output = Path(f"{entry['id']}.{ext}")
    else:
        output = Path(output)

    output.parent.mkdir(parents=True, exist_ok=True)

    # Load and render template
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
    html = tmpl.render(data=data["entry"], summary=data["summary"])

    if fmt == "pdf":
        try:
            from weasyprint import HTML
        except ImportError:
            raise ImportError(
                "PDF reports require weasyprint. Install with: pip install 'litmus[pdf]'"
            )
        HTML(string=html).write_pdf(str(output))
    else:
        output.write_text(html)

    return output
