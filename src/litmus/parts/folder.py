"""Part folder operations.

PartFolder provides CRUD operations for part folders, handling:
- Creating new part folders with manifest
- Loading existing parts
- Saving/updating files within the folder
- Listing all parts in a directory
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import yaml

from litmus.models.part import Part
from litmus.models.part_manifest import (
    PartManifest,
    WorkflowStep,
)
from litmus.store import dump_yaml, load_manifest, load_part
from litmus.store import save_manifest as _store_save_manifest

logger = logging.getLogger(__name__)


class PartFolder:
    """Manages a part folder on disk.

    A part folder contains:
        parts/{part_id}/
            manifest.yaml       # Workflow state and metadata
            datasheet.md        # Source document (optional)
            spec.yaml           # Part specification
            requirements.yaml   # Derived requirements (optional)
            station_selection.yaml  # Station mapping (optional)

    Example usage:
        # Create new part folder
        folder = PartFolder.create(
            base_path=Path("parts"),
            part_id="tps54302",
            name="TPS54302 Buck Converter",
        )

        # Load existing
        folder = PartFolder.load(Path("parts/tps54302"))

        # Save spec
        folder.save_spec(part)

        # Update workflow state
        folder.manifest.complete_step(WorkflowStep.PARSE_DATASHEET)
        folder.save_manifest()
    """

    def __init__(self, path: Path, manifest: PartManifest):
        """Initialize with folder path and loaded manifest.

        Use PartFolder.create() or PartFolder.load() instead of
        calling this directly.
        """
        self.path = path
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        base_path: Path,
        part_id: str,
        name: str,
        description: str | None = None,
    ) -> "PartFolder":
        """Create a new part folder.

        Args:
            base_path: Parent directory (e.g., Path("parts"))
            part_id: Unique identifier for the part
            name: Human-readable part name
            description: Optional description

        Returns:
            PartFolder instance for the new folder

        Raises:
            FileExistsError: If folder already exists
        """
        folder_path = base_path / part_id
        if folder_path.exists():
            raise FileExistsError(f"Part folder already exists: {folder_path}")

        # Create folder
        folder_path.mkdir(parents=True)

        # Create manifest
        manifest = PartManifest(
            part_id=part_id,
            name=name,
            description=description,
            current_step=WorkflowStep.PARSE_DATASHEET,
        )

        # Save manifest
        _store_save_manifest(manifest, folder_path / "manifest.yaml")

        return cls(folder_path, manifest)

    @classmethod
    def load(cls, path: Path) -> "PartFolder":
        """Load an existing part folder.

        Args:
            path: Path to the part folder

        Returns:
            PartFolder instance

        Raises:
            FileNotFoundError: If folder or manifest doesn't exist
        """
        if not path.exists():
            raise FileNotFoundError(f"Part folder not found: {path}")

        manifest_path = path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        manifest = load_manifest(manifest_path)
        return cls(path, manifest)

    @classmethod
    def list_all(cls, base_path: Path) -> Iterator["PartFolder"]:
        """List all part folders in a directory.

        Args:
            base_path: Parent directory containing part folders

        Yields:
            PartFolder instances for each valid part folder
        """
        if not base_path.exists():
            return

        for item in sorted(base_path.iterdir()):
            if item.is_dir() and (item / "manifest.yaml").exists():
                try:
                    yield cls.load(item)
                except (FileNotFoundError, OSError, ValueError, yaml.YAMLError) as exc:
                    logger.debug("Skipping invalid part folder %s: %s", item, exc)
                    continue

    def save_manifest(self) -> None:
        """Save the manifest to disk."""
        _store_save_manifest(self.manifest, self.path / "manifest.yaml")

    def save_datasheet(self, content: str, filename: str = "datasheet.md") -> Path:
        """Save datasheet content to the folder.

        Args:
            content: Datasheet content (markdown, text, etc.)
            filename: Filename to use (default: datasheet.md)

        Returns:
            Path to the saved file
        """
        file_path = self.path / filename
        file_path.write_text(content)

        self.manifest.files.datasheet = filename
        self.save_manifest()
        return file_path

    def save_spec(self, part: Part, filename: str = "spec.yaml") -> Path:
        """Save part specification to the folder.

        Args:
            part: Part model to save
            filename: Filename to use (default: spec.yaml)

        Returns:
            Path to the saved file
        """
        file_path = self.path / filename
        file_path.write_text(dump_yaml({"part": part.model_dump(exclude_none=True)}))

        self.manifest.files.spec = filename
        self.save_manifest()
        return file_path

    def load_spec(self) -> Part | None:
        """Load the part specification from the folder.

        Returns:
            Part model or None if spec doesn't exist
        """
        if not self.manifest.files.spec:
            return None

        spec_path = self.path / self.manifest.files.spec
        if not spec_path.exists():
            return None

        return load_part(spec_path)

    def load_datasheet(self) -> str | None:
        """Load the datasheet content from the folder.

        Returns:
            Datasheet content or None if it doesn't exist
        """
        if not self.manifest.files.datasheet:
            return None

        datasheet_path = self.path / self.manifest.files.datasheet
        if not datasheet_path.exists():
            return None

        return datasheet_path.read_text()

    def get_file_path(self, file_type: str) -> Path | None:
        """Get the full path for a file type.

        Args:
            file_type: One of 'datasheet', 'spec', 'requirements',
                      'station_selection', 'tests'

        Returns:
            Full path to the file or None if not set
        """
        filename = getattr(self.manifest.files, file_type, None)
        if filename:
            return self.path / filename
        return None

    @property
    def part_id(self) -> str:
        """Get the part ID."""
        return self.manifest.part_id

    @property
    def name(self) -> str:
        """Get the part name."""
        return self.manifest.name

    @property
    def current_step(self) -> WorkflowStep | None:
        """Get the current workflow step."""
        return self.manifest.current_step

    def __repr__(self) -> str:
        return f"PartFolder({self.part_id!r}, step={self.current_step})"
