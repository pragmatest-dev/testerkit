"""Tests for the flat marker-scope schema and YAML range expanders."""

from __future__ import annotations

import pytest

from litmus.config.expanders import expand_ranges
from litmus.config.test_config import SidecarConfig, TestEntry


class TestTestEntryShape:
    def test_empty_is_valid(self) -> None:
        entry = TestEntry()
        assert entry.limits == {}
        assert entry.sweeps == []
        assert entry.mocks == []
        assert entry.specs == []
        assert entry.connections is None
        assert entry.retry is None
        assert entry.prompts == {}
        assert entry.runner == {}
        assert entry.tests == {}

    def test_limits_dict_keyed_by_measurement(self) -> None:
        entry = TestEntry.model_validate({"limits": {"v_rail": {"tolerance_pct": 5.0}}})
        assert entry.limits == {"v_rail": {"tolerance_pct": 5.0}}

    def test_sweeps_list_of_dicts(self) -> None:
        entry = TestEntry.model_validate({"sweeps": [{"vin": [3.3, 5.0]}]})
        assert entry.sweeps == [{"vin": [3.3, 5.0]}]

    def test_mocks_list_of_dicts(self) -> None:
        entry = TestEntry.model_validate({"mocks": [{"target": "dmm.read", "return_value": 3.31}]})
        assert entry.mocks[0]["target"] == "dmm.read"

    def test_specs_list_of_strings(self) -> None:
        entry = TestEntry.model_validate({"specs": ["rail_3v3"]})
        assert entry.specs == ["rail_3v3"]

    def test_connections_singleton_dict(self) -> None:
        entry = TestEntry.model_validate({"connections": {"connections": ["vout"]}})
        assert entry.connections == {"connections": ["vout"]}

    def test_retry_singleton_dict(self) -> None:
        entry = TestEntry.model_validate({"retry": {"max_attempts": 3}})
        assert entry.retry == {"max_attempts": 3}

    def test_prompts_dict_keyed_by_name(self) -> None:
        entry = TestEntry.model_validate({"prompts": {"setup": {"message": "Insert DUT"}}})
        assert entry.prompts == {"setup": {"message": "Insert DUT"}}

    def test_runner_is_opaque_dict(self) -> None:
        entry = TestEntry.model_validate({"runner": {"markers": [{"flaky": {"reruns": 2}}]}})
        assert entry.runner == {"markers": [{"flaky": {"reruns": 2}}]}

    def test_unknown_top_level_key_rejected(self) -> None:
        # litmus_X is a typo for X — caught by extra="forbid".
        with pytest.raises(ValueError, match="extra"):
            TestEntry.model_validate({"litmus_limits": {}})


class TestExpandRanges:
    def test_linspace(self) -> None:
        result = expand_ranges({"linspace": [0.0, 1.0, 5]})
        assert result == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])

    def test_arange(self) -> None:
        result = expand_ranges({"arange": [0, 5, 1]})
        assert result == [0, 1, 2, 3, 4]

    def test_logspace(self) -> None:
        result = expand_ranges({"logspace": [0, 2, 3]})
        assert result == pytest.approx([1.0, 10.0, 100.0])

    def test_geomspace(self) -> None:
        result = expand_ranges({"geomspace": [1, 1000, 4]})
        assert result == pytest.approx([1.0, 10.0, 100.0, 1000.0])

    def test_repeat(self) -> None:
        assert expand_ranges({"repeat": [5.0, 3]}) == [5.0, 5.0, 5.0]

    def test_range_two_args(self) -> None:
        assert expand_ranges({"range": [0, 4]}) == [0, 1, 2, 3]

    def test_range_three_args(self) -> None:
        assert expand_ranges({"range": [1, 10, 2]}) == [1, 3, 5, 7, 9]

    def test_unknown_key_passes_through(self) -> None:
        assert expand_ranges({"mystery": [1, 2, 3]}) == {"mystery": [1, 2, 3]}

    def test_multi_key_dict_not_an_expander(self) -> None:
        data = {"linspace": [0, 1, 3], "other": "value"}
        result = expand_ranges(data)
        assert result == data

    def test_scalar_passthrough(self) -> None:
        assert expand_ranges(5.0) == 5.0
        assert expand_ranges("hello") == "hello"
        assert expand_ranges(None) is None

    def test_nested_in_list(self) -> None:
        result = expand_ranges(["vin", {"linspace": [0.0, 1.0, 3]}])
        assert result == ["vin", pytest.approx([0.0, 0.5, 1.0])]

    def test_nested_in_dict_value(self) -> None:
        result = expand_ranges({"parametrize": {"argnames": "vin", "argvalues": {"range": [0, 3]}}})
        assert result == {"parametrize": {"argnames": "vin", "argvalues": [0, 1, 2]}}

    def test_expander_error_wrapped(self) -> None:
        with pytest.raises(ValueError, match="Range expander 'linspace' failed"):
            expand_ranges({"linspace": ["not", "numeric", "args"]})


class TestSidecarConfig:
    def test_empty_is_valid(self) -> None:
        sidecar = SidecarConfig()
        assert sidecar.limits == {}
        assert sidecar.runner == {}
        assert sidecar.tests == {}

    def test_recursive_tree_with_class_branch_and_module_test(self) -> None:
        sidecar = SidecarConfig.model_validate(
            {
                "limits": {"v_rail": {"tolerance_pct": 5.0}},
                "tests": {
                    "TestRails": {
                        "sweeps": [{"vin": [4.5, 5.0, 5.5]}],
                        "tests": {
                            "test_rail": {
                                "limits": {"v_rail": {"tolerance_pct": 1.0}},
                            },
                        },
                    },
                    "test_standalone": {
                        "runner": {"markers": [{"flaky": {"reruns": 2}}]},
                    },
                },
            }
        )
        assert sidecar.limits == {"v_rail": {"tolerance_pct": 5.0}}
        rails = sidecar.tests["TestRails"]
        assert isinstance(rails, TestEntry)
        assert rails.sweeps == [{"vin": [4.5, 5.0, 5.5]}]
        nested = rails.tests["test_rail"]
        assert isinstance(nested, TestEntry)
        assert nested.limits == {"v_rail": {"tolerance_pct": 1.0}}
        standalone = sidecar.tests["test_standalone"]
        assert isinstance(standalone, TestEntry)
        assert standalone.runner == {"markers": [{"flaky": {"reruns": 2}}]}
        assert standalone.tests == {}

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"vectors": {}})

    def test_rejects_unknown_test_entry_key(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"tests": {"test_x": {"litmus_limits": {}}}})

    def test_rejects_legacy_config_wrapper(self) -> None:
        # The old `config:` wrapper is gone — fields live at root now.
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"config": {"limits": {"v_rail": {}}}})
