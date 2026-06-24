# Reference — pytest plugin

The fixtures and markers the bundled pytest plugin contributes on top of stock pytest. For pytest-native mechanics (collection, parametrize, conftest, CLI flags), see [pytest-native](../overview/pytest-native.md).

- [Fixtures](fixtures.md) — all the fixtures the plugin contributes, with signatures, scopes, and per-fixture examples
- [Markers](markers.md) — the seven `@pytest.mark.litmus_*` decorators and their 1:1 sidecar equivalents

## See also

- [Concepts → pytest](../../concepts/overview/pytest.md) — why the platform rides on pytest as the primary runner
- [How-to → Writing tests](../../how-to/execution/writing-tests.md) — task recipes for authoring tests that use these fixtures + markers
- [Reference → Configuration](../configuration.md) — sidecar YAML schemas (the YAML form of every marker)
