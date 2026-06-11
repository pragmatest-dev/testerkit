# Concepts — Configuration

The DUT-to-instrument model. Parts and stations are YAML entities you author once; fixtures wire one part's pins to one station's instruments; capabilities are how the matcher pairs them up.

- [Parts](parts.md) — what you're testing: part number, revision, pins, characteristics, specs
- [Stations](stations.md) — where you test: the instruments on a bench plus their roles
- [Capabilities](capabilities.md) — what an instrument can do (function + direction + signals), and how the matcher uses that to answer "can this station test this part?"
- [Fixtures](fixtures.md) — the wiring between a part's pins and a station's instrument channels

## See also

- [How-to → Configuring stations](../../how-to/configuration/configuring-stations.md) — task recipe for writing station YAML
- [Reference → Configuration](../../reference/configuration.md) — the YAML schemas (generated from the Pydantic models)
- [Reference → Catalog schema](../../reference/catalog/schema.md) — how catalog entries define instrument capabilities
