# Instrument Three-Tier Model

Universal (catalog) → unit (asset) → runtime (record). Catalog entries describe what a make/model can do; assets bind a serial to a catalog entry with calibration; records are the live, role-mapped runtime view the fixture/logger tracks.

```mermaid
flowchart LR
    classDef definition fill:#dde9f5,stroke:#345e8f,color:#1a2a3f
    classDef primitive fill:#ebeef2,stroke:#6b7682,color:#2a3138
    classDef runtime fill:#dcecdc,stroke:#3f7e3f,color:#1a3a1a
    classDef highlight stroke-width:3px
    instrument_catalog_entry[InstrumentCatalogEntry]:::definition
    instrument_asset_file[InstrumentAssetFile]:::definition
    instrument_record[InstrumentRecord]:::runtime
    station_instrument_config[StationInstrumentConfig]:::primitive
    station_config[StationConfig]:::definition
    instrument_info[InstrumentInfo]:::primitive
    calibration_info[CalibrationInfo]:::primitive
    instrument_capability[InstrumentCapability]:::primitive
    channel_topology[ChannelTopology]:::primitive
    attribute[Attribute]:::primitive
    class instrument_record highlight
    instrument_catalog_entry -->|offers| channel_topology
    instrument_catalog_entry -->|offers| attribute
    instrument_catalog_entry -->|offers| instrument_capability
    instrument_asset_file -.->|references| instrument_catalog_entry
    instrument_asset_file -->|identifies| instrument_info
    instrument_asset_file -->|calibrated_per| calibration_info
    instrument_record -->|identifies| instrument_info
    instrument_record -->|calibrated_per| calibration_info
    instrument_record -.->|references| instrument_catalog_entry
    station_instrument_config -.->|references| instrument_catalog_entry
    station_config -->|equips| station_instrument_config
```

## Concepts in this slice

- [attribute](../index.md#attribute) — Fixed hardware fact (bandwidth, sample rate, scpi_version) — value or range or options, optionally banded.
- [calibration_info](../index.md#calibration-info) — Calibration status from configuration (due/last/cert/lab). NOT queryable from device — comes from the asset file.
- [channel_topology](../index.md#channel-topology) — Physical topology of a single instrument channel — terminals, connector type, ground topology, optional flag.
- [instrument_asset_file](../index.md#instrument-asset-file) — Unit-specific tier of the 3-tier instrument model — a specific physical device (serial, calibration) referencing a catalog entry. Tier 2 of (catalog → asset → record).
- [instrument_capability](../index.md#instrument-capability) — Capability + channel list + operational metadata. The instrument- side dialect of the shared Capability shape.
- [instrument_catalog_entry](../index.md#instrument-catalog-entry) — Universal tier of the 3-tier instrument model — what a make/model can do. Channels, attributes, and a list of InstrumentCapability entries. Tier 1 of (catalog → asset → record).
- [instrument_info](../index.md#instrument-info) — Identity queried from device (manufacturer/model/serial/firmware). For VISA, parsed from *IDN?.
- [instrument_record](../index.md#instrument-record) — Tier 3 of the 3-tier instrument model — runtime view combining role + asset + identity + calibration + driver + catalog_ref + mock flag. What the fixture/logger tracks during a session.
- [station_config](../index.md#station-config) — Concrete bench deployment. Names a station_type for contract validation; hostname enables session-start auto-match against socket.gethostname().
- [station_instrument_config](../index.md#station-instrument-config) — Single instrument entry in a station file — type, driver, resource, optional catalog_ref, mock flag, channel mapping.
