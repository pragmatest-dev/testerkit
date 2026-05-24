# Reference — Catalog

The instrument catalog schema. Catalog entries are shared across projects, one entry per `<vendor>.<model>` — so the schema gets its own reference, separate from the project-local YAML schemas under [configuration](../configuration.md).

- [Schema](schema.md) — every field in a `catalog/<vendor>/<model>.yaml` entry, the rules, the "what goes WHERE" decision tree
- [Cookbook](cookbook.md) — worked recipes for the recurring datasheet shapes (accuracy bands, dual-unit values, shared controls, conditional attributes, etc.)

## See also

- [Concepts → Capabilities](../../concepts/configuration/capabilities.md) — how catalog capabilities pair with product characteristics for matching
- [How-to → Datasheet → tests](../../how-to/catalog/datasheet-to-test.md) — AI-assisted workflow that produces catalog YAML from a datasheet PDF
