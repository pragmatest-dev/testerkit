"""Tests for the flat marker-scope schema and YAML range expanders.

Sub-model field validation (target shape on MockEntry, zip-coherence
on SweepEntry, extra="forbid" rejection of unknown keys, etc.) is
provided by Pydantic — those tests live in the model definitions, not
here. This file exercises the higher-level shape: that TestEntry /
SidecarConfig accept and coerce the documented YAML structure.
"""

from __future__ import annotations

import pytest

from testerkit.models.test_config import (
    MeasurementLimitConfig,
    MockEntry,
    PromptConfig,
    RetryConfig,
    SidecarConfig,
    SweepEntry,
    TestEntry,
)
from testerkit.store import expand_ranges


class TestTestEntryShape:
    def test_empty_is_valid(self) -> None:
        entry = TestEntry()
        assert entry.limits == {}
        assert entry.sweeps == []
        assert entry.mocks == []
        assert entry.characteristics == []
        assert entry.connections is None
        assert entry.retry is None
        assert entry.prompts == {}
        assert entry.runner == {}
        assert entry.tests == {}

    def test_limits_coerced_to_measurement_limit_config(self) -> None:
        entry = TestEntry.model_validate({"limits": {"v_rail": {"tolerance_pct": 5.0}}})
        assert isinstance(entry.limits["v_rail"], MeasurementLimitConfig)
        assert entry.limits["v_rail"].tolerance_pct == 5.0

    def test_sweeps_coerced_to_sweep_entry(self) -> None:
        entry = TestEntry.model_validate({"sweeps": [{"vin": [3.3, 5.0]}]})
        assert isinstance(entry.sweeps[0], SweepEntry)
        assert entry.sweeps[0].root == {"vin": [3.3, 5.0]}

    def test_mocks_coerced_to_mock_entry(self) -> None:
        entry = TestEntry.model_validate({"mocks": [{"target": "dmm.read", "return_value": 3.31}]})
        assert isinstance(entry.mocks[0], MockEntry)
        assert entry.mocks[0].target == "dmm.read"
        assert entry.mocks[0].patch_kwargs() == {"return_value": 3.31}

    def test_characteristics_list_of_strings(self) -> None:
        entry = TestEntry.model_validate({"characteristics": ["rail_3v3"]})
        assert entry.characteristics == ["rail_3v3"]

    def test_connections_list_form_binds_by_name(self) -> None:
        """List shape → bind to fixture-connection names (matches
        ``testerkit_characteristics`` positional list shape)."""
        entry = TestEntry.model_validate({"connections": ["vout", "vin_3v3"]})
        assert entry.connections == ["vout", "vin_3v3"]

    def test_connections_dict_form_binds_by_channel(self) -> None:
        """Dict shape → bind to instrument → channel selectors (matches
        ``testerkit_limits`` kwargs-by-name shape)."""
        entry = TestEntry.model_validate({"connections": {"dmm": ["ch1", "ch2"], "psu": "ch1"}})
        assert entry.connections == {"dmm": ["ch1", "ch2"], "psu": "ch1"}

    def test_connections_dict_with_all_sentinel(self) -> None:
        """``'all'`` string is the supported sentinel for every channel
        on an instrument."""
        entry = TestEntry.model_validate({"connections": {"dmm": "all"}})
        assert entry.connections == {"dmm": "all"}

    def test_retry_coerced_to_policy(self) -> None:
        entry = TestEntry.model_validate({"retry": {"max_retries": 2}})
        assert isinstance(entry.retry, RetryConfig)
        assert entry.retry.max_retries == 2

    def test_prompts_coerced_to_prompt_config(self) -> None:
        entry = TestEntry.model_validate({"prompts": {"setup": {"message": "Insert UUT"}}})
        assert isinstance(entry.prompts["setup"], PromptConfig)
        assert entry.prompts["setup"].message == "Insert UUT"

    def test_runner_is_opaque_dict(self) -> None:
        entry = TestEntry.model_validate({"runner": {"markers": [{"flaky": {"reruns": 2}}]}})
        assert entry.runner == {"markers": [{"flaky": {"reruns": 2}}]}

    def test_unknown_top_level_key_rejected(self) -> None:
        # ``testerkit_X`` is the typo for ``X`` — caught by ``extra="forbid"``.
        with pytest.raises(ValueError, match="extra"):
            TestEntry.model_validate({"testerkit_limits": {}})


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
        assert sidecar.limits["v_rail"].tolerance_pct == 5.0
        rails = sidecar.tests["TestRails"]
        assert isinstance(rails, TestEntry)
        assert rails.sweeps[0].root == {"vin": [4.5, 5.0, 5.5]}
        nested = rails.tests["test_rail"]
        assert nested.limits["v_rail"].tolerance_pct == 1.0
        standalone = sidecar.tests["test_standalone"]
        assert standalone.runner == {"markers": [{"flaky": {"reruns": 2}}]}
        assert standalone.tests == {}

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"vectors": {}})

    def test_rejects_legacy_config_wrapper(self) -> None:
        # The old `config:` wrapper is gone — fields live at root now.
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"config": {"limits": {"v_rail": {}}}})

    def test_sweep_zip_dim_mismatch_caught_at_load(self) -> None:
        # Pydantic catches dim-mismatch at YAML load — no separate
        # plugin-side validation needed.
        with pytest.raises(ValueError, match="same length"):
            SidecarConfig.model_validate({"sweeps": [{"vin": [3.3, 5.0], "vout": [1.0]}]})

    def test_mock_target_shape_caught_at_load(self) -> None:
        with pytest.raises(ValueError, match="<fixture>.<attr>"):
            SidecarConfig.model_validate({"mocks": [{"target": "no_dot"}]})
