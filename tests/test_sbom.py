"""Tests for litmus.sbom — SBOM generation and environment extraction."""

import json

from litmus.environment import EnvironmentSnapshot, PackageInfo
from litmus.sbom import environment_from_parquet, format_environment_table, generate_cyclonedx


def _make_snapshot(**overrides) -> EnvironmentSnapshot:
    defaults = dict(
        python_version="3.12.0", os_name="Linux", os_version="6.0",
        platform_machine="x86_64", litmus_version="0.1.0",
        packages=[
            PackageInfo(name="alpha", version="1.0"),
            PackageInfo(name="beta", version="2.0"),
        ],
    )
    defaults.update(overrides)
    return EnvironmentSnapshot(**defaults)


class TestFormatEnvironmentTable:
    def test_contains_key_fields(self):
        snap = _make_snapshot()
        table = format_environment_table(snap)
        assert "3.12.0" in table
        assert "Linux" in table
        assert "0.1.0" in table
        assert "alpha 1.0" in table
        assert "beta 2.0" in table

    def test_includes_lockfile_hash(self):
        snap = _make_snapshot(lockfile_hash="abc123def456")
        table = format_environment_table(snap)
        assert "abc123def456" in table

    def test_omits_lockfile_when_none(self):
        snap = _make_snapshot(lockfile_hash=None)
        table = format_environment_table(snap)
        assert "Lockfile" not in table

    def test_packages_sorted(self):
        snap = _make_snapshot(packages=[
            PackageInfo(name="Zeta", version="1.0"),
            PackageInfo(name="alpha", version="2.0"),
        ])
        table = format_environment_table(snap)
        alpha_pos = table.index("alpha")
        zeta_pos = table.index("Zeta")
        assert alpha_pos < zeta_pos


class TestEnvironmentFromParquet:
    def test_returns_none_for_file_without_metadata(self, tmp_path):
        """Parquet file without environment metadata returns None."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"x": [1, 2, 3]})
        path = tmp_path / "no_env.parquet"
        pq.write_table(table, path)

        result = environment_from_parquet(path)
        assert result is None

    def test_roundtrip_through_parquet(self, tmp_path):
        """Write snapshot to parquet metadata, read it back."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        snap = _make_snapshot()
        json_bytes = snap.model_dump_json().encode("utf-8")

        table = pa.table({"x": [1, 2, 3]})
        metadata = table.schema.metadata or {}
        metadata[b"environment_json"] = json_bytes
        table = table.replace_schema_metadata(metadata)

        path = tmp_path / "with_env.parquet"
        pq.write_table(table, path)

        restored = environment_from_parquet(path)
        assert restored is not None
        assert restored.python_version == "3.12.0"
        assert len(restored.packages) == 2
        assert restored.packages[0].name == "alpha"


class TestGenerateCyclonedx:
    def test_returns_valid_json(self):
        snap = _make_snapshot()
        result = generate_cyclonedx(snap)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_bom_format(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        assert parsed.get("bomFormat") == "CycloneDX"

    def test_spec_version_1_6(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        assert parsed.get("specVersion") == "1.6"

    def test_metadata_component_is_test_environment(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        meta_component = parsed["metadata"]["component"]
        assert meta_component["name"] == "test-environment"
        assert meta_component["version"] == snap.fingerprint
        assert meta_component["type"] == "application"

    def test_metadata_has_litmus_tool(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        tools = parsed["metadata"]["tools"]
        tool_names = [c["name"] for c in tools.get("components", [])]
        assert "litmus" in tool_names

    def test_metadata_properties_contain_python_version(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        props = {p["name"]: p["value"] for p in parsed["metadata"]["properties"]}
        assert props["python:version"] == "3.12.0"
        assert props["os:name"] == "Linux"
        assert props["os:version"] == "6.0"
        assert props["platform:machine"] == "x86_64"

    def test_metadata_properties_include_lockfile_hash(self):
        snap = _make_snapshot(lockfile_hash="deadbeef12345678")
        parsed = json.loads(generate_cyclonedx(snap))
        props = {p["name"]: p["value"] for p in parsed["metadata"]["properties"]}
        assert props["lockfile:hash"] == "deadbeef12345678"

    def test_metadata_properties_omit_lockfile_when_none(self):
        snap = _make_snapshot(lockfile_hash=None)
        parsed = json.loads(generate_cyclonedx(snap))
        prop_names = [p["name"] for p in parsed["metadata"]["properties"]]
        assert "lockfile:hash" not in prop_names

    def test_components_match_packages(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        components = parsed.get("components", [])
        component_names = {c["name"] for c in components}
        assert component_names == {"alpha", "beta"}

    def test_component_has_purl(self):
        snap = _make_snapshot(packages=[PackageInfo(name="MyPkg", version="3.0")])
        parsed = json.loads(generate_cyclonedx(snap))
        comp = parsed["components"][0]
        assert comp["purl"] == "pkg:pypi/mypkg@3.0"

    def test_component_type_is_library(self):
        snap = _make_snapshot()
        parsed = json.loads(generate_cyclonedx(snap))
        for comp in parsed["components"]:
            assert comp["type"] == "library"

    def test_empty_packages(self):
        snap = _make_snapshot(packages=[])
        parsed = json.loads(generate_cyclonedx(snap))
        assert parsed.get("components", []) == []
