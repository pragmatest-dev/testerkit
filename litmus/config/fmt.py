"""Litmus YAML formatter — consistent style via ruamel.yaml round-trip.

Applies to all Litmus YAML: catalog, products, sequences, stations, fixtures.

Style rules:
  Block always: when, signals, conditions, controls, attributes,
                capabilities, channels, catalog_entry, specs,
                characteristics, vectors, steps, limits
  Flow always:  leaf-value dicts with only scalars (range, accuracy, etc.)
  Flow always:  scalar lists ≤8 items (channels, interfaces, options)

Boolean safety: YAML reserves on/off/yes/no as booleans.  The formatter
quotes any string value that would be misinterpreted by a plain YAML load.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

_BLOCK_KEYS = {
    # Catalog
    "when",
    "signals",
    "conditions",
    "controls",
    "attributes",
    "capabilities",
    "channels",
    "catalog_entry",
    "specs",
    # Products
    "characteristics",
    "vectors",
    "limits",
    # Sequences
    "steps",
    # Station / fixture
    "instruments",
    "roles",
    "pins",
}


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool, type(None)))


def _quote_if_needed(v: Any) -> Any:
    """Quote all string values for safe, unambiguous YAML output."""
    if isinstance(v, str):
        return DoubleQuotedScalarString(v)
    return v


def _apply_style(data: Any, key: str | None = None) -> Any:
    """Recursively apply style rules to a data structure."""
    if isinstance(data, dict):
        cm = CommentedMap()
        for k, v in data.items():
            cm[k] = _apply_style(v, key=k)

        if key not in _BLOCK_KEYS and all(_is_scalar(v) for v in data.values()):
            cm.fa.set_flow_style()
        return cm

    elif isinstance(data, list):
        cs = CommentedSeq()
        for item in data:
            cs.append(_apply_style(_quote_if_needed(item)))

        if all(_is_scalar(v) for v in data) and len(data) <= 8:
            cs.fa.set_flow_style()
        return cs

    return _quote_if_needed(data)


def _make_yaml() -> YAML:
    """Create a configured ruamel YAML instance."""
    ry = YAML()
    ry.default_flow_style = False
    ry.width = 120
    ry.indent(mapping=2, sequence=2, offset=0)
    return ry


def dump_yaml(data: dict[str, Any]) -> str:
    """Dump a dict to a YAML string with Litmus conventions."""
    styled = _apply_style(data)
    ry = _make_yaml()
    buf = StringIO()
    ry.dump(styled, buf)
    return buf.getvalue()


def format_file(path: Path) -> str:
    """Load a YAML file and return it formatted.

    Strips comments and enforces consistent style.
    """
    plain = yaml.safe_load(path.read_text())
    return dump_yaml(plain)


def format_file_inplace(path: Path) -> bool:
    """Format a YAML file in-place. Returns True if changed."""
    original = path.read_text()
    formatted = format_file(path)
    if formatted != original:
        path.write_text(formatted)
        return True
    return False
