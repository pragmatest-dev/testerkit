# pyright: reportAttributeAccessIssue=false, reportCallIssue=false
"""CycloneDX SBOM generation and environment extraction from Parquet files.

Uses cyclonedx-python-lib for proper SBOM generation with schema validation.
Install with: uv pip install 'testerkit[sbom]'

SBOM needs the full installed-package list, which is intentionally excluded
from ``EnvironmentSnapshot`` (the snapshot stores only top-level deps and
a lockfile hash).  This module captures the full list on demand.

Pyright note: ``cyclonedx-python-lib`` decorates all its models with
``@serializable.serializable_class`` which produces a union return type
(``Bom | _JsonSerializable | _XmlSerializable``). Pyright then rejects
every attribute access and kwarg on the resulting classes, even though
the runtime types are correct. We disable the two affected rules at
file scope — this is an upstream stub-shape issue, not a TesterKit bug.
Upstream tracking: https://github.com/CycloneDX/cyclonedx-python-lib
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from testerkit.environment import EnvironmentSnapshot


def environment_from_parquet(parquet_path: Path) -> EnvironmentSnapshot | None:
    """Read EnvironmentSnapshot from Parquet file-level metadata.

    Returns None if the file has no environment data (e.g., older runs).
    """
    import pyarrow.parquet as pq

    metadata = pq.read_metadata(parquet_path)
    file_meta = metadata.metadata
    if file_meta is None:
        return None

    raw = file_meta.get(b"environment_json")
    if raw is None:
        return None

    return EnvironmentSnapshot.model_validate_json(raw)


def _installed_packages() -> list[tuple[str, str]]:
    """Return sorted (name, version) for all installed packages."""
    seen: dict[str, str] = {}
    for d in importlib.metadata.distributions():
        name = d.metadata["Name"]
        if name and name.lower() not in seen:
            seen[name.lower()] = d.metadata["Version"]
    return sorted(seen.items())


def generate_cyclonedx(snapshot: EnvironmentSnapshot) -> str:
    """Convert EnvironmentSnapshot to CycloneDX 1.6 JSON string.

    Captures the full installed-package list at call time for the SBOM
    (not stored in the snapshot).

    Requires cyclonedx-python-lib (install with ``uv pip install 'testerkit[sbom]'``).

    Raises:
        ImportError: If cyclonedx library is not installed.
    """
    try:
        from cyclonedx.model import Property
        from cyclonedx.model.bom import Bom
        from cyclonedx.model.component import Component, ComponentType
        from cyclonedx.model.dependency import Dependency
        from cyclonedx.output.json import JsonV1Dot6
        from packageurl import PackageURL
    except ImportError:
        raise ImportError(
            "cyclonedx-python-lib is required for SBOM export. "
            "Install with: uv pip install 'testerkit[sbom]'"
        ) from None

    bom = Bom()

    # Root component: the test environment itself
    bom.metadata.component = Component(
        name="test-environment",
        type=ComponentType.APPLICATION,
        version=snapshot.lockfile_hash or snapshot.testerkit_version,
    )

    # Tool that generated this SBOM
    bom.metadata.tools.components.add(
        Component(
            name="testerkit",
            type=ComponentType.APPLICATION,
            version=snapshot.testerkit_version,
        )
    )

    # Environment properties
    bom.metadata.properties.add(Property(name="python:version", value=snapshot.python_version))
    bom.metadata.properties.add(Property(name="os:name", value=snapshot.os_name))
    bom.metadata.properties.add(Property(name="os:version", value=snapshot.os_version))
    bom.metadata.properties.add(Property(name="platform:machine", value=snapshot.platform_machine))
    if snapshot.lockfile_hash:
        bom.metadata.properties.add(Property(name="lockfile:hash", value=snapshot.lockfile_hash))

    # Add each installed package as a component
    root_dep = Dependency(ref=bom.metadata.component.bom_ref)

    for name, version in _installed_packages():
        comp = Component(
            name=name,
            type=ComponentType.LIBRARY,
            version=version,
            purl=PackageURL(type="pypi", name=name.lower(), version=version),
        )
        bom.components.add(comp)
        root_dep.dependencies.add(Dependency(ref=comp.bom_ref))

    bom.dependencies.add(root_dep)

    outputter = JsonV1Dot6(bom)
    return outputter.output_as_string(indent=2)


def format_environment_table(snapshot: EnvironmentSnapshot) -> str:
    """Format environment snapshot as a human-readable table."""
    lines = [
        "Environment:",
        f"  Python:    {snapshot.python_version}",
        f"  OS:        {snapshot.os_name} {snapshot.os_version}",
        f"  Machine:   {snapshot.platform_machine}",
        f"  TesterKit:    {snapshot.testerkit_version}",
        f"  Deps:      {len(snapshot.dependencies)}",
    ]
    if snapshot.lockfile_hash:
        lines.append(f"  Lockfile:  {snapshot.lockfile_hash}")
    lines.append("")
    lines.append("  Dependencies:")
    for dep in snapshot.dependencies:
        lines.append(f"    {dep}")
    return "\n".join(lines)
