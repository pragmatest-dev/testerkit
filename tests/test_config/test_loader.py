"""Tests for Litmus configuration loader."""

from litmus.config.loader import (
    find_test_config,
    get_test_config,
    load_test_config,
)


class TestLoadTestConfig:
    def test_load_test_config(self, tmp_path):
        yaml_content = """
test_voltage_sweep:
  vectors:
    expand: product
    voltage: [3.3, 5.0, 12.0]
  limits:
    output_voltage:
      low: 3.0
      high: 3.6
      units: V
  retry:
    max_attempts: 3
    delay_seconds: 0.5
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)

        configs = load_test_config(config_file)
        assert "test_voltage_sweep" in configs
        cfg = configs["test_voltage_sweep"]
        assert cfg["vectors"]["voltage"] == [3.3, 5.0, 12.0]
        assert cfg["limits"]["output_voltage"].low == 3.0
        assert cfg["retry"].max_attempts == 3

    def test_load_test_config_empty(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")

        configs = load_test_config(config_file)
        assert configs == {}


class TestFindTestConfig:
    def test_find_test_config_exists(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("test: {}")
        test_file = tmp_path / "test_something.py"

        result = find_test_config(test_file)
        assert result == config_file

    def test_find_test_config_missing(self, tmp_path):
        test_file = tmp_path / "test_something.py"
        assert find_test_config(test_file) is None


class TestGetTestConfig:
    def test_get_test_config(self, tmp_path):
        yaml_content = """
test_voltage:
  limits:
    vout:
      low: 4.5
      high: 5.5
      units: V
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        test_file = tmp_path / "test_power.py"

        result = get_test_config("test_voltage", test_file)
        assert result is not None
        assert result["limits"]["vout"].low == 4.5

    def test_get_test_config_not_found(self, tmp_path):
        test_file = tmp_path / "test_power.py"
        assert get_test_config("test_voltage", test_file) is None
