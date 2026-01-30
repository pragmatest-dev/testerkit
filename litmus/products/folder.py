"""Product folder operations.

ProductFolder provides CRUD operations for product folders, handling:
- Creating new product folders with manifest
- Loading existing products
- Saving/updating files within the folder
- Listing all products in a directory
"""

from pathlib import Path
from typing import Iterator

import yaml

from litmus.products.manifest import (
    FileReferences,
    ProductManifest,
    WorkflowState,
    WorkflowStep,
)
from litmus.products.models import Product


class ProductFolder:
    """Manages a product folder on disk.

    A product folder contains:
        products/{product_id}/
            manifest.yaml       # Workflow state and metadata
            datasheet.md        # Source document (optional)
            spec.yaml           # Product specification
            requirements.yaml   # Derived requirements (optional)
            station_selection.yaml  # Station mapping (optional)

    Example usage:
        # Create new product folder
        folder = ProductFolder.create(
            base_path=Path("products"),
            product_id="tps54302",
            name="TPS54302 Buck Converter",
        )

        # Load existing
        folder = ProductFolder.load(Path("products/tps54302"))

        # Save spec
        folder.save_spec(product)

        # Update workflow state
        folder.manifest.complete_step(WorkflowStep.PARSE_DATASHEET, agent="claude")
        folder.save_manifest()
    """

    def __init__(self, path: Path, manifest: ProductManifest):
        """Initialize with folder path and loaded manifest.

        Use ProductFolder.create() or ProductFolder.load() instead of
        calling this directly.
        """
        self.path = path
        self.manifest = manifest

    @classmethod
    def create(
        cls,
        base_path: Path,
        product_id: str,
        name: str,
        description: str | None = None,
        created_by: str | None = None,
    ) -> "ProductFolder":
        """Create a new product folder.

        Args:
            base_path: Parent directory (e.g., Path("products"))
            product_id: Unique identifier for the product
            name: Human-readable product name
            description: Optional description
            created_by: Optional creator identifier

        Returns:
            ProductFolder instance for the new folder

        Raises:
            FileExistsError: If folder already exists
        """
        folder_path = base_path / product_id
        if folder_path.exists():
            raise FileExistsError(f"Product folder already exists: {folder_path}")

        # Create folder
        folder_path.mkdir(parents=True)

        # Create manifest
        manifest = ProductManifest(
            product_id=product_id,
            name=name,
            description=description,
            created_by=created_by,
            workflow=WorkflowState(current_step=WorkflowStep.PARSE_DATASHEET),
        )

        # Save manifest
        manifest_path = folder_path / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(
                manifest.model_dump(mode="json", exclude_none=True),
                f,
                default_flow_style=False,
                sort_keys=False,
            )

        return cls(folder_path, manifest)

    @classmethod
    def load(cls, path: Path) -> "ProductFolder":
        """Load an existing product folder.

        Args:
            path: Path to the product folder

        Returns:
            ProductFolder instance

        Raises:
            FileNotFoundError: If folder or manifest doesn't exist
        """
        if not path.exists():
            raise FileNotFoundError(f"Product folder not found: {path}")

        manifest_path = path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        with open(manifest_path) as f:
            data = yaml.safe_load(f)

        manifest = ProductManifest.model_validate(data)
        return cls(path, manifest)

    @classmethod
    def list_all(cls, base_path: Path) -> Iterator["ProductFolder"]:
        """List all product folders in a directory.

        Args:
            base_path: Parent directory containing product folders

        Yields:
            ProductFolder instances for each valid product folder
        """
        if not base_path.exists():
            return

        for item in sorted(base_path.iterdir()):
            if item.is_dir() and (item / "manifest.yaml").exists():
                try:
                    yield cls.load(item)
                except Exception:
                    # Skip invalid folders
                    continue

    def save_manifest(self) -> None:
        """Save the manifest to disk."""
        manifest_path = self.path / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(
                self.manifest.model_dump(mode="json", exclude_none=True),
                f,
                default_flow_style=False,
                sort_keys=False,
            )

    def save_datasheet(self, content: str, filename: str = "datasheet.md") -> Path:
        """Save datasheet content to the folder.

        Args:
            content: Datasheet content (markdown, text, etc.)
            filename: Filename to use (default: datasheet.md)

        Returns:
            Path to the saved file
        """
        file_path = self.path / filename
        with open(file_path, "w") as f:
            f.write(content)

        self.manifest.files.datasheet = filename
        self.save_manifest()
        return file_path

    def save_spec(self, product: Product, filename: str = "spec.yaml") -> Path:
        """Save product specification to the folder.

        Args:
            product: Product model to save
            filename: Filename to use (default: spec.yaml)

        Returns:
            Path to the saved file
        """
        file_path = self.path / filename
        with open(file_path, "w") as f:
            yaml.dump(
                {"product": product.model_dump(mode="json", exclude_none=True)},
                f,
                default_flow_style=False,
                sort_keys=False,
            )

        self.manifest.files.spec = filename
        self.save_manifest()
        return file_path

    def load_spec(self) -> Product | None:
        """Load the product specification from the folder.

        Returns:
            Product model or None if spec doesn't exist
        """
        if not self.manifest.files.spec:
            return None

        spec_path = self.path / self.manifest.files.spec
        if not spec_path.exists():
            return None

        with open(spec_path) as f:
            data = yaml.safe_load(f)

        if "product" in data:
            from litmus.products.loader import _parse_product

            return _parse_product(data)
        return None

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

        with open(datasheet_path) as f:
            return f.read()

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
    def product_id(self) -> str:
        """Get the product ID."""
        return self.manifest.product_id

    @property
    def name(self) -> str:
        """Get the product name."""
        return self.manifest.name

    @property
    def current_step(self) -> WorkflowStep | None:
        """Get the current workflow step."""
        return self.manifest.workflow.current_step

    def __repr__(self) -> str:
        return f"ProductFolder({self.product_id!r}, step={self.current_step})"
