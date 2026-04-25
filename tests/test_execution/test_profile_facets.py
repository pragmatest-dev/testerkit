"""Profile ``extends:`` chain + ``profiles/*.yaml`` discovery.

Unit tests for the pure resolver (``flatten_profile_chain``) and for
``load_project`` picking up one-file-per-profile YAMLs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from litmus.config.test_config import MarkerSpec, TestEntry
from litmus.execution.profiles import flatten_profile_chain, resolve_active_profile
from litmus.models.project import ProfileConfig, ProfilePytest, ProjectConfig
from litmus.store import load_project


def _make_project(profiles: dict[str, ProfileConfig]) -> ProjectConfig:
    return ProjectConfig(name="p", profiles=profiles)


def _limits(**kwargs: object) -> MarkerSpec:
    return MarkerSpec(name="litmus_limits", kwargs=dict(kwargs))


def _parametrize(*args: object) -> MarkerSpec:
    return MarkerSpec(name="parametrize", args=list(args))


class TestFlattenProfileChain:
    def test_single_profile_returns_self_flattened(self) -> None:
        leaf = ProfileConfig(
            facets={"test_phase": "characterization"},
            tests={"test_a": TestEntry(markers=[_limits(tol=1.0)])},
        )
        project = _make_project({"char": leaf})
        merged = flatten_profile_chain("char", project)
        assert merged.facets == {"test_phase": "characterization"}
        assert merged.tests == {"test_a": TestEntry(markers=[_limits(tol=1.0)])}
        assert merged.extends is None

    def test_child_overrides_parent_on_same_key(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(markers=[_limits(low=3.2, high=3.4)])},
        )
        child = ProfileConfig(
            facets={"test_phase": "production", "product": "tps54302"},
            extends="family",
            tests={"test_rail": TestEntry(markers=[_limits(low=3.25, high=3.35)])},
        )
        project = _make_project({"family": parent, "prod-tps54302": child})
        merged = flatten_profile_chain("prod-tps54302", project)
        # Parent-first merge: both markers present; consumer picks last-wins.
        assert merged.tests["test_rail"].markers == [
            _limits(low=3.2, high=3.4),
            _limits(low=3.25, high=3.35),
        ]

    def test_parent_only_keys_pass_through(self) -> None:
        parent = ProfileConfig(
            tests={
                "test_rail": TestEntry(
                    markers=[_parametrize("vin", [5.0]), _limits(low=3.2, high=3.4)]
                ),
            },
        )
        child = ProfileConfig(
            extends="family",
            tests={"test_output": TestEntry(markers=[_limits(tol_pct=1.0)])},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.tests["test_rail"].markers == [
            _parametrize("vin", [5.0]),
            _limits(low=3.2, high=3.4),
        ]
        assert merged.tests["test_output"].markers == [_limits(tol_pct=1.0)]

    def test_class_scoped_markers_merge(self) -> None:
        parent = ProfileConfig(
            tests={"TestRails": TestEntry(markers=[_parametrize("vin", [4.5, 5.0])])},
        )
        child = ProfileConfig(
            extends="family",
            tests={"TestRails": TestEntry(markers=[_limits(tol=1.0)])},
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.tests["TestRails"].markers == [
            _parametrize("vin", [4.5, 5.0]),
            _limits(tol=1.0),
        ]

    def test_nested_class_method_merge(self) -> None:
        """Same recursive merge applies one level deeper for class methods."""
        parent = ProfileConfig(
            tests={
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_limits(low=3.2)])},
                )
            },
        )
        child = ProfileConfig(
            extends="family",
            tests={
                "TestRails": TestEntry(
                    tests={"test_rail": TestEntry(markers=[_limits(low=3.25)])},
                )
            },
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        rails = merged.tests["TestRails"]
        assert rails.tests["test_rail"].markers == [_limits(low=3.2), _limits(low=3.25)]

    def test_file_level_markers_extend(self) -> None:
        parent = ProfileConfig(markers=[_limits(tolerance_pct=5.0)])
        child = ProfileConfig(extends="family", markers=[_limits(tolerance_pct=1.0)])
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.markers == [
            _limits(tolerance_pct=5.0),
            _limits(tolerance_pct=1.0),
        ]

    def test_pytest_addopts_child_appends_to_parent(self) -> None:
        parent = ProfileConfig(pytest=ProfilePytest(addopts="--strict-markers"))
        child = ProfileConfig(
            extends="family",
            pytest=ProfilePytest(addopts="-p no:cacheprovider"),
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.pytest.addopts == "--strict-markers -p no:cacheprovider"

    def test_pytest_markexpr_and_keyword_child_wins_when_set(self) -> None:
        parent = ProfileConfig(pytest=ProfilePytest(markexpr="not slow", keyword="rail"))
        child = ProfileConfig(
            extends="family",
            pytest=ProfilePytest(markexpr="production"),
        )
        project = _make_project({"family": parent, "leaf": child})
        merged = flatten_profile_chain("leaf", project)
        assert merged.pytest.markexpr == "production"
        assert merged.pytest.keyword == "rail"

    def test_cycle_raises_usage_error(self) -> None:
        a = ProfileConfig(extends="b")
        b = ProfileConfig(extends="a")
        project = _make_project({"a": a, "b": b})
        with pytest.raises(pytest.UsageError, match="Cyclic profile extends chain"):
            flatten_profile_chain("a", project)

    def test_self_extends_raises_usage_error(self) -> None:
        loop = ProfileConfig(extends="loop")
        project = _make_project({"loop": loop})
        with pytest.raises(pytest.UsageError, match="Cyclic profile extends chain"):
            flatten_profile_chain("loop", project)

    def test_unknown_parent_raises_usage_error(self) -> None:
        orphan = ProfileConfig(extends="nonexistent")
        project = _make_project({"orphan": orphan})
        with pytest.raises(
            pytest.UsageError,
            match="extends unknown profile 'nonexistent'",
        ):
            flatten_profile_chain("orphan", project)

    def test_three_level_chain_walks_parent_first(self) -> None:
        grandparent = ProfileConfig(
            tests={"test_a": TestEntry(markers=[_limits(v=1)])},
        )
        parent = ProfileConfig(
            extends="grandparent",
            tests={"test_b": TestEntry(markers=[_limits(v=2)])},
        )
        child = ProfileConfig(
            extends="parent",
            tests={"test_a": TestEntry(markers=[_limits(v=99)])},
        )
        project = _make_project({"grandparent": grandparent, "parent": parent, "child": child})
        merged = flatten_profile_chain("child", project)
        assert merged.tests["test_a"].markers == [_limits(v=1), _limits(v=99)]
        assert merged.tests["test_b"].markers == [_limits(v=2)]


class TestResolveActiveProfileWithExtends:
    def test_facet_query_selects_and_flattens(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(markers=[_limits(low=3.2)])},
        )
        child = ProfileConfig(
            facets={"test_phase": "production", "product": "tps54302"},
            extends="family",
            tests={"test_rail": TestEntry(markers=[_limits(low=3.25)])},
        )
        project = _make_project({"family": parent, "prod-tps54302": child})
        name, profile, facets = resolve_active_profile(
            None, {"test_phase": "production", "product": "tps54302"}, project
        )
        assert name == "prod-tps54302"
        assert profile is not None
        assert profile.tests["test_rail"].markers == [_limits(low=3.2), _limits(low=3.25)]
        assert facets == {"test_phase": "production", "product": "tps54302"}

    def test_parent_without_facets_unreachable_via_facet_query(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(markers=[_limits(low=3.2)])},
        )
        project = _make_project({"family": parent})
        with pytest.raises(pytest.UsageError, match="No profile matches the facet query"):
            resolve_active_profile(None, {"test_phase": "production"}, project)

    def test_parent_reachable_via_litmus_profile_name(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(markers=[_limits(low=3.2)])},
        )
        project = _make_project({"family": parent})
        name, profile, _facets = resolve_active_profile("family", {}, project)
        assert name == "family"
        assert profile is not None
        assert profile.tests["test_rail"].markers == [_limits(low=3.2)]

    def test_name_selection_walks_extends_chain(self) -> None:
        parent = ProfileConfig(
            tests={"test_rail": TestEntry(markers=[_limits(low=3.2, high=3.4)])},
        )
        child = ProfileConfig(
            extends="family",
            tests={"test_rail": TestEntry(markers=[_limits(low=3.25, high=3.35)])},
        )
        project = _make_project({"family": parent, "prod": child})
        _name, profile, _facets = resolve_active_profile("prod", {}, project)
        assert profile is not None
        assert profile.tests["test_rail"].markers == [
            _limits(low=3.2, high=3.4),
            _limits(low=3.25, high=3.35),
        ]


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
            "tests:\n  test_rail:\n    markers:\n      - litmus_limits: {low: 3.2, high: 3.4}\n"
        )
        (profiles_dir / "leaf.yaml").write_text(
            "extends: family\nfacets: {test_phase: production}\n"
        )
        project = load_project(tmp_path / "litmus.yaml")
        leaf = project.profiles["leaf"]
        assert leaf.extends == "family"
        merged = flatten_profile_chain("leaf", project)
        assert merged.tests["test_rail"].markers == [_limits(low=3.2, high=3.4)]
