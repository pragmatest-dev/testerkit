"""TesterKit ontology — concept graph for models, runtime entities, events.

Lightweight by design: importing this module does not read the YAML.
Call :func:`load_ontology` to parse and validate the bundled file.
"""

from __future__ import annotations

from importlib.resources import files

import yaml

from testerkit.ontology.schema import (
    Category,
    Concept,
    IgnoredEntry,
    Ontology,
    Relationship,
    Slice,
    VerbKind,
)

__all__ = [
    "Category",
    "Concept",
    "IgnoredEntry",
    "Ontology",
    "Relationship",
    "Slice",
    "VerbKind",
    "load_ontology",
]


def load_ontology() -> Ontology:
    """Load and validate the bundled TesterKit ontology."""
    yaml_text = files("testerkit.ontology").joinpath("testerkit.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(yaml_text)
    return Ontology.model_validate(raw)
