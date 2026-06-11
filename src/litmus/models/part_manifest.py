"""Part folder manifest model.

The manifest tracks workflow position for a part folder.
Git handles provenance (who/when/what changed).
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class WorkflowStep(StrEnum):
    """Steps in the datasheet-to-test workflow."""

    PARSE_DATASHEET = "parse_datasheet"
    REVIEW_SPEC = "review_spec"
    DERIVE_REQUIREMENTS = "derive_requirements"
    SELECT_STATION = "select_station"
    GENERATE_TESTS = "generate_tests"
    EXECUTE_ANALYZE = "execute_analyze"


WORKFLOW_STEP_ORDER = [
    WorkflowStep.PARSE_DATASHEET,
    WorkflowStep.REVIEW_SPEC,
    WorkflowStep.DERIVE_REQUIREMENTS,
    WorkflowStep.SELECT_STATION,
    WorkflowStep.GENERATE_TESTS,
    WorkflowStep.EXECUTE_ANALYZE,
]


class FileReferences(BaseModel):
    """References to files in the part folder."""

    model_config = {"extra": "forbid"}

    datasheet: str | None = None  # Relative path to datasheet
    spec: str | None = None  # Relative path to spec.yaml
    requirements: str | None = None  # Relative path to requirements.yaml
    station_selection: str | None = None  # Relative path to station_selection.yaml
    tests: str | None = None  # Relative path to test directory or file


class PartManifest(BaseModel):
    """Manifest for a part folder.

    Stored as `parts/{part_id}/manifest.yaml`.

    Example:
        part_id: tps54302
        name: "TPS54302 3A Buck Converter"
        current_step: derive_requirements
        completed_steps:
          - parse_datasheet
          - review_spec
        files:
          datasheet: datasheet.md
          spec: spec.yaml
    """

    model_config = {"extra": "forbid"}

    part_id: str
    name: str
    description: str | None = None
    current_step: WorkflowStep | None = None
    completed_steps: list[WorkflowStep] = Field(default_factory=list)
    files: FileReferences = Field(default_factory=FileReferences)

    def complete_step(self, step: WorkflowStep) -> None:
        """Mark step completed and advance to next."""
        if step not in self.completed_steps:
            self.completed_steps.append(step)

        # Advance to next step
        step_index = WORKFLOW_STEP_ORDER.index(step)
        if step_index < len(WORKFLOW_STEP_ORDER) - 1:
            self.current_step = WORKFLOW_STEP_ORDER[step_index + 1]
        else:
            self.current_step = None

    def is_step_completed(self, step: WorkflowStep) -> bool:
        """Check if a step has been completed."""
        return step in self.completed_steps

    def can_proceed_to(self, step: WorkflowStep) -> bool:
        """Check if we can proceed to a given step."""
        step_index = WORKFLOW_STEP_ORDER.index(step)
        if step_index == 0:
            return True
        # All previous steps must be completed
        for i in range(step_index):
            if WORKFLOW_STEP_ORDER[i] not in self.completed_steps:
                return False
        return True
