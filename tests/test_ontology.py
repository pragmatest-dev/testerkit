"""Ontology integrity tests.

The TesterKit ontology at ``src/testerkit/ontology/testerkit.yaml`` is the
declared concept graph. These tests enforce that:

1. The YAML loads and Pydantic validation passes — internal consistency
   (relationship targets exist, slice members are real concepts, etc.).
2. Every Pydantic ``BaseModel`` under the covered source modules is
   either referenced by a concept or explicitly listed in
   ``ontology_ignored:`` with a reason. New models silently added
   without an ontology entry will fail this test.
3. Every ``model:`` named in the ontology resolves to a real class.

When this test fails, the fix is to edit
``src/testerkit/ontology/testerkit.yaml`` — either add a concept for the new
model, or add it to ``ontology_ignored:`` with a one-line reason.
"""

from __future__ import annotations

from scripts.build_ontology_docs import cross_validate
from testerkit.ontology import load_ontology


def test_ontology_loads_and_validates() -> None:
    """Pydantic validation of the YAML file catches internal drift."""
    ontology = load_ontology()
    assert ontology.version >= 1
    assert ontology.concepts, "ontology must declare at least one concept"


def test_ontology_no_drift_against_source_models() -> None:
    """Every BaseModel under covered modules must be in the ontology or ignored."""
    ontology = load_ontology()
    problems = cross_validate(ontology)
    if problems:
        msg = "Ontology drift — fix src/testerkit/ontology/testerkit.yaml:\n  - " + "\n  - ".join(
            problems
        )
        raise AssertionError(msg)
