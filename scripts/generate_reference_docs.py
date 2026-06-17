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
    event-types   -> docs/reference/data/event-types.md
    models        -> docs/reference/data/models.md
    configuration -> docs/reference/configuration.md
    api           -> docs/reference/runtime/api.md
    cli           -> docs/reference/cli.md
    pytest-native -> docs/reference/overview/pytest-native.md
    query-api     -> docs/reference/data/query-api.md

Usage:
    uv run python scripts/generate_reference_docs.py event-types
    uv run python scripts/generate_reference_docs.py --all
    uv run python scripts/generate_reference_docs.py --all --check

``--check`` exits nonzero if any file would change. The pre-commit hook
runs that form so source / docs drift fails the commit.
"""

from __future__ import annotations

# Keep the daemon-notify hop from firing when this script imports
# litmus.api.app (ParquetBackend) or litmus.mcp.server — the script
# just walks routes / tools, it does not run a session. Must run
# BEFORE any litmus import.
import os as _os

_os.environ.setdefault("LITMUS_SKIP_DAEMON_NOTIFY", "1")

import argparse
import ast
import importlib
import inspect
import re
import sys
import types
import typing
from collections.abc import Iterable
from enum import Enum
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
        if isinstance(v, Enum):
            return f"`{type(v).__name__}.{v.name}`"
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
    ("Slot (multi-UUT)", ["SlotStarted", "SlotCompleted", "SyncArrived", "SyncRelease"]),
    (
        "Fixture",
        [
            "InstrumentConnected",
            "IdentityVerified",
            "CalibrationWarning",
            "UutScanned",
            "InstrumentDisconnected",
        ],
    ),
    (
        "Test",
        [
            "StepStarted",
            "StepEnded",
            "MeasurementRecorded",
            "Observation",
            "StepsDiscovered",
        ],
    ),
    ("Route (switching)", ["RouteClosed", "RouteOpened"]),
    (
        "Instrument (proxy traffic)",
        ["InstrumentSet", "InstrumentConfigure"],
    ),
    ("Channel (lifecycle)", ["ChannelStarted", "ChannelEnded"]),
    ("Diagnostic", ["DiagnosticWarning", "DiagnosticError"]),
    ("File", ["FileStarted", "FileEnded", "StreamCheckpoint"]),
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

    target = DOCS_DIR / "data" / "event-types.md"

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
# models.md
# =============================================================================


# Display order for source files. Each entry is (heading, dotted-module).
# Order matters — readers walk top-to-bottom.
_MODELS_MODULES: list[tuple[str, str]] = [
    ("Project & station YAML", "litmus.models.project"),
    ("Station", "litmus.models.station"),
    ("Part", "litmus.models.part"),
    ("Part manifest", "litmus.models.part_manifest"),
    ("Test config (sidecar, markers, limits, fixtures)", "litmus.models.test_config"),
    ("Capabilities (catalog signal/condition/control/attribute)", "litmus.models.capability"),
    ("Catalog entry", "litmus.models.catalog"),
    ("Instrument record", "litmus.models.instrument"),
    ("Instrument asset", "litmus.models.instrument_asset"),
    ("Runtime data (events, runs, steps, measurements)", "litmus.data.models"),
    ("Channel store records", "litmus.data.channels.models"),
    ("HTTP API request shapes", "litmus.api.models"),
    ("HTTP API response shapes", "litmus.api.responses"),
    ("Query API row records", "litmus.analysis.runs_query"),
    ("Query API row records (steps)", "litmus.analysis.steps_query"),
    ("Query API facets & filters", "litmus.analysis.measurement_facets"),
]


def _classes_defined_in(module: types.ModuleType, base: type) -> list[type]:
    """Subclasses of ``base`` declared in ``module`` (not imported), in source order."""
    out: list[type] = []
    for obj in module.__dict__.values():
        if not isinstance(obj, type):
            continue
        if obj is base:
            continue
        if not issubclass(obj, base):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        out.append(obj)
    out.sort(key=lambda c: inspect.getsourcelines(c)[1])
    return out


def _render_enum_table(enum_cls: type[Enum]) -> str:
    """Render an enum as a markdown table of value + (optional) description.

    Description is mined from a per-member docstring of the form
    ``MEMBER = "value"  # description``. Without comments, the column
    is left blank.
    """
    src_lines, _ = inspect.getsourcelines(enum_cls)
    descriptions: dict[str, str] = {}
    for raw in src_lines:
        # Match: `IDENT = "value"  # description` or with single quotes
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=\s*['\"][^'\"]*['\"]\s*#\s*(.*)", raw)
        if m:
            descriptions[m.group(1)] = m.group(2).strip()
    rows = ["| Value | Description |", "|---|---|"]
    for member in enum_cls:
        desc = descriptions.get(member.name, "")
        value_repr = f"`{member.value!r}`" if isinstance(member.value, str) else f"`{member.value}`"
        rows.append(f"| {value_repr} | {desc} |")
    return "\n".join(rows)


def _render_model_class(cls: type[BaseModel], *, level: int = 4) -> list[str]:
    """Render one BaseModel as anchor + heading + blurb + field table."""
    hashes = "#" * level
    out = [f"{hashes} `{cls.__name__}` {{#model-{cls.__name__.lower()}}}"]
    blurb = _first_paragraph(cls.__doc__)
    if blurb:
        out.append("")
        out.append(blurb)
    out.append("")
    if cls.model_fields:
        out.append(_render_field_table(cls))
    else:
        out.append("*(no fields)*")
    out.append("")
    return out


def _render_enum_class(cls: type[Enum], *, level: int = 4) -> list[str]:
    """Render one Enum as anchor + heading + blurb + value table."""
    hashes = "#" * level
    out = [f"{hashes} `{cls.__name__}` {{#enum-{cls.__name__.lower()}}}"]
    blurb = _first_paragraph(cls.__doc__)
    if blurb:
        out.append("")
        out.append(blurb)
    out.append("")
    out.append(_render_enum_table(cls))
    out.append("")
    return out


def _generate_models(*, check: bool) -> bool:
    target = DOCS_DIR / "data" / "models.md"

    # Per-module sections — emitted into the GENERATED:models-by-module block.
    parts: list[str] = []
    for label, dotted in _MODELS_MODULES:
        module = importlib.import_module(dotted)
        parts.append(f"### {label} — `{dotted}`\n")

        # BaseModel classes first (the heavyweight tables)
        for model in _classes_defined_in(module, BaseModel):
            parts.extend(_render_model_class(model, level=4))

        # Then any enums declared alongside (StrEnum / Enum)
        for enum_cls in _classes_defined_in(module, Enum):
            parts.extend(_render_enum_class(enum_cls, level=4))

    models_body = "\n".join(parts).rstrip() + "\n"

    # The shared enums module (litmus.models.enums) is rendered as one
    # standalone block so it isn't duplicated under every module that
    # imports from it.
    import litmus.models.enums as enums_mod

    enum_parts: list[str] = []
    for enum_cls in _classes_defined_in(enums_mod, Enum):
        enum_parts.extend(_render_enum_class(enum_cls, level=3))
    enums_body = "\n".join(enum_parts).rstrip() + "\n"

    existing = target.read_text()
    new = _replace_section(existing, "models-by-module", models_body)
    new = _replace_section(new, "models-shared-enums", enums_body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# configuration.md
# =============================================================================


# (yaml-file-pattern, validating-model-dotted-path, one-line-purpose)
_CONFIG_FILES: list[tuple[str, str, str]] = [
    (
        "`litmus.yaml`",
        "litmus.models.project.ProjectConfig",
        "Project root — names, defaults, profiles, multi-slot knobs.",
    ),
    (
        "`stations/<id>.yaml`",
        "litmus.models.station.StationConfig",
        "Concrete station deployment — instruments, drivers, resources.",
    ),
    (
        "`stations/types/<id>.yaml`",
        "litmus.models.station.StationType",
        "Abstract station-type template — required roles, capabilities.",
    ),
    (
        "`fixtures/<id>.yaml`",
        "litmus.models.test_config.FixtureConfig",
        "UUT-pin ↔ instrument-channel routing (single-UUT) or per-slot routing (multi-UUT).",
    ),
    (
        "`parts/<id>.yaml`",
        "litmus.models.part.Part",
        "Part specification — pins, signal groups, characteristics.",
    ),
    (
        "`tests/test_<name>.yaml`",
        "litmus.models.test_config.SidecarConfig",
        "Sidecar test config co-located with `tests/test_<name>.py`"
        " — sweeps, limits, mocks, retry, prompts.",
    ),
    (
        "`catalog/<vendor>/<model>.yaml`",
        "litmus.models.catalog.InstrumentCatalogEntry",
        "Instrument capability catalog"
        " — see [catalog-schema.md](catalog-schema.md) for the full reference.",
    ),
]


def _generate_configuration(*, check: bool) -> bool:
    target = DOCS_DIR / "configuration.md"

    rows = [
        "| File | Pydantic model | What it carries |",
        "|---|---|---|",
    ]
    for pattern, dotted, purpose in _CONFIG_FILES:
        cls_name = dotted.rsplit(".", 1)[1]
        model_anchor = f"models.md#model-{cls_name.lower()}"
        rows.append(f"| {pattern} | [`{cls_name}`]({model_anchor}) | {purpose} |")

    body = "\n".join(rows)

    existing = target.read_text()
    new = _replace_section(existing, "configuration-file-index", body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# api.md
# =============================================================================


def _http_response_model_name(rm: Any) -> str:
    """Render a FastAPI route's response_model as a human-readable cell."""
    if rm is None:
        return "—"
    # `Union` / `X | Y` types (e.g. MatchSingleResponse | MatchAllResponse)
    origin = get_origin(rm)
    if origin is Union or origin is types.UnionType:
        return " \\| ".join(_http_response_model_name(a) for a in get_args(rm))
    name = getattr(rm, "__name__", None)
    if name:
        return f"`{name}`"
    return f"`{rm!r}`"


# Group HTTP routes by section heading. Match by path prefix; routes
# that don't match any prefix go under "Other". Order matters — first
# matching prefix wins. The prefix-stripping is the doc heading; full
# path stays in the table.
_HTTP_SECTIONS: list[tuple[str, str]] = [
    ("Runs", "/api/runs"),
    ("Active runs", "/api/active"),
    ("Dialogs", "/api/dialogs"),
    ("Events & sessions", "/api/events"),
    ("Events & sessions", "/api/sessions"),
    ("Channels", "/api/channels"),
    ("Parts", "/api/parts"),
    ("Stations", "/api/stations"),
    ("Capability matching", "/api/match"),
    ("Instruments", "/api/instruments"),
    ("Metrics", "/api/metrics"),
    ("MCP-parity tools", "/api/discover"),
    ("MCP-parity tools", "/api/open"),
    ("MCP-parity tools", "/api/schema"),
    ("MCP-parity tools", "/api/save"),
    ("MCP-parity tools", "/api/read"),
    ("MCP-parity tools", "/api/enum"),
    ("MCP-parity tools", "/api/enum-reference"),
    ("API discovery", "/api/openapi.json"),
    ("API discovery", "/api/docs"),
    ("API discovery", "/api/redoc"),
]


def _classify_http_route(path: str) -> str:
    for label, prefix in _HTTP_SECTIONS:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return label
    return "Other"


def _generate_api(*, check: bool) -> bool:
    # Late import — pulls in FastAPI + Pydantic-validated config; daemon
    # notify is suppressed by the env var set at module load.
    from fastapi.routing import APIRoute

    from litmus.api.app import create_api_router

    router = create_api_router()

    # Group routes by section, preserving discovery order within each.
    by_section: dict[str, list[tuple[str, str, str, str]]] = {}
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = ", ".join(sorted(route.methods - {"HEAD"}))
        section = _classify_http_route(route.path)
        summary = _first_paragraph(route.endpoint.__doc__) or ""
        response = _http_response_model_name(getattr(route, "response_model", None))
        by_section.setdefault(section, []).append((methods, route.path, response, summary))

    section_order = list({label: None for label, _ in _HTTP_SECTIONS}) + ["Other"]
    parts: list[str] = []
    for label in section_order:
        rows = by_section.get(label)
        if not rows:
            continue
        parts.append(f"### {label}\n")
        parts.append("| Method | Path | Response model | Summary |")
        parts.append("|---|---|---|---|")
        for methods, path, response, summary in rows:
            parts.append(f"| `{methods}` | `{path}` | {response} | {summary} |")
        parts.append("")
    http_body = "\n".join(parts).rstrip() + "\n"

    # ---- MCP tools + prompts -------------------------------------------
    from litmus.mcp.server import create_mcp_server

    mcp = create_mcp_server()
    import asyncio

    tools_dict = asyncio.run(mcp.get_tools())

    mcp_parts: list[str] = ["| Tool | Parameters | Summary |", "|---|---|---|"]
    for name, tool in sorted(tools_dict.items()):
        props = tool.parameters.get("properties", {}) if tool.parameters else {}
        params = ", ".join(f"`{p}`" for p in props) or "—"
        summary = _first_paragraph(tool.description)
        mcp_parts.append(f"| `{name}` | {params} | {summary} |")
    mcp_body = "\n".join(mcp_parts)

    prompts_dict = asyncio.run(mcp.get_prompts())
    prompt_parts: list[str] = ["| Prompt | Arguments | Summary |", "|---|---|---|"]
    for name, prompt in sorted(prompts_dict.items()):
        args = ", ".join(f"`{a.name}`" for a in (prompt.arguments or [])) or "—"
        summary = _first_paragraph(prompt.description)
        prompt_parts.append(f"| `{name}` | {args} | {summary} |")
    prompts_body = "\n".join(prompt_parts)

    target = DOCS_DIR / "runtime" / "api.md"
    existing = target.read_text()
    new = _replace_section(existing, "api-http-routes", http_body)
    new = _replace_section(new, "api-mcp-tools", mcp_body)
    new = _replace_section(new, "api-mcp-prompts", prompts_body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# cli.md
# =============================================================================


def _render_click_param(param: Any) -> tuple[str, str, str]:
    """Return (name-cell, type-cell, help-cell) for one click Param.

    Distinguishes arguments (positional) from options (--flag) by the
    Param subclass; renders sensible type strings for click types
    (Choice as ``{a, b, c}``, INT/FLOAT/STRING as bare names).
    """
    import click

    name = param.name or ""
    if isinstance(param, click.Argument):
        cell = f"`{name.upper()}`"
        if param.nargs == -1:
            cell += "..."
    else:
        opts = "/".join(f"`{o}`" for o in param.opts)
        if param.secondary_opts:
            opts += "/" + "/".join(f"`{o}`" for o in param.secondary_opts)
        cell = opts

    pt = param.type
    if isinstance(pt, click.Choice):
        type_str = "{" + ", ".join(pt.choices) + "}"
    elif pt is None or getattr(pt, "name", "") == "boolean":
        type_str = "flag" if getattr(param, "is_flag", False) else "bool"
    else:
        type_str = getattr(pt, "name", "")

    help_text = ""
    if isinstance(param, click.Option):
        help_text = (param.help or "").replace("\n", " ").strip()
        if (
            param.default is not None
            and not param.is_flag
            and not param.required
            and type(param.default).__name__ != "Sentinel"  # click._utils.Sentinel.UNSET
        ):
            help_text = (
                f"{help_text}  *(default: `{param.default}`)*"
                if help_text
                else f"*(default: `{param.default}`)*"
            )

    return cell, type_str, help_text


def _render_click_command(cmd: Any, dotted: str, *, level: int = 4) -> list[str]:
    """Render one click Command as heading + summary + params table."""
    import click

    hashes = "#" * level
    out = [f"{hashes} `litmus {dotted}` {{#cli-{dotted.replace(' ', '-')}}}"]
    summary = (cmd.help or cmd.short_help or "").strip()
    summary_first = summary.split("\n\n", 1)[0] if summary else ""
    summary_first = " ".join(line.strip() for line in summary_first.splitlines() if line.strip())
    if summary_first:
        out.append("")
        out.append(summary_first)
    out.append("")

    # Skip the implicit --help on every command — readers know.
    visible_params = [
        p for p in cmd.params if not (isinstance(p, click.Option) and p.opts == ["--help"])
    ]
    if visible_params:
        out.append("| Argument / option | Type | Description |")
        out.append("|---|---|---|")
        for p in visible_params:
            name_cell, type_cell, help_cell = _render_click_param(p)
            type_disp = f"`{type_cell}`" if type_cell else ""
            out.append(f"| {name_cell} | {type_disp} | {help_cell} |")
    else:
        out.append("*(no options or arguments.)*")
    out.append("")
    return out


def _walk_commands(group: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Yield ``(dotted-path, command)`` for every leaf command, depth-first.

    Group nodes are returned so the renderer can emit a section heading,
    but only leaf commands carry param tables — groups exist purely to
    namespace.
    """
    import click

    out: list[tuple[str, Any]] = []
    for name in sorted(group.commands):
        cmd = group.commands[name]
        dotted = f"{prefix} {name}".strip()
        out.append((dotted, cmd))
        if isinstance(cmd, click.Group):
            out.extend(_walk_commands(cmd, dotted))
    return out


def _generate_cli(*, check: bool) -> bool:
    import click

    from litmus.cli import main

    parts: list[str] = []
    for dotted, cmd in _walk_commands(main):
        depth = dotted.count(" ")  # 0 = top-level, 1 = one level nested, …
        if isinstance(cmd, click.Group):
            # ### for top-level groups (under `## Commands`), #### for nested.
            hashes = "#" * (depth + 3)
            parts.append(f"{hashes} `litmus {dotted}` (group) {{#cli-{dotted.replace(' ', '-')}}}")
            help_first = (cmd.help or cmd.short_help or "").splitlines()[0].strip()
            if help_first:
                parts.append("")
                parts.append(help_first)
            parts.append("")
            continue
        # Leaf command — top-level uses ###, subcommand-of-group uses ####.
        level = depth + 3
        parts.extend(_render_click_command(cmd, dotted, level=level))

    body = "\n".join(parts).rstrip() + "\n"

    target = DOCS_DIR / "cli.md"
    existing = target.read_text()
    new = _replace_section(existing, "cli-commands", body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# pytest-native.md (pytest_addoption flags)
# =============================================================================


def _ast_str(node: ast.AST) -> str | None:
    """Return the string value of a `Constant` / `Str` / simple concat, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f-strings — skip; we don't render them
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _ast_str(node.left)
        right = _ast_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _extract_addoption_calls(func: ast.FunctionDef) -> list[dict[str, Any]]:
    """Walk a function body, collect every `group.addoption(...)` call.

    Returns one dict per call with keys: flag (the first positional arg),
    default (rendered string or None), action (string or None), help (string or None),
    dest (string or None). Calls that take a non-literal first arg are skipped.
    """
    out: list[dict[str, Any]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "addoption":
            continue
        if not node.args:
            continue
        flag = _ast_str(node.args[0])
        if flag is None:
            continue

        entry: dict[str, Any] = {
            "flag": flag,
            "default": None,
            "action": None,
            "help": None,
            "dest": None,
        }
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if kw.arg == "help":
                entry["help"] = _ast_str(kw.value)
            elif kw.arg == "action":
                entry["action"] = _ast_str(kw.value)
            elif kw.arg == "dest":
                entry["dest"] = _ast_str(kw.value)
            elif kw.arg == "default":
                # Render the default as best we can; non-literal → "*dynamic*".
                v = kw.value
                if isinstance(v, ast.Constant):
                    entry["default"] = v.value
                else:
                    entry["default"] = "*dynamic*"
        out.append(entry)
    return out


def _generate_pytest_native(*, check: bool) -> bool:
    target = DOCS_DIR / "overview" / "pytest-native.md"
    source = (REPO_ROOT / "src/litmus/pytest_plugin/hooks.py").read_text()
    tree = ast.parse(source)

    func: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_addoption":
            func = node
            break
    if func is None:
        raise SystemExit("error: pytest_addoption not found in src/litmus/pytest_plugin/hooks.py")

    addoptions = _extract_addoption_calls(func)

    parts = ["| Flag | Type | Default | Description |", "|---|---|---|---|"]
    for entry in addoptions:
        flag = f"`{entry['flag']}`"
        action = entry["action"]
        default = entry["default"]
        if action == "store_true":
            type_str = "`flag`"
            default_str = "`False`" if default in (None, False) else f"`{default}`"
        elif action == "store_false":
            type_str = "`flag` (inverse)"
            default_str = "`True`" if default in (None, True) else f"`{default}`"
        else:
            type_str = "`text`"
            if default is None:
                default_str = ""
            elif default == "*dynamic*":
                default_str = "*resolved at runtime*"
            else:
                default_str = f"`{default!r}`" if isinstance(default, str) else f"`{default}`"
        help_text = (entry["help"] or "").replace("\n", " ").strip()
        help_text = re.sub(r"\s+", " ", help_text)
        help_cell = help_text.replace("|", "\\|")
        parts.append(f"| {flag} | {type_str} | {default_str} | {help_cell} |")

    parts.append("")
    parts.append(
        "Plus dynamic flags generated from `litmus.yaml`:"
        " every `profiles[*].facets:` key becomes `--<facet-key>` and"
        " every `required_inputs:` key becomes `--<required-input-key>`."
        " See [how-to/profiles.md](../how-to/profiles.md) for the resolution"
        " chain (CLI flag → env var → profile binding → `default_*`)."
    )

    body = "\n".join(parts).rstrip() + "\n"

    existing = target.read_text()
    new = _replace_section(existing, "pytest-plugin-flags", body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# query-api.md (RunsQuery / StepsQuery / MeasurementsQuery method tables)
# =============================================================================


_QUERY_CLASSES: list[tuple[str, str]] = [
    ("`RunsQuery`", "litmus.analysis.runs_query.RunsQuery"),
    ("`StepsQuery`", "litmus.analysis.steps_query.StepsQuery"),
    ("`MeasurementsQuery`", "litmus.analysis.measurements_query.MeasurementsQuery"),
]


def _render_method_signature(name: str, method: Any) -> str:
    """Render a method's signature with parameter names + annotations + return."""
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        return f"`{name}(...)`"
    params: list[str] = []
    for p_name, p in sig.parameters.items():
        if p_name == "self":
            continue
        bit = p_name
        if p.kind is inspect.Parameter.KEYWORD_ONLY and "*" not in params:
            params.append("*")
        if p.annotation is not inspect.Parameter.empty:
            bit += f": {_render_annotation(p.annotation)}"
        if p.default is not inspect.Parameter.empty:
            bit += f" = {p.default!r}"
        params.append(bit)
    ret = ""
    if sig.return_annotation is not inspect.Signature.empty:
        ret = f" → {_render_annotation(sig.return_annotation)}"
    return f"`{name}({', '.join(params)}){ret}`"


def _public_methods(cls: type) -> list[tuple[str, Any]]:
    """Return ``(name, method)`` for every public method declared on cls (not inherited)."""
    out: list[tuple[str, Any]] = []
    for name, val in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        if name not in cls.__dict__:  # skip inherited
            continue
        out.append((name, val))
    out.sort(key=lambda nm: inspect.getsourcelines(nm[1])[1])
    return out


def _generate_query_api(*, check: bool) -> bool:
    target = DOCS_DIR / "data" / "query-api.md"

    parts: list[str] = []
    for label, dotted in _QUERY_CLASSES:
        module_name, cls_name = dotted.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, cls_name)
        anchor = cls_name.lower()
        parts.append(f"## {label} {{#{anchor}}}\n")
        blurb = _first_paragraph(cls.__doc__)
        if blurb:
            parts.append(blurb)
            parts.append("")
        # The three Query classes are re-exported from ``litmus.queries`` —
        # show the shallow user-facing path in the Import: line, not the
        # implementation module. The source-module is still surfaced so
        # contributors know where the implementation lives.
        parts.append(f"Source: `{module_name}`. Import: `from litmus.queries import {cls_name}`.\n")

        for name, method in _public_methods(cls):
            sig = _render_method_signature(name, method)
            parts.append(f"### `{cls_name}.{name}` {{#{anchor}-{name}}}\n")
            parts.append(sig)
            parts.append("")
            doc = _first_paragraph(method.__doc__)
            if doc:
                parts.append(doc)
                parts.append("")

    body = "\n".join(parts).rstrip() + "\n"

    existing = target.read_text()
    new = _replace_section(existing, "query-api-classes", body)
    return _write_or_check(target, new, check=check)


# =============================================================================
# Dispatcher
# =============================================================================


GENERATORS: dict[str, Any] = {
    "event-types": _generate_event_types,
    "models": _generate_models,
    "configuration": _generate_configuration,
    "api": _generate_api,
    "cli": _generate_cli,
    "pytest-native": _generate_pytest_native,
    "query-api": _generate_query_api,
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
