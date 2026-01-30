"""Product folder manifest model.

The manifest tracks workflow state for a product folder, enabling:
- Resume workflow from any step
- Track history of AI/human actions
- Reference related files (datasheet, spec, tests)
"""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

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


class HistoryEntry(BaseModel):
    """A single entry in the workflow history."""

    step: WorkflowStep
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent: str | None = None  # "claude", "gpt-4", etc.
    confidence: float | None = None  # 0.0-1.0
    edited_by_user: bool = False
    notes: str | None = None


class WorkflowState(BaseModel):
    """Current state of the workflow."""

    current_step: WorkflowStep | None = None
    completed_steps: list[WorkflowStep] = Field(default_factory=list)

    def is_step_completed(self, step: WorkflowStep) -> bool:
        """Check if a step has been completed."""
        return step in self.completed_steps

    def get_step_index(self) -> int:
        """Get 0-based index of current step."""
        if self.current_step is None:
            return -1
        return WORKFLOW_STEP_ORDER.index(self.current_step)

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


class FileReferences(BaseModel):
    """References to files in the product folder."""

    datasheet: str | None = None  # Relative path to datasheet
    spec: str | None = None  # Relative path to spec.yaml
    requirements: str | None = None  # Relative path to requirements.yaml
    station_selection: str | None = None  # Relative path to station_selection.yaml
    tests: str | None = None  # Relative path to test file


class ProductManifest(BaseModel):
    """Manifest for a product folder.

    Stored as `products/{product_id}/manifest.yaml`.

    Example:
        product_id: tps54302
        name: "TPS54302 3A Buck Converter"
        created_at: 2026-01-29T12:00:00Z

        workflow:
          current_step: derive_requirements
          completed_steps:
            - parse_datasheet
            - review_spec

        files:
          datasheet: datasheet.md
          spec: spec.yaml

        history:
          - step: parse_datasheet
            timestamp: 2026-01-29T12:01:00Z
            agent: claude
            confidence: 0.94
    """

    product_id: str
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str | None = None

    workflow: WorkflowState = Field(default_factory=WorkflowState)
    files: FileReferences = Field(default_factory=FileReferences)
    history: list[HistoryEntry] = Field(default_factory=list)

    # Arbitrary metadata
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_history_entry(
        self,
        step: WorkflowStep,
        agent: str | None = None,
        confidence: float | None = None,
        edited_by_user: bool = False,
        notes: str | None = None,
    ) -> HistoryEntry:
        """Add an entry to the workflow history."""
        entry = HistoryEntry(
            step=step,
            agent=agent,
            confidence=confidence,
            edited_by_user=edited_by_user,
            notes=notes,
        )
        self.history.append(entry)
        return entry

    def complete_step(
        self,
        step: WorkflowStep,
        agent: str | None = None,
        confidence: float | None = None,
        edited_by_user: bool = False,
    ) -> None:
        """Mark a step as completed and advance workflow."""
        if step not in self.workflow.completed_steps:
            self.workflow.completed_steps.append(step)

        self.add_history_entry(
            step=step,
            agent=agent,
            confidence=confidence,
            edited_by_user=edited_by_user,
        )

        # Advance to next step
        step_index = WORKFLOW_STEP_ORDER.index(step)
        if step_index < len(WORKFLOW_STEP_ORDER) - 1:
            self.workflow.current_step = WORKFLOW_STEP_ORDER[step_index + 1]

    def get_progress_percentage(self) -> float:
        """Get workflow progress as percentage (0-100)."""
        if not self.workflow.completed_steps:
            return 0.0
        return (len(self.workflow.completed_steps) / len(WORKFLOW_STEP_ORDER)) * 100
