"""Build human + machine-readable views of the TesterKit ontology.

Reads the bundled ontology, cross-validates it against the Pydantic
source tree, and emits:

  * docs/reference/ontology/index.md          rendered glossary
  * docs/reference/ontology/graph.json        machine-readable form
  * docs/reference/ontology/slices/<id>.md    one Mermaid diagram per slice

Pyvis interactive HTML is intentionally deferred — it requires pyvis +
networkx, which are not yet in the lockfile. Add them and a second
script when ready.

Usage:
    uv run python scripts/build_ontology_docs.py
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import sys
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

from testerkit.ontology import Concept, Ontology, Slice, VerbKind, load_ontology

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_OUT = REPO_ROOT / "docs" / "reference" / "ontology"
SLICES_OUT = DOCS_OUT / "slices"

# Packages whose BaseModel subclasses must be referenced by the ontology
# (or explicitly listed in ontology_ignored:).
COVERED_MODULES = (
    "testerkit.models",
    "testerkit.data.models",
    "testerkit.data.events",
    "testerkit.data.channels.models",
    "testerkit.models.instrument_asset",
)

# Mermaid edge-style mapping per verb category. Thick = semantic / type;
# dotted = naming (FK by id); everything else = plain solid arrow.
THICK_VERBS = {
    VerbKind.TESTS,
    VerbKind.RUNS_ON,
    VerbKind.INSTANCE_OF,
    VerbKind.INSTANTIATED_AS,
    VerbKind.VALIDATES_AGAINST,
    VerbKind.RESOLVES_TO,
    VerbKind.DERIVES_FROM,
}
DOTTED_VERBS = {VerbKind.REFERENCES, VerbKind.EXTENDS}

# Per-category Mermaid classDef stanzas. Same palette as the planned
# Pyvis HTML so the static and interactive views match.
CATEGORY_STYLES = {
    "definition": "fill:#dde9f5,stroke:#345e8f,color:#1a2a3f",
    "primitive": "fill:#ebeef2,stroke:#6b7682,color:#2a3138",
    "config-overlay": "fill:#f1dcec,stroke:#824073,color:#3a1a32",
    "runtime": "fill:#dcecdc,stroke:#3f7e3f,color:#1a3a1a",
    "event": "fill:#f5dcc1,stroke:#8a5a26,color:#3f2810",
    "store": "fill:#f5e7b8,stroke:#8a7026,color:#3f3210",
    "enum": "fill:#e8e8c4,stroke:#75752a,color:#33330f",
    "lifecycle": "fill:#fff2c4,stroke:#7a6620,color:#33290a",
    "external": "fill:#dddddd,stroke:#666666,color:#222,stroke-dasharray:4",
}

HIGHLIGHT_STYLE = "stroke-width:3px"


# ---------------------------------------------------------------------------
# Cross-validation against Pydantic source
# ---------------------------------------------------------------------------


def _iter_basemodels(module_name: str) -> Iterable[tuple[str, type[BaseModel]]]:
    """Yield ``(dotted_path, cls)`` for every BaseModel under ``module_name``."""
    pkg = importlib.import_module(module_name)
    yield from _models_in_module(pkg)
    if not hasattr(pkg, "__path__"):
        return
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{module_name}."):
        try:
            mod = importlib.import_module(info.name)
        except ImportError:
            continue
        yield from _models_in_module(mod)


def _models_in_module(mod: object) -> Iterable[tuple[str, type[BaseModel]]]:
    for name, cls in inspect.getmembers(mod, inspect.isclass):
        if not issubclass(cls, BaseModel) or cls is BaseModel:
            continue
        if cls.__module__ != mod.__name__:  # type: ignore[attr-defined]
            continue
        yield f"{cls.__module__}.{name}", cls


def cross_validate(ontology: Ontology) -> list[str]:
    """Return a list of drift problems; empty list = clean."""
    problems: list[str] = []

    # If a concept names a model, it must resolve. Most categories expect a
    # BaseModel; `store` concepts point at runtime data-access classes;
    # `enum` concepts point at StrEnum. Model-less concepts (session,
    # testerkit_marker, pytest_test_function) are permitted in any category.
    for concept in ontology.concepts:
        if concept.model is None:
            continue
        try:
            mod_path, _, cls_name = concept.model.rpartition(".")
            module = importlib.import_module(mod_path)
            cls = getattr(module, cls_name)
        except (ImportError, AttributeError):
            problems.append(f"{concept.id}: model {concept.model!r} does not resolve")
            continue
        if not inspect.isclass(cls):
            problems.append(f"{concept.id}: model {concept.model!r} is not a class")
            continue
        category = concept.category.value
        if category in ("store", "enum"):
            continue  # non-BaseModel is expected
        if not issubclass(cls, BaseModel):
            problems.append(
                f"{concept.id}: model {concept.model!r} is not a BaseModel (category={category!r})"
            )

    # Every BaseModel under COVERED_MODULES must be either referenced as a
    # concept.model or listed in ontology_ignored.
    referenced = {c.model for c in ontology.concepts if c.model}
    ignored = {entry.model for entry in ontology.ontology_ignored}
    seen: set[str] = set()
    for module_name in COVERED_MODULES:
        for dotted, _ in _iter_basemodels(module_name):
            if dotted in seen:
                continue
            seen.add(dotted)
            if dotted in referenced or dotted in ignored:
                continue
            problems.append(f"drift: {dotted} is a BaseModel but not in ontology or ignored")

    return problems


# ---------------------------------------------------------------------------
# Glossary (index.md)
# ---------------------------------------------------------------------------


def _concept_anchor(concept_id: str) -> str:
    return concept_id.replace("_", "-")


def render_glossary(ontology: Ontology) -> str:
    by_category: dict[str, list[Concept]] = {}
    for c in ontology.concepts:
        by_category.setdefault(c.category.value, []).append(c)

    lines: list[str] = []
    lines.append("# TesterKit Ontology — Glossary")
    lines.append("")
    lines.append(
        "Every TesterKit concept, its canonical Pydantic model, and how it "
        "relates to the others. Generated from "
        "`src/testerkit/ontology/testerkit.yaml`; do not hand-edit."
    )
    lines.append("")
    lines.append(f"**Version:** {ontology.version}  ·  **Concepts:** {len(ontology.concepts)}")
    lines.append("")

    for category in (
        "definition",
        "primitive",
        "config-overlay",
        "runtime",
        "store",
        "event",
        "enum",
        "lifecycle",
        "external",
    ):
        concepts = by_category.get(category)
        if not concepts:
            continue
        lines.append(f"## {category}")
        lines.append("")
        for concept in sorted(concepts, key=lambda c: c.id):
            lines.append(f"### {concept.id} {{#{_concept_anchor(concept.id)}}}")
            lines.append("")
            lines.append(concept.summary.strip())
            lines.append("")
            if concept.model:
                lines.append(f"- **Model:** `{concept.model}`")
            if concept.authored_at:
                lines.append(f"- **Authored at:** `{concept.authored_at}`")
            if concept.event_type:
                lines.append(f"- **Event type:** `{concept.event_type}`")
            if concept.docs:
                lines.append(f"- **Concept doc:** [{concept.docs}](/{concept.docs})")
            if concept.relationships:
                lines.append("- **Relationships:**")
                for rel in concept.relationships:
                    anchor = _concept_anchor(rel.target)
                    lines.append(f"    - `{rel.kind.value}` → [{rel.target}](#{anchor})")
            lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Slices (one Mermaid file per slice)
# ---------------------------------------------------------------------------


def _mermaid_arrow(kind: VerbKind) -> str:
    """Return the arrow token to put between source and ``|label| target``."""
    if kind in THICK_VERBS:
        return "==>"
    if kind in DOTTED_VERBS:
        return "-.->"
    return "-->"


def _mermaid_label(concept: Concept) -> str:
    """Display label for a node — model class name when available."""
    if concept.model:
        return concept.model.rpartition(".")[-1]
    # Fall back to title-cased concept id for model-less concepts.
    return "".join(word.capitalize() for word in concept.id.split("_"))


def render_slice(ontology: Ontology, slice_: Slice) -> str:
    by_id = {c.id: c for c in ontology.concepts}
    members = [by_id[cid] for cid in slice_.concepts]
    member_ids = set(slice_.concepts)
    allowed_edges = set(slice_.edges) if slice_.edges else None

    lines: list[str] = []
    lines.append(f"# {slice_.title}")
    lines.append("")
    lines.append(slice_.summary.strip())
    lines.append("")
    lines.append("```mermaid")
    lines.append(f"flowchart {slice_.layout}")

    # classDef for each category that appears in this slice
    cats_in_slice = {c.category.value for c in members}
    for cat in sorted(cats_in_slice):
        style = CATEGORY_STYLES.get(cat, "")
        lines.append(f"    classDef {cat.replace('-', '_')} {style}")
    lines.append(f"    classDef highlight {HIGHLIGHT_STYLE}")

    # Nodes
    for concept in members:
        label = _mermaid_label(concept)
        css = concept.category.value.replace("-", "_")
        lines.append(f"    {concept.id}[{label}]:::{css}")
    for h in slice_.highlight:
        lines.append(f"    class {h} highlight")

    # Edges (only when both endpoints are in the slice, and verb is allowed)
    for concept in members:
        for rel in concept.relationships:
            if rel.target not in member_ids:
                continue
            if allowed_edges is not None and rel.kind not in allowed_edges:
                continue
            arrow = _mermaid_arrow(rel.kind)
            lines.append(f"    {concept.id} {arrow}|{rel.kind.value}| {rel.target}")

    lines.append("```")
    lines.append("")
    lines.append("## Concepts in this slice")
    lines.append("")
    for concept in sorted(members, key=lambda c: c.id):
        link = f"../index.md#{_concept_anchor(concept.id)}"
        lines.append(f"- [{concept.id}]({link}) — {concept.summary.strip().splitlines()[0]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Machine-readable graph.json
# ---------------------------------------------------------------------------


def _graph_data(ontology: Ontology) -> dict[str, object]:
    nodes = []
    edges = []
    for concept in ontology.concepts:
        nodes.append(
            {
                "id": concept.id,
                "label": _mermaid_label(concept),
                "category": concept.category.value,
                "model": concept.model,
                "authored_at": concept.authored_at,
                "event_type": concept.event_type,
                "summary": concept.summary.strip(),
                "docs": concept.docs,
            }
        )
        for rel in concept.relationships:
            edges.append({"source": concept.id, "target": rel.target, "kind": rel.kind.value})
    return {"version": ontology.version, "nodes": nodes, "edges": edges}


def render_graph_json(ontology: Ontology) -> str:
    return json.dumps(_graph_data(ontology), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Interactive static HTML (vis-network from CDN — no Python runtime deps)
# ---------------------------------------------------------------------------

# Verb → category for edge styling. Mirrors the Mermaid edge-style choices
# in render_slice so the static and interactive views are visually aligned.
VERB_CATEGORIES = {
    # Domain composition — every domain verb shares the same visual category.
    VerbKind.EXPOSES: "composition",
    VerbKind.SPECIFIES: "composition",
    VerbKind.BUNDLES: "composition",
    VerbKind.PARAMETERIZED_BY: "composition",
    VerbKind.EQUIPS: "composition",
    VerbKind.OFFERS: "composition",
    VerbKind.IDENTIFIES: "composition",
    VerbKind.CALIBRATED_PER: "composition",
    VerbKind.WIRES: "composition",
    VerbKind.PARTITIONS_INTO: "composition",
    VerbKind.ROUTED_THROUGH: "composition",
    VerbKind.DECLARES: "composition",
    VerbKind.CONFIGURES: "composition",
    VerbKind.NESTS: "composition",
    VerbKind.RESOLVES_VIA: "composition",
    VerbKind.APPLIES_STIMULUS: "composition",
    # Temporal containment.
    VerbKind.CONTAINS: "temporal",
    # Class / type-instance.
    VerbKind.INHERITS_FROM: "type",
    VerbKind.INSTANCE_OF: "type",
    VerbKind.INSTANTIATED_AS: "type",
    # Naming pointers.
    VerbKind.REFERENCES: "naming",
    VerbKind.EXTENDS: "naming",
    # Execution + derivation semantics.
    VerbKind.TESTS: "semantic",
    VerbKind.RUNS_ON: "semantic",
    VerbKind.VALIDATES_AGAINST: "semantic",
    VerbKind.RESOLVES_TO: "semantic",
    VerbKind.DERIVES_FROM: "semantic",
    # Event semantics.
    VerbKind.EMITS: "event",
    VerbKind.RECORDS: "event",
    VerbKind.PAIRED_WITH: "event",
    # Config layering.
    VerbKind.OVERLAYS: "overlay",
    # Storage.
    VerbKind.STORED_IN: "storage",
}


_GRAPH_TEMPLATE_PATH = Path(__file__).resolve().parent / "_ontology_graph_template.html"


def render_graph_html(ontology: Ontology) -> str:
    data = _graph_data(ontology)
    verb_categories = {k.value: v for k, v in VERB_CATEGORIES.items()}
    payload = json.dumps(
        {**data, "verb_categories": verb_categories},
        ensure_ascii=False,
    )
    template = _GRAPH_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__ONTOLOGY_JSON__", payload)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ontology = load_ontology()

    problems = cross_validate(ontology)
    if problems:
        print("Ontology drift detected:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    DOCS_OUT.mkdir(parents=True, exist_ok=True)
    SLICES_OUT.mkdir(parents=True, exist_ok=True)

    (DOCS_OUT / "index.md").write_text(render_glossary(ontology), encoding="utf-8")
    (DOCS_OUT / "graph.json").write_text(render_graph_json(ontology), encoding="utf-8")
    (DOCS_OUT / "graph.html").write_text(render_graph_html(ontology), encoding="utf-8")

    for slice_ in ontology.slices:
        (SLICES_OUT / f"{slice_.id}.md").write_text(
            render_slice(ontology, slice_), encoding="utf-8"
        )

    print(f"wrote {DOCS_OUT}/index.md")
    print(f"wrote {DOCS_OUT}/graph.json")
    print(f"wrote {len(ontology.slices)} slices to {SLICES_OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
