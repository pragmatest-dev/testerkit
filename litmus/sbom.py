"""CycloneDX SBOM generation and environment extraction from Parquet files.

Uses cyclonedx-python-lib for proper SBOM generation with schema validation.
Install with: uv pip install 'litmus[sbom]'
"""

from __future__ import annotations

from pathlib import Path

from litmus.environment import EnvironmentSnapshot, _package_sort_key


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


def generate_cyclonedx(snapshot: EnvironmentSnapshot) -> str:
    """Convert EnvironmentSnapshot to CycloneDX 1.6 JSON string.

    Requires cyclonedx-python-lib (install with ``uv pip install 'litmus[sbom]'``).

    Raises:
        ImportError: If cyclonedx library is not installed.
    """
    try:
        from cyclonedx.model import Property
        from cyclonedx.model.bom import Bom
        from cyclonedx.model.component import Component, ComponentType
        from cyclonedx.output.json import JsonV1Dot6
        from packageurl import PackageURL
    except ImportError:
        raise ImportError(
            "cyclonedx-python-lib is required for SBOM export. "
            "Install with: uv pip install 'litmus[sbom]'"
        ) from None

    bom = Bom()

    # Root component: the test environment itself
    bom.metadata.component = Component(
        name="test-environment",
        type=ComponentType.APPLICATION,
        version=snapshot.fingerprint,
    )

    # Tool that generated this SBOM
    bom.metadata.tools.components.add(
        Component(
            name="litmus",
            type=ComponentType.APPLICATION,
            version=snapshot.litmus_version,
        )
    )

    # Environment properties
    bom.metadata.properties.add(
        Property(name="python:version", value=snapshot.python_version)
    )
    bom.metadata.properties.add(
        Property(name="os:name", value=snapshot.os_name)
    )
    bom.metadata.properties.add(
        Property(name="os:version", value=snapshot.os_version)
    )
    bom.metadata.properties.add(
        Property(name="platform:machine", value=snapshot.platform_machine)
    )
    if snapshot.lockfile_hash:
        bom.metadata.properties.add(
            Property(name="lockfile:hash", value=snapshot.lockfile_hash)
        )

    # Add each installed package as a component
    for pkg in sorted(snapshot.packages, key=_package_sort_key):
        bom.components.add(
            Component(
                name=pkg.name,
                type=ComponentType.LIBRARY,
                version=pkg.version,
                purl=PackageURL(type="pypi", name=pkg.name.lower(), version=pkg.version),
            )
        )

    outputter = JsonV1Dot6(bom)
    return outputter.output_as_string(indent=2)


def format_environment_table(snapshot: EnvironmentSnapshot) -> str:
    """Format environment snapshot as a human-readable table."""
    lines = [
        "Environment:",
        f"  Python:    {snapshot.python_version}",
        f"  OS:        {snapshot.os_name} {snapshot.os_version}",
        f"  Machine:   {snapshot.platform_machine}",
        f"  Litmus:    {snapshot.litmus_version}",
        f"  Packages:  {len(snapshot.packages)}",
        f"  Fingerprint: {snapshot.fingerprint}",
    ]
    if snapshot.lockfile_hash:
        lines.append(f"  Lockfile:  {snapshot.lockfile_hash}")
    lines.append("")
    lines.append("  Installed packages:")
    for pkg in sorted(snapshot.packages, key=_package_sort_key):
        lines.append(f"    {pkg.name} {pkg.version}")
    return "\n".join(lines)
