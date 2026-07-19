#!/usr/bin/env python3
"""Build skill zip for Claude Desktop.

Copies source-of-truth docs into testerkit/skills/refs/ and generates
enums.md from models.py before zipping.
"""

import importlib
import inspect
import re
import shutil
import sys
import zipfile
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "testerkit" / "skills"
REFS_DIR = SKILLS_DIR / "refs"
DOCS_DIR = REPO_ROOT / "docs"
OUTPUT_DIR = REPO_ROOT / "dist"

# Source docs → refs/ mapping
# All refs are condensed LLM-optimized versions in docs/refs/.
# Source of truth for human docs: docs/concepts/, docs/guides/, docs/reference/.
# Source of truth for LLM refs: docs/refs/.
REFS_SRC = DOCS_DIR / "refs"
DOCS_TO_COPY = {
    REFS_SRC / "capability.md": REFS_DIR / "capability.md",
    REFS_SRC / "part.md": REFS_DIR / "part.md",
    REFS_SRC / "station.md": REFS_DIR / "station.md",
    REFS_SRC / "fixture.md": REFS_DIR / "fixture.md",
    REFS_SRC / "test-writing.md": REFS_DIR / "test-writing.md",
    REFS_SRC / "limits.md": REFS_DIR / "limits.md",
    REFS_SRC / "sequence.md": REFS_DIR / "sequence.md",
    REFS_SRC / "cli.md": REFS_DIR / "cli.md",
}


def generate_enums_md() -> str:
    """Generate enums.md from testerkit.models.enums."""
    sys.path.insert(0, str(REPO_ROOT))
    models = importlib.import_module("testerkit.models.enums")

    lines = ["# Enum Reference", "", "Generated from `testerkit/models/enums.py`.", ""]

    enum_classes = [
        ("MeasurementFunction", "What's being measured/sourced. Use the MOST SPECIFIC value."),
        ("Direction", "Signal flow direction for a capability."),
        (
            "WaveformShape",
            "Waveform shapes (parameter of function=waveform, not separate functions).",
        ),
        (
            "ConditionKey",
            "Canonical keys for the `conditions` dict. "
            "Shared vocabulary for parts and instruments.",
        ),
        ("ConnectorType", "Physical connector type on instrument."),
        ("TerminalRole", "Physical terminal on an instrument channel."),
        ("GroundTopology", "How channel grounds relate to each other and earth."),
        ("CompareMode", "Comparison direction for capability parameters."),
        ("Comparator", "Limit comparators per ATML/IEEE 1671."),
    ]

    for cls_name, description in enum_classes:
        cls = getattr(models, cls_name, None)
        if cls is None or not (inspect.isclass(cls) and issubclass(cls, StrEnum)):
            continue

        lines.append(f"## {cls_name}")
        lines.append("")
        lines.append(description)
        lines.append("")

        if cls_name == "MeasurementFunction":
            lines.append(_format_measurement_function(cls))
        else:
            lines.append("| Value | Description |")
            lines.append("|-------|-------------|")
            for member in cls:
                doc = _get_member_comment(cls, member.name)
                lines.append(f"| `{member.value}` | {doc} |")
            lines.append("")

    lines.extend(
        [
            "## Common Mistakes",
            "",
            "| Wrong | Right | Why |",
            "|-------|-------|-----|",
            "| `dc_voltage` for heater output | `heater_power` | Dedicated enum exists |",
            "| `dc_current` for sensor excitation | `excitation_current` | Dedicated enum exists |",
            "| `dc_voltage` for trigger I/O | `trigger` | Dedicated enum exists |",
            (
                "| Only `waveform` on a scope | "
                "Also add `dc_voltage`, `ac_voltage`, `frequency`, `rise_time`, `fall_time`, "
                "`pulse_width`, `duty_cycle`, `phase` | "
                "Scopes measure all of these |"
            ),
            "| `dc_voltage` for 10 MHz ref | `reference_clock` | Dedicated enum exists |",
            "",
        ]
    )

    return "\n".join(lines)


def _format_measurement_function(cls) -> str:
    """Format MeasurementFunction with grouping from source comments."""
    source = inspect.getsource(cls)
    lines_out = []

    for line in source.split("\n"):
        line = line.strip()
        group_match = re.match(r"^#\s+(.+?)(?:\s+\(.*\))?$", line)
        if group_match and "=" not in line:
            current_group = group_match.group(1).strip()
            if not current_group.startswith("Use "):
                lines_out.append(f"\n### {current_group}\n")
                lines_out.append("| Value | Description |")
                lines_out.append("|-------|-------------|")
            continue

        member_match = re.match(r'^(\w+)\s*=\s*"([^"]+)"(?:\s+#\s*(.*))?', line)
        if member_match:
            name, value, comment = member_match.groups()
            comment = comment or name.replace("_", " ").lower()
            lines_out.append(f"| `{value}` | {comment} |")

    lines_out.append("")
    return "\n".join(lines_out)


def _get_member_comment(cls, member_name: str) -> str:
    """Extract inline comment for an enum member from source."""
    source = inspect.getsource(cls)
    pattern = rf'{member_name}\s*=\s*"[^"]*"(?:\s+#\s*(.*))?'
    match = re.search(pattern, source)
    if match and match.group(1):
        return match.group(1).strip()
    return member_name.replace("_", " ").lower()


def build_refs():
    """Copy docs and generate enums into refs/."""
    REFS_DIR.mkdir(parents=True, exist_ok=True)

    for src, dst in DOCS_TO_COPY.items():
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  Copied: {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")
        else:
            print(f"  WARNING: Source not found: {src}")

    enums_path = REFS_DIR / "enums.md"
    enums_path.write_text(generate_enums_md())
    print(f"  Generated: {enums_path.relative_to(REPO_ROOT)}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("Building refs/...")
    build_refs()

    output_path = OUTPUT_DIR / "testerkit-skills.zip"
    skill_name = "testerkit-skills"
    print("\nCreating ZIP...")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in SKILLS_DIR.rglob("*"):
            if file.is_file() and "__pycache__" not in str(file):
                arcname = Path(skill_name) / file.relative_to(SKILLS_DIR)
                zf.write(file, arcname)
                print(f"  Added: {arcname}")

    print(f"\nCreated: {output_path}")
    print(f"Size: {output_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
