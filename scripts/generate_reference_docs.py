"""Generate enumerative sections of the Litmus reference docs from source.

The five reference pages listed below contain large field / endpoint /
command tables that drift the moment source moves. Each table is wrapped
in HTML-comment markers:

    <!-- GENERATED:<section-id>:start -->
    ... auto-generated content ...
    <!-- GENERATED:<section-id>:end -->

This script overwrites only the content between matching markers; the
hand-written prose, examples, and intros outside the markers stay put.

Targets:
    event-types   -> docs/reference/event-types.md
    models        -> docs/reference/models.md
    configuration -> docs/reference/configuration.md
    api           -> docs/reference/api.md
    cli           -> docs/reference/cli.md

Usage:
    uv run python scripts/generate_reference_docs.py event-types
    uv run python scripts/generate_reference_docs.py --all
    uv run python scripts/generate_reference_docs.py --all --check

``--check`` exits nonzero if any file would change. The pre-commit hook
runs that form so source / docs drift fails the commit.
"""

from __future__ import annotations

import argparse
import re
import sys
import types
import typing
from collections.abc import Iterable
from inspect import cleandoc
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs" / "reference"

MARKER_RE = re.compile(
    r"<!-- GENERATED:(?P<id>[a-z0-9_-]+):start -->.*?<!-- GENERATED:(?P=id):end -->",
    re.DOTALL,
)


# =============================================================================
# Type rendering
# =============================================================================


def _render_annotation(ann: Any) -> str:
    """Render a Python / Pydantic annotation as a readable string.

    Handles: bare classes, ``X | None`` unions, ``list[X]`` / ``dict[K, V]``,
    ``Literal[...]``, ``Annotated[X, ...]`` (unwraps to X), and StrEnum
    subclasses (rendered as the enum name).
    """
    origin = get_origin(ann)

    # Annotated[X, ...] — unwrap
    if origin is typing.Annotated:
        return _render_annotation(get_args(ann)[0])

    # Literal[...] — render values
    if origin is Literal:
        vals = ", ".join(repr(v) for v in get_args(ann))
        return f"Literal[{vals}]"

    # Unions (X | Y and typing.Union)
    if origin is Union or origin is types.UnionType:
        parts = [_render_annotation(a) for a in get_args(ann)]
        # Push None to the end for readability
        nones = [p for p in parts if p == "None"]
        others = [p for p in parts if p != "None"]
        return " | ".join(others + nones)

    # Generic builtins / typing aliases (list, dict, tuple, set, frozenset)
    if origin is not None:
        name = getattr(origin, "__name__", None) or str(origin)
        args = get_args(ann)
        if args:
            inner = ", ".join(_render_annotation(a) for a in args)
            return f"{name}[{inner}]"
        return name

    # Bare classes / NoneType / typing.Any
    if ann is type(None):
        return "None"
    if ann is Any:
        return "Any"
    if isinstance(ann, type):
        return ann.__name__
    if ann is Ellipsis:
        return "..."
    return str(ann)


def _render_default(field: FieldInfo) -> str:
    """Render a Pydantic field's default cell value."""
    if field.is_required():
        return "*required*"
    if field.default is not PydanticUndefined:
        v = field.default
        if v is None:
            return "`None`"
        if isinstance(v, str):
            return f"`{v!r}`"
        if isinstance(v, bool):
            return f"`{v}`"
        if isinstance(v, (int, float)):
            return f"`{v}`"
        return f"`{v!r}`"
    if field.default_factory is not None:
        name = getattr(field.default_factory, "__name__", None)
        if name == "<lambda>" or name is None:
            return "*factory*"
        if name in ("dict", "list", "tuple", "set"):
            empty = {"dict": "{}", "list": "[]", "tuple": "()", "set": "set()"}[name]
            return f"`{empty}`"
        return f"*via* `{name}()`"
    return ""


def _render_field_table(model: type[BaseModel]) -> str:
    """Render a Pydantic model's fields as a markdown table."""
    rows = ["| Field | Type | Default |", "|---|---|---|"]
    for name, field in model.model_fields.items():
        type_str = _render_annotation(field.annotation).replace("|", "\\|")
        default = _render_default(field)
        rows.append(f"| `{name}` | `{type_str}` | {default} |")
    return "\n".join(rows)


def _first_paragraph(docstring: str | None) -> str:
    """Return the first paragraph of a docstring, cleaned and one-line-collapsed."""
    if not docstring:
        return ""
    cleaned = cleandoc(docstring)
    para = cleaned.split("\n\n", 1)[0]
    return " ".join(line.strip() for line in para.splitlines() if line.strip())


# =============================================================================
# Marker substitution
# =============================================================================


def _replace_section(content: str, section_id: str, new_body: str) -> str:
    """Replace the body inside one GENERATED marker pair. Errors if missing.

    ``new_body`` is the content that goes between the markers (no markers
    in it; the function wraps with them). A trailing newline is enforced
    so the closing marker stays on its own line.
    """
    body = new_body.rstrip() + "\n"
    block = f"<!-- GENERATED:{section_id}:start -->\n{body}<!-- GENERATED:{section_id}:end -->"

    pattern = re.compile(
        rf"<!-- GENERATED:{re.escape(section_id)}:start -->.*?"
        rf"<!-- GENERATED:{re.escape(section_id)}:end -->",
        re.DOTALL,
    )
    new_content, count = pattern.subn(block, content, count=1)
    if count == 0:
        raise SystemExit(
            f"error: section marker '{section_id}' not found in target file. "
            "Add the marker pair to the docs page before running this generator."
        )
    return new_content


def _write_or_check(path: Path, new_content: str, *, check: bool) -> bool:
    """Write ``new_content`` to ``path`` (or report drift in --check mode).

    Returns True when the file matches the desired content, False on drift.
    """
    existing = path.read_text() if path.exists() else ""
    if existing == new_content:
        return True
    if check:
        print(f"DRIFT: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return False
    path.write_text(new_content)
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return True


# =============================================================================
# event-types.md
# =============================================================================


# (category-label, set-name-in-events-module, ordered-class-list-source)
# The ordered list keeps the doc deterministic; sets don't preserve order.
_EVENT_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Session", ["SessionStarted", "SessionEnded"]),
    ("Run", ["RunStarted", "RunEnded", "RunMaterialized"]),
    ("Slot (multi-DUT)", ["SlotStarted", "SlotCompleted", "SyncArrived", "SyncRelease"]),
    (
        "Fixture",
        [
            "InstrumentConnected",
            "IdentityVerified",
            "CalibrationWarning",
            "DutScanned",
            "InstrumentDisconnected",
        ],
    ),
    (
        "Test",
        ["StepStarted", "StepEnded", "MeasurementRecorded", "RecordEvent", "StepsDiscovered"],
    ),
    ("Route (switching)", ["RouteClosed", "RouteOpened"]),
    (
        "Instrument (proxy traffic)",
        ["InstrumentRead", "InstrumentSet", "InstrumentConfigure"],
    ),
    ("Diagnostic", ["DiagnosticWarning", "DiagnosticError"]),
    ("Stream", ["StreamStarted", "StreamEnded", "StreamFrameIndex"]),
    ("Dialog", ["DialogOpened", "DialogResponded"]),
]


def _event_type_value(cls: type[BaseModel]) -> str:
    """Return the literal value of the class's ``event_type`` field."""
    field = cls.model_fields.get("event_type")
    if field is None:
        return ""
    if field.default is not PydanticUndefined and isinstance(field.default, str):
        return field.default
    # Fall back to the Literal type if no default set
    ann = field.annotation
    if get_origin(ann) is Literal:
        args = get_args(ann)
        if args and isinstance(args[0], str):
            return args[0]
    return ""


def _generate_event_types(*, check: bool) -> bool:
    from litmus.data import events as events_mod

    target = DOCS_DIR / "event-types.md"

    # Base fields section — emit the EventBase table.
    base_body = _render_field_table(events_mod.EventBase)

    # Categories section — one ## heading per category, one ### per class.
    parts: list[str] = []
    seen_classes: set[type[BaseModel]] = set()
    for label, ordered_names in _EVENT_CATEGORIES:
        parts.append(f"## {label} events\n")
        for class_name in ordered_names:
            cls = getattr(events_mod, class_name)
            seen_classes.add(cls)
            event_type = _event_type_value(cls)
            heading = (
                f"### `{event_type}` — `{class_name}`" if event_type else f"### `{class_name}`"
            )
            parts.append(heading)
            # ``__doc__`` directly — getdoc() inherits from the base class
            # and would print EventBase's docstring on every class that
            # doesn't define its own.
            blurb = _first_paragraph(cls.__doc__)
            if blurb:
                parts.append("")
                parts.append(blurb)
            parts.append("")
            # Omit the base fields (documented at the top) and the
            # event_type discriminator (already in the heading).
            class_only = {
                name: f
                for name, f in cls.model_fields.items()
                if name not in events_mod.EventBase.model_fields and name != "event_type"
            }
            if class_only:
                rows = ["| Field | Type | Default |", "|---|---|---|"]
                for name, field in class_only.items():
                    type_str = _render_annotation(field.annotation).replace("|", "\\|")
                    rows.append(f"| `{name}` | `{type_str}` | {_render_default(field)} |")
                parts.append("\n".join(rows))
            else:
                parts.append(
                    "*(no fields beyond the base; "
                    "the event_type discriminator is the only marker.)*"
                )
            parts.append("")

    # Sanity check — every EventBase subclass in ALL_EVENTS must appear in
    # the category list. Catch the "new event class, no docs" case.
    missing = events_mod.ALL_EVENTS - seen_classes
    if missing:
        names = sorted(c.__name__ for c in missing)
        raise SystemExit(
            f"error: event class(es) not assigned to a category in "
            f"_EVENT_CATEGORIES: {names}. Update the generator."
        )

    body_section = "\n".join(parts).rstrip() + "\n"

    existing = target.read_text()
    new = _replace_section(existing, "event-types-base-fields", base_body)
    new = _replace_section(new, "event-types-by-category", body_section)
    return _write_or_check(target, new, check=check)


# =============================================================================
# Dispatcher
# =============================================================================


GENERATORS: dict[str, Any] = {
    "event-types": _generate_event_types,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "targets",
        nargs="*",
        choices=list(GENERATORS) + ["all"],
        help="Page(s) to regenerate; omit to print the list.",
    )
    parser.add_argument("--all", action="store_true", help="Regenerate every page.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit nonzero if any file would change.",
    )
    args = parser.parse_args(argv)

    if not args.targets and not args.all:
        print("Available targets:")
        for name in GENERATORS:
            print(f"  {name}")
        print("\nUse --all to regenerate every page.")
        return 0

    if args.all or "all" in args.targets:
        targets: Iterable[str] = list(GENERATORS)
    else:
        targets = args.targets

    ok = True
    for target in targets:
        if not GENERATORS[target](check=args.check):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
