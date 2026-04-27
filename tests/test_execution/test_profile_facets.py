"""Profile ``extends:`` chain + ``profiles/*.yaml`` discovery.

Unit tests for the pure resolver (``flatten_profile_chain``) and for
``load_project`` picking up one-file-per-profile YAMLs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from litmus.config.test_config import MeasurementLimitConfig, SweepEntry, TestEntry
from litmus.execution.profiles import (
    ProfileError,
    flatten_profile_chain,
    resolve_active_profile,
)
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.store import load_project


def _make_project(profiles: dict[str, ProfileConfig]) -> ProjectConfig:
    return ProjectConfig(name="p", profiles=profiles)


def _v_rail(**kwargs: Any) -> dict[str, MeasurementLimitConfig]:
    """Build a ``limits={v_rail: MeasurementLimitConfig(...)}`` dict."""
    return {"v_rail": MeasurementLimitConfig.model_validate(dict(kwargs))}


def _sweep(**kwargs: Any) -> SweepEntry:
    """Build a single :class:`SweepEntry` from kwargs."""
    return SweepEntry.model_validate(dict(kwargs))


def _assert_v_rail(limits: dict[str, MeasurementLimitConfig], **expected: Any) -> None:
    """Assert ``limits['v_rail']`` carries the expected fields (typed)."""
    actual = limits["v_rail"]
    for key, value in expected.items():
        assert getattr(actual, key) == value, f"{key}: got {getattr(actual, key)!r}, want {value!r}"


class TestFlattenProfileChain:
    def test_single_profile_returns_self_flattened(self) -> None:
        leaf = ProfileConfig(
            facets={"test_phase": "characterization"},
            tests={"test_a": TestEntry(limits=_v_rail(tolerance_pct=1.0))},
        )
        project = _make_project({"char": leaf})
        merged = flatten_profile_chain("char", project)
        assert merged.facets == {"test_phase": "characterization"}
        _assert_v_rail(merged.tests["test_a"].limits, tolerance_pct=1.0)
        assert merged.extends is None

    def test_child_overrides_parent_on_same_key(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.2, high=3.4))},
        )
        child = ProfileConfig(
            facets={"test_phase": "production", "product": "tps54302"},
            extends="family",
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.25, high=3.35))},
        )
        project = _make_project({"family": parent, "prod-tps54302": child})
        merged = flatten_profile_chain("prod-tps54302", project)
        _assert_v_rail(merged.tests["test_rail"].limits, low=3.25, high=3.35)

    def test_parent_only_keys_pass_through(self) -> None:
        parent = ProfileConfig(
            tests={
                "test_rail": TestEntry(
                    sweeps=[_sweep(vin=[5.0])],
                    limits=_v_rail(low=3.2, high=3.4),
                ),
            },
        )
        child = ProfileConfig(
            extends="family",
            tests={"test_output": TestEntry(limits=_v_rail(tolerance_pct=1.0))},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        rail = merged.tests["test_rail"]
        assert [s.root for s in rail.sweeps] == [{"vin": [5.0]}]
        _assert_v_rail(rail.limits, low=3.2, high=3.4)
        _assert_v_rail(merged.tests["test_output"].limits, tolerance_pct=1.0)

    def test_class_scoped_markers_merge(self) -> None:
        parent = ProfileConfig(
            tests={"TestRails": TestEntry(sweeps=[_sweep(vin=[4.5, 5.0])])},
        )
        child = ProfileConfig(
            extends="family",
            tests={"TestRails": TestEntry(limits=_v_rail(tolerance_pct=1.0))},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        rails = merged.tests["TestRails"]
        assert [s.root for s in rails.sweeps] == [{"vin": [4.5, 5.0]}]
        _assert_v_rail(rails.limits, tolerance_pct=1.0)

    def test_nested_class_method_merge(self) -> None:
        """Same recursive merge applies one level deeper for class methods."""
        parent = ProfileConfig(
            tests={
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(limits=_v_rail(low=3.2))},
                )
            },
        )
        child = ProfileConfig(
            extends="family",
            tests={
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(limits=_v_rail(low=3.25))},
                )
            },
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        rails = merged.tests["TestRails"]
        _assert_v_rail(rails.tests["test_rail"].limits, low=3.25)

    def test_file_level_markers_merge(self) -> None:
        parent = ProfileConfig(limits=_v_rail(tolerance_pct=5.0))
        child = ProfileConfig(extends="family", limits=_v_rail(tolerance_pct=1.0))
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        _assert_v_rail(merged.limits, tolerance_pct=1.0)

    def test_runner_addopts_child_appends_to_parent(self) -> None:
        parent = ProfileConfig(runner={"addopts": "--strict-markers"})
        child = ProfileConfig(
            extends="family",
            runner={"addopts": "-p no:cacheprovider"},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.runner.get("addopts") == "--strict-markers -p no:cacheprovider"

    def test_runner_markexpr_and_keyword_child_wins_when_set(self) -> None:
        parent = ProfileConfig(runner={"markexpr": "not slow", "keyword": "rail"})
        child = ProfileConfig(
            extends="family",
            runner={"markexpr": "production"},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.runner.get("markexpr") == "production"
        assert merged.runner.get("keyword") == "rail"

    def test_cycle_raises_usage_error(self) -> None:
        a = ProfileConfig(extends="b")
        b = ProfileConfig(extends="a")
        project = _make_project({"a": a, "b": b})
        with pytest.raises(ProfileError, match="Cyclic profile extends chain"):
            flatten_profile_chain("a", project)

    def test_self_extends_raises_usage_error(self) -> None:
        loop = ProfileConfig(extends="loop")
        project = _make_project({"loop": loop})
        with pytest.raises(ProfileError, match="Cyclic profile extends chain"):
            flatten_profile_chain("loop", project)

    def test_unknown_parent_raises_usage_error(self) -> None:
        orphan = ProfileConfig(extends="nonexistent")
        project = _make_project({"orphan": orphan})
        with pytest.raises(
            ProfileError,
            match="extends unknown profile 'nonexistent'",
        ):
            flatten_profile_chain("orphan", project)

    def test_three_level_chain_walks_parent_first(self) -> None:
        grandparent = ProfileConfig(
            tests={"test_a": TestEntry(limits=_v_rail(low=1.0))},
        )
        parent = ProfileConfig(
            extends="grandparent",
            tests={"test_b": TestEntry(limits=_v_rail(low=2.0))},
        )
        child = ProfileConfig(
            extends="parent",
            tests={"test_a": TestEntry(limits=_v_rail(low=99.0))},
        )
        project = _make_project({"grandparent": grandparent, "parent": parent, "child": child})
        merged = flatten_profile_chain("child", project)
        _assert_v_rail(merged.tests["test_a"].limits, low=99.0)
        _assert_v_rail(merged.tests["test_b"].limits, low=2.0)


class TestResolveActiveProfileWithExtends:
    def test_facet_query_selects_and_flattens(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.2))},
        )
        child = ProfileConfig(
            facets={"test_phase": "production", "product": "tps54302"},
            extends="family",
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.25))},
        )
        project = _make_project({"family": parent, "prod-tps54302": child})
        name, profile, facets = resolve_active_profile(
            None, {"test_phase": "production", "product": "tps54302"}, project
        )
        assert name == "prod-tps54302"
        assert profile is not None
        _assert_v_rail(profile.tests["test_rail"].limits, low=3.25)
        assert facets == {"test_phase": "production", "product": "tps54302"}

    def test_parent_without_facets_unreachable_via_facet_query(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.2))},
        )
        project = _make_project({"family": parent})
        with pytest.raises(ProfileError, match="No profile matches the facet query"):
            resolve_active_profile(None, {"test_phase": "production"}, project)

    def test_parent_reachable_via_litmus_profile_name(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.2))},
        )
        project = _make_project({"family": parent})
        name, profile, _ = resolve_active_profile("family", {}, project)
        assert name == "family"
        assert profile is not None
        _assert_v_rail(profile.tests["test_rail"].limits, low=3.2)

    def test_name_selection_walks_extends_chain(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.2, high=3.4))},
        )
        child = ProfileConfig(
            extends="family",
            tests={"test_rail": TestEntry(limits=_v_rail(low=3.25, high=3.35))},
        )
        project = _make_project({"family": parent, "prod": child})
        _, profile, _ = resolve_active_profile("prod", {}, project)
        assert profile is not None
        _assert_v_rail(profile.tests["test_rail"].limits, low=3.25, high=3.35)


class TestProfilesDirLoader:
    def test_discovers_yaml_files_keyed_by_stem(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text("name: p\n")
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "production.yaml").write_text("facets: {test_phase: production}\n")
        (profiles_dir / "characterization.yaml").write_text(
            "facets: {test_phase: characterization}\n"
        )
        project = load_project(tmp_path / "litmus.yaml")
        assert set(project.profiles) == {"production", "characterization"}
        assert project.profiles["production"].facets == {"test_phase": "production"}

    def test_inline_and_sidecar_profiles_coexist(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text(
            "name: p\nprofiles:\n  inline:\n    facets: {test_phase: validation}\n"
        )
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "production.yaml").write_text("facets: {test_phase: production}\n")
        project = load_project(tmp_path / "litmus.yaml")
        assert set(project.profiles) == {"inline", "production"}

    def test_name_conflict_raises(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text(
            "name: p\nprofiles:\n  prod:\n    facets: {test_phase: production}\n"
        )
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "prod.yaml").write_text("facets: {test_phase: production}\n")
        with pytest.raises(ValueError, match="Profile name conflict: 'prod'"):
            load_project(tmp_path / "litmus.yaml")

    def test_no_profiles_dir_is_fine(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text("name: p\n")
        project = load_project(tmp_path / "litmus.yaml")
        assert project.profiles == {}

    def test_empty_profiles_dir_is_fine(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text("name: p\n")
        (tmp_path / "profiles").mkdir()
        project = load_project(tmp_path / "litmus.yaml")
        assert project.profiles == {}

    def test_extends_roundtrips_through_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "litmus.yaml").write_text("name: p\n")
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        (profiles_dir / "family.yaml").write_text(
            "tests:\n  test_rail:\n    limits:\n      v_rail: {low: 3.2, high: 3.4}\n"
        )
        (profiles_dir / "leaf.yaml").write_text(
            "extends: family\nfacets: {test_phase: production}\n"
        )
        project = load_project(tmp_path / "litmus.yaml")
        leaf = project.profiles["leaf"]
        assert leaf.extends == "family"
        merged = flatten_profile_chain("leaf", project)
        _assert_v_rail(merged.tests["test_rail"].limits, low=3.2, high=3.4)
