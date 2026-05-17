# Litmus Ontology — Glossary

Every Litmus concept, its canonical Pydantic model, and how it relates to the others. Generated from `src/litmus/ontology/litmus.yaml`; do not hand-edit.

**Version:** 1  ·  **Concepts:** 92

## definition

### fixture_config {#fixture-config}

Bench-to-DUT wiring. Either single-DUT connections or multi-slot slots; never both. station_types[] declares which abstract station layouts this fixture can wire against.

- **Model:** `litmus.models.test_config.FixtureConfig`
- **Authored at:** `fixtures/*.yaml`
- **Concept doc:** [docs/concepts/fixtures.md](/docs/concepts/fixtures.md)
- **Relationships:**
    - `wires` → [fixture_connection](#fixture-connection)
    - `partitions_into` → [fixture_slot](#fixture-slot)
    - `references` → [product](#product)
    - `references` → [station_type](#station-type)

### instrument_asset_file {#instrument-asset-file}

Unit-specific tier of the 3-tier instrument model — a specific physical device (serial, calibration) referencing a catalog entry. Tier 2 of (catalog → asset → record).

- **Model:** `litmus.models.instrument_asset.InstrumentAssetFile`
- **Authored at:** `instruments/*.yaml`
- **Relationships:**
    - `references` → [instrument_catalog_entry](#instrument-catalog-entry)
    - `identifies` → [instrument_info](#instrument-info)
    - `calibrated_per` → [calibration_info](#calibration-info)

### instrument_catalog_entry {#instrument-catalog-entry}

Universal tier of the 3-tier instrument model — what a make/model can do. Channels, attributes, and a list of InstrumentCapability entries. Tier 1 of (catalog → asset → record).

- **Model:** `litmus.models.catalog.InstrumentCatalogEntry`
- **Authored at:** `catalog/**/*.yaml`
- **Concept doc:** [docs/reference/capability-schema.md](/docs/reference/capability-schema.md)
- **Relationships:**
    - `offers` → [channel_topology](#channel-topology)
    - `offers` → [attribute](#attribute)
    - `offers` → [instrument_capability](#instrument-capability)

### product {#product}

Spec for a thing-under-test: identity, pins, signal groups, and characteristics. ATML "UUT Description".

- **Model:** `litmus.models.product.Product`
- **Authored at:** `products/*.yaml | products/{id}/spec.yaml`
- **Concept doc:** [docs/concepts/products.md](/docs/concepts/products.md)
- **Relationships:**
    - `exposes` → [pin](#pin)
    - `exposes` → [signal_group](#signal-group)
    - `specifies` → [product_characteristic](#product-characteristic)
    - `instantiated_as` → [dut](#dut)

### product_characteristic {#product-characteristic}

A DUT capability tied to a physical interface (pin/pins/net/ signal_group) with optional datasheet ref. Extends Capability — the matching service pairs DUT OUTPUT characteristics with instrument INPUT capabilities by direction flip.

- **Model:** `litmus.models.product.ProductCharacteristic`
- **Authored at:** `products/*.yaml (under characteristics:)`
- **Concept doc:** [docs/concepts/capability-model.md](/docs/concepts/capability-model.md)
- **Relationships:**
    - `inherits_from` → [capability](#capability)
    - `references` → [pin](#pin)

### product_manifest {#product-manifest}

Per-product folder manifest tracking workflow position (parse → review → derive → select station → generate tests → execute).

- **Model:** `litmus.models.product_manifest.ProductManifest`
- **Authored at:** `products/{id}/manifest.yaml`
- **Relationships:**
    - `references` → [product](#product)

### profile_config {#profile-config}

Named config set applied to a pytest session. Same flat shape as a TestEntry plus profile-only description/facets/extends and an optional station_type / fixture binding. Selected via CLI facets.

- **Model:** `litmus.models.project.ProfileConfig`
- **Authored at:** `litmus.yaml (under profiles:)`
- **Concept doc:** [docs/concepts/sessions.md](/docs/concepts/sessions.md)
- **Relationships:**
    - `inherits_from` → [test_entry](#test-entry)
    - `extends` → [profile_config](#profile-config)
    - `references` → [station_type](#station-type)
    - `references` → [fixture_config](#fixture-config)

### project_config {#project-config}

Project root config. Names the default station/fixture/profile, data dir, multi-slot knobs, profiles, and required operator inputs.

- **Model:** `litmus.models.project.ProjectConfig`
- **Authored at:** `litmus.yaml`
- **Concept doc:** [docs/reference/configuration.md](/docs/reference/configuration.md)
- **Relationships:**
    - `declares` → [profile_config](#profile-config)
    - `declares` → [multi_slot_config](#multi-slot-config)
    - `declares` → [prompt_config](#prompt-config)
    - `references` → [station_config](#station-config)
    - `references` → [fixture_config](#fixture-config)
    - `references` → [profile_config](#profile-config)

### station_config {#station-config}

Concrete bench deployment. Names a station_type for contract validation; hostname enables session-start auto-match against socket.gethostname().

- **Model:** `litmus.models.station.StationConfig`
- **Authored at:** `stations/*.yaml`
- **Concept doc:** [docs/concepts/stations.md](/docs/concepts/stations.md)
- **Relationships:**
    - `equips` → [station_instrument_config](#station-instrument-config)
    - `references` → [station_type](#station-type)
    - `validates_against` → [station_type](#station-type)

### station_type {#station-type}

Abstract station-type template. Declares required instrument roles + types that concrete StationConfig deployments must cover.

- **Model:** `litmus.models.station.StationType`
- **Authored at:** `stations/types/*.yaml`
- **Relationships:**
    - `equips` → [instrument_config](#instrument-config)

## primitive

### attribute {#attribute}

Fixed hardware fact (bandwidth, sample rate, scpi_version) — value or range or options, optionally banded.

- **Model:** `litmus.models.capability.Attribute`

### bus_signal {#bus-signal}

One signal within a bus group; references a Pin by name.

- **Model:** `litmus.models.product.BusSignal`
- **Relationships:**
    - `references` → [pin](#pin)

### calibration_info {#calibration-info}

Calibration status from configuration (due/last/cert/lab). NOT queryable from device — comes from the asset file.

- **Model:** `litmus.models.instrument.CalibrationInfo`

### capability {#capability}

What a signal endpoint can do — base of both ProductCharacteristic (DUT side) and InstrumentCapability (bench side). Function + direction + signals/conditions/controls/attributes. ATML/IVI/ IEEE 1641 lineage.

- **Model:** `litmus.models.capability.Capability`
- **Concept doc:** [docs/concepts/capabilities.md](/docs/concepts/capabilities.md)
- **Relationships:**
    - `references` → [measurement_function](#measurement-function)
    - `references` → [direction](#direction)
    - `parameterized_by` → [signal](#signal)
    - `parameterized_by` → [condition](#condition)
    - `parameterized_by` → [control](#control)
    - `parameterized_by` → [attribute](#attribute)
    - `parameterized_by` → [spec_band](#spec-band)

### channel_topology {#channel-topology}

Physical topology of a single instrument channel — terminals, connector type, ground topology, optional flag.

- **Model:** `litmus.models.capability.ChannelTopology`

### condition {#condition}

Operating condition that affects accuracy (frequency, temperature, NPLC, …). NOT user-adjustable — describes the envelope under which specs were characterized.

- **Model:** `litmus.models.capability.Condition`

### control {#control}

A user-configurable knob (coupling, autorange, setpoint, …).

- **Model:** `litmus.models.capability.Control`

### fixture_connection {#fixture-connection}

Named DUT-pin ↔ instrument-channel pairing — the addressable unit of fixture routing. Optionally carries a measurement function (DMM for DC, scope for AC) and a SwitchRoute for switched fixtures.

- **Model:** `litmus.models.test_config.FixtureConnection`
- **Relationships:**
    - `references` → [pin](#pin)
    - `references` → [measurement_function](#measurement-function)
    - `routed_through` → [switch_route](#switch-route)

### fixture_slot {#fixture-slot}

One DUT slot inside a multi-DUT fixture; has its own connection map.

- **Model:** `litmus.models.test_config.FixtureSlot`
- **Relationships:**
    - `wires` → [fixture_connection](#fixture-connection)

### instrument_capability {#instrument-capability}

Capability + channel list + operational metadata. The instrument- side dialect of the shared Capability shape.

- **Model:** `litmus.models.capability.InstrumentCapability`
- **Relationships:**
    - `inherits_from` → [capability](#capability)

### instrument_config {#instrument-config}

Instrument-role declaration inside a StationType (type + driver + settings, no resource). The contract a deployment must satisfy.

- **Model:** `litmus.models.station.InstrumentConfig`

### instrument_info {#instrument-info}

Identity queried from device (manufacturer/model/serial/firmware). For VISA, parsed from *IDN?.

- **Model:** `litmus.models.instrument.InstrumentInfo`

### limit {#limit}

A resolved test limit — low/high/nominal + units + comparator (GELE, EQ, LE, …) + optional traceability (characteristic_id, spec_ref). Per-comparator membership check via Limit.__contains__.

- **Model:** `litmus.models.test_config.Limit`

### limit_lookup_config {#limit-lookup-config}

Lookup-table limit — value of `key` indexes a table of Limits.

- **Model:** `litmus.models.test_config.LimitLookupConfig`

### limit_step_config {#limit-step-config}

Step-function limit — ranges with `below:` thresholds and a `default:` catch-all.

- **Model:** `litmus.models.test_config.LimitStepConfig`

### measurement_limit_config {#measurement-limit-config}

Per-measurement limit policy — direct, characteristic-derived, banded, expression, lookup, step, or callable. The first matching band (or the parent fallback) wins at vector resolve time.

- **Model:** `litmus.models.test_config.MeasurementLimitConfig`
- **Relationships:**
    - `resolves_via` → [measurement_limit_config](#measurement-limit-config)
    - `resolves_via` → [limit_lookup_config](#limit-lookup-config)
    - `resolves_via` → [limit_step_config](#limit-step-config)
    - `resolves_to` → [limit](#limit)
    - `derives_from` → [product_characteristic](#product-characteristic)

### mock_entry {#mock-entry}

Per-test mock — target ("<fixture>.<attr>") plus arbitrary patch.object kwargs (return_value, side_effect, …).

- **Model:** `litmus.models.test_config.MockEntry`

### multi_slot_config {#multi-slot-config}

Multi-slot orchestration knobs (per-child grace seconds, etc.).

- **Model:** `litmus.models.project.MultiSlotConfig`

### pin {#pin}

Physical DUT pin with role classification (signal / ground / power / reference) for fixture routing.

- **Model:** `litmus.models.product.Pin`

### prompt_config {#prompt-config}

Operator prompt — message + type (confirm/choice/input) + optional timeout. Used for required_inputs and per-step dialogs.

- **Model:** `litmus.models.test_config.PromptConfig`

### retry_config {#retry-config}

Runner-neutral retry config — translates to flaky under pytest. max_retries=0 means single execution (no retry).

- **Model:** `litmus.models.test_config.RetryConfig`

### signal {#signal}

A measurable/sourceable parameter (range + accuracy + resolution).

- **Model:** `litmus.models.capability.Signal`

### signal_group {#signal-group}

Grouped signals forming a bus interface (I2C, SPI, UART).

- **Model:** `litmus.models.product.SignalGroup`
- **Relationships:**
    - `bundles` → [bus_signal](#bus-signal)

### spec_band {#spec-band}

Condition-dependent spec override — "at this operating point, here are the specs." Empty `when:` always matches. Anchors the shared "top-level default + optional when:-keyed override" pattern.

- **Model:** `litmus.models.capability.SpecBand`

### station_instrument_config {#station-instrument-config}

Single instrument entry in a station file — type, driver, resource, optional catalog_ref, mock flag, channel mapping.

- **Model:** `litmus.models.station.StationInstrumentConfig`
- **Relationships:**
    - `references` → [instrument_catalog_entry](#instrument-catalog-entry)

### sweep_entry {#sweep-entry}

One sweep level — {argname: argvalues, …}. Multiple keys = a zipped axis; all lengths must match (validated at YAML load).

- **Model:** `litmus.models.test_config.SweepEntry`

### switch_route {#switch-route}

Switch channels to close before this connection's instrument can be used; carries settling time.

- **Model:** `litmus.models.test_config.SwitchRoute`

## config-overlay

### litmus_marker {#litmus-marker}

A @pytest.mark.litmus_* decorator on a test. Anything authorable in a sidecar's marker fields can also be written as a marker. Same vocabulary; the marker form is closer to the code.

- **Relationships:**
    - `overlays` → [pytest_test_function](#pytest-test-function)

### sidecar_config {#sidecar-config}

Top-level shape of a per-test-module sidecar YAML. Same flat TestEntry shape; the file root carries file-level marker fields and a nested tests: tree.

- **Model:** `litmus.models.test_config.SidecarConfig`
- **Authored at:** `tests/test_*.yaml`
- **Concept doc:** [docs/concepts/step-manifest.md](/docs/concepts/step-manifest.md)
- **Relationships:**
    - `inherits_from` → [test_entry](#test-entry)
    - `overlays` → [pytest_test_function](#pytest-test-function)

### test_entry {#test-entry}

Recursive node in a sidecar/profile tests: tree. Mirrors pytest's node-id structure: class = branch with own marker fields + nested tests:; function = leaf. Reserved keys at every level: runner, tests. Every other key is a typed Litmus-marker sub-model.

- **Model:** `litmus.models.test_config.TestEntry`
- **Concept doc:** [docs/concepts/step-manifest.md](/docs/concepts/step-manifest.md)
- **Relationships:**
    - `nests` → [test_entry](#test-entry)
    - `configures` → [measurement_limit_config](#measurement-limit-config)
    - `configures` → [sweep_entry](#sweep-entry)
    - `configures` → [mock_entry](#mock-entry)
    - `configures` → [retry_config](#retry-config)
    - `configures` → [prompt_config](#prompt-config)

## runtime

### channel_descriptor {#channel-descriptor}

Metadata for a live channel — data_type (scalar/array), instrument_role, resource, units. Written once when first seen.

- **Model:** `litmus.data.channels.models.ChannelDescriptor`
- **Relationships:**
    - `stored_in` → [channel_store](#channel-store)
    - `references` → [waveform](#waveform)

### channel_sample {#channel-sample}

Single channel data point delivered to subscribers — channel_id, timestamp, value, units, sample_interval, source_method.

- **Model:** `litmus.data.channels.models.ChannelSample`
- **Relationships:**
    - `stored_in` → [channel_store](#channel-store)
    - `references` → [channel_descriptor](#channel-descriptor)

### collected_item {#collected-item}

Pytest-collected item with collection-time-assigned indices (step_index, vector_index, vector_count_planned). Enables manifest reconciliation against executed steps to detect unrun vectors.

- **Model:** `litmus.data.models.CollectedItem`

### dut {#dut}

Physical instance of a Product — serial, part_number, revision, lot_number. Created at run start from operator scan or CLI.

- **Model:** `litmus.data.models.DUT`
- **Concept doc:** [docs/concepts/architecture-erd.md](/docs/concepts/architecture-erd.md)
- **Relationships:**
    - `instance_of` → [product](#product)

### instrument_record {#instrument-record}

Tier 3 of the 3-tier instrument model — runtime view combining role + asset + identity + calibration + driver + catalog_ref + mock flag. What the fixture/logger tracks during a session.

- **Model:** `litmus.models.instrument.InstrumentRecord`
- **Relationships:**
    - `identifies` → [instrument_info](#instrument-info)
    - `calibrated_per` → [calibration_info](#calibration-info)
    - `references` → [instrument_catalog_entry](#instrument-catalog-entry)
    - `emits` → [instrument_connected](#instrument-connected)
    - `emits` → [instrument_disconnected](#instrument-disconnected)

### measurement {#measurement}

Single measurement — name, value, units, limit fields, outcome. Carries full signal path (dut_pin, instrument_name, resource, channel, fixture_connection) for traceability. check_limit() is the single comparator-aware judgment path.

- **Model:** `litmus.data.models.Measurement`
- **Relationships:**
    - `stored_in` → [run_store](#run-store)
    - `references` → [outcome](#outcome)
    - `references` → [limit](#limit)
    - `emits` → [measurement_recorded](#measurement-recorded)

### run_summary {#run-summary}

Lightweight run header read from the parquet index (no steps or measurements). Powers the runs list view.

- **Model:** `litmus.data.models.RunSummary`
- **Relationships:**
    - `stored_in` → [run_store](#run-store)
    - `references` → [test_run](#test-run)

### stimulus_record {#stimulus-record}

Captured stimulus (commanded inputs) — param/value/units + instrument/resource/channel + dut_pin + fixture_connection.

- **Model:** `litmus.data.models.StimulusRecord`

### test_run {#test-run}

One complete test execution against one DUT on one Station. Carries DUT/product/station/fixture traceability, profile/facets, git context, operator, collected items, executed steps, custom metadata, and the final outcome.

- **Model:** `litmus.data.models.TestRun`
- **Concept doc:** [docs/concepts/three-stores.md](/docs/concepts/three-stores.md)
- **Relationships:**
    - `stored_in` → [run_store](#run-store)
    - `contains` → [test_step](#test-step)
    - `contains` → [collected_item](#collected-item)
    - `tests` → [dut](#dut)
    - `runs_on` → [station_config](#station-config)
    - `references` → [product](#product)
    - `references` → [fixture_config](#fixture-config)
    - `references` → [profile_config](#profile-config)
    - `references` → [outcome](#outcome)
    - `emits` → [run_started](#run-started)
    - `emits` → [run_ended](#run-ended)

### test_step {#test-step}

One pytest test function invocation. Contains TestVectors expanded from sweep/parametrize. Carries code identity (node_id, file, module, class, function, markers) and a stamped outcome.

- **Model:** `litmus.data.models.TestStep`
- **Relationships:**
    - `stored_in` → [run_store](#run-store)
    - `contains` → [test_vector](#test-vector)
    - `references` → [outcome](#outcome)
    - `emits` → [step_started](#step-started)
    - `emits` → [step_ended](#step-ended)

### test_vector {#test-vector}

One parameter-set execution of a step. Carries params (in_*), observations (out_*), stimulus signal paths, measurements, retry counter, and a per-vector outcome.

- **Model:** `litmus.data.models.TestVector`
- **Relationships:**
    - `stored_in` → [run_store](#run-store)
    - `contains` → [measurement](#measurement)
    - `applies_stimulus` → [stimulus_record](#stimulus-record)
    - `references` → [outcome](#outcome)

### waveform {#waveform}

Time-series data shape — t0 + dt + Y[]. Time axis is reconstructed from t0 + i*dt; no paired timestamps stored. The shape carried by array-type channels and array-valued instrument reads. Not a field on any other Pydantic model — flows through as data, not a typed reference.

- **Model:** `litmus.data.models.Waveform`

## store

### channel_store {#channel-store}

Live waveform / streaming-data store served over Arrow Flight. Subscribers receive ChannelSample batches in real time.

- **Model:** `litmus.data.channels.store.ChannelStore`
- **Concept doc:** [docs/concepts/flight-streaming.md](/docs/concepts/flight-streaming.md)

### event_store {#event-store}

Append-only parquet log of every emitted Event. The source of truth before materialization; subscribers tail it for live UI.

- **Model:** `litmus.data.event_store.EventStore`
- **Concept doc:** [docs/concepts/event-log.md](/docs/concepts/event-log.md)

### run_store {#run-store}

Materialized parquet read model for runs, steps, vectors, measurements. Backed by a long-lived daemon ingesting RunEnded cohorts into DuckDB-queryable parquet.

- **Model:** `litmus.data.run_store.RunStore`
- **Concept doc:** [docs/concepts/three-stores.md](/docs/concepts/three-stores.md)

## event

### calibration_warning {#calibration-warning}

Calibration is near or past due for a role's instrument.

- **Model:** `litmus.data.events.CalibrationWarning`
- **Event type:** `fixture.calibration_warning`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### diagnostic_error {#diagnostic-error}

Free-form error with source/message/details.

- **Model:** `litmus.data.events.DiagnosticError`
- **Event type:** `diagnostic.error`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### diagnostic_warning {#diagnostic-warning}

Free-form warning with source/message/details.

- **Model:** `litmus.data.events.DiagnosticWarning`
- **Event type:** `diagnostic.warning`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### dialog_opened {#dialog-opened}

An operator dialog opened, pausing test execution.

- **Model:** `litmus.data.events.DialogOpened`
- **Event type:** `dialog.opened`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [dialog_responded](#dialog-responded)

### dialog_responded {#dialog-responded}

Operator responded — answered / cancelled / timed_out — with duration_seconds.

- **Model:** `litmus.data.events.DialogResponded`
- **Event type:** `dialog.responded`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### dut_scanned {#dut-scanned}

DUT serial scanned at run start (operator or barcode).

- **Model:** `litmus.data.events.DutScanned`
- **Event type:** `fixture.dut_scanned`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [dut](#dut)

### event_base {#event-base}

Base for all event log events. Carries id, occurred_at, received_at, session_id, run_id (None for session-scope events).

- **Model:** `litmus.data.events.EventBase`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)

### identity_verified {#identity-verified}

Identity check — expected vs actual IDN per role.

- **Model:** `litmus.data.events.IdentityVerified`
- **Event type:** `fixture.identity_verified`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### instrument_configure {#instrument-configure}

A driver configure method was called via proxy.

- **Model:** `litmus.data.events.InstrumentConfigure`
- **Event type:** `instrument.configure`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### instrument_connected {#instrument-connected}

Instrument connected and identified — role + identity + calibration.

- **Model:** `litmus.data.events.InstrumentConnected`
- **Event type:** `fixture.instrument_connected`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [instrument_disconnected](#instrument-disconnected)
    - `records` → [instrument_record](#instrument-record)

### instrument_disconnected {#instrument-disconnected}

Instrument disconnected during teardown.

- **Model:** `litmus.data.events.InstrumentDisconnected`
- **Event type:** `fixture.instrument_disconnected`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### instrument_read {#instrument-read}

A driver read method was called via proxy. Array/waveform values are replaced with a channel:// URI claim-check at JSON-serialize time to keep the column compact.

- **Model:** `litmus.data.events.InstrumentRead`
- **Event type:** `instrument.read`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `references` → [channel_descriptor](#channel-descriptor)
    - `references` → [waveform](#waveform)

### instrument_set {#instrument-set}

A driver set method was called via proxy.

- **Model:** `litmus.data.events.InstrumentSet`
- **Event type:** `instrument.set`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### measurement_recorded {#measurement-recorded}

One measurement landed — name, value, units, outcome, limits, full signal path, plus dynamic vector columns (inputs/outputs/custom).

- **Model:** `litmus.data.events.MeasurementRecorded`
- **Event type:** `test.measurement`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [measurement](#measurement)

### record_event {#record-event}

A key/value record from harness.record() — out-of-band data.

- **Model:** `litmus.data.events.RecordEvent`
- **Event type:** `test.record`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### route_closed {#route-closed}

Switch channels closed to activate a fixture route.

- **Model:** `litmus.data.events.RouteClosed`
- **Event type:** `route.closed`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [route_opened](#route-opened)
    - `records` → [fixture_connection](#fixture-connection)

### route_opened {#route-opened}

Switch channels opened to deactivate a fixture route.

- **Model:** `litmus.data.events.RouteOpened`
- **Event type:** `route.opened`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [fixture_connection](#fixture-connection)

### run_ended {#run-ended}

Emitted at the end of a test run with its final outcome.

- **Model:** `litmus.data.events.RunEnded`
- **Event type:** `run.ended`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [test_run](#test-run)

### run_materialized {#run-materialized}

Emitted by a materializer after a run's state lands in a durable, query-optimized backend. Distinct from run.ended — run can be ended without yet being materialized.

- **Model:** `litmus.data.events.RunMaterialized`
- **Event type:** `run.materialized`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [test_run](#test-run)

### run_started {#run-started}

Emitted once per test run. Full run context — station/DUT/product/ operator/test phase/git/environment.

- **Model:** `litmus.data.events.RunStarted`
- **Event type:** `run.started`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [run_ended](#run-ended)
    - `records` → [test_run](#test-run)

### session_ended {#session-ended}

Emitted at session end. Must NOT carry run_id.

- **Model:** `litmus.data.events.SessionEnded`
- **Event type:** `session.ended`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [session](#session)

### session_started {#session-started}

Emitted once at session start (interactive or test orchestrator). Session-wide metadata only — must NOT carry run_id.

- **Model:** `litmus.data.events.SessionStarted`
- **Event type:** `session.started`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [session_ended](#session-ended)
    - `records` → [session](#session)

### slot_completed {#slot-completed}

A DUT slot finishes execution.

- **Model:** `litmus.data.events.SlotCompleted`
- **Event type:** `slot.completed`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### slot_started {#slot-started}

A DUT slot begins execution. Carries slot_id and dut_serial.

- **Model:** `litmus.data.events.SlotStarted`
- **Event type:** `slot.started`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [slot_completed](#slot-completed)

### step_ended {#step-ended}

Step (or step+vector) finished. Carries per-vector outcome and the step-level aggregate.

- **Model:** `litmus.data.events.StepEnded`
- **Event type:** `test.step_ended`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `records` → [test_step](#test-step)

### step_started {#step-started}

A step (or step+vector) is about to execute. Carries code identity and the commanded sweep inputs.

- **Model:** `litmus.data.events.StepStarted`
- **Event type:** `test.step_started`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [step_ended](#step-ended)
    - `records` → [test_step](#test-step)

### steps_discovered {#steps-discovered}

Full pytest-collected item list, emitted after instruments connect and before steps execute. One per run.

- **Model:** `litmus.data.events.StepsDiscovered`
- **Event type:** `test.steps_discovered`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### stream_ended {#stream-ended}

A stream ended.

- **Model:** `litmus.data.events.StreamEnded`
- **Event type:** `stream.ended`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### stream_frame_index {#stream-frame-index}

Periodic stream-progress beacon (frame_count).

- **Model:** `litmus.data.events.StreamFrameIndex`
- **Event type:** `stream.frame_index`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

### stream_started {#stream-started}

A stream began — stream_id, format, optional file path.

- **Model:** `litmus.data.events.StreamStarted`
- **Event type:** `stream.started`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [stream_ended](#stream-ended)

### sync_arrived {#sync-arrived}

Child process has reached a named sync point.

- **Model:** `litmus.data.events.SyncArrived`
- **Event type:** `sync.arrived`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)
    - `paired_with` → [sync_release](#sync-release)

### sync_release {#sync-release}

Orchestrator unblocks all slots at a sync point.

- **Model:** `litmus.data.events.SyncRelease`
- **Event type:** `sync.release`
- **Relationships:**
    - `stored_in` → [event_store](#event-store)
    - `inherits_from` → [event_base](#event-base)

## enum

### direction {#direction}

Signal direction (INPUT / OUTPUT). Capability matching pairs DUT OUTPUT with instrument INPUT.

- **Model:** `litmus.models.enums.Direction`

### measurement_function {#measurement-function}

Canonical measurement-function vocabulary — dc_voltage, ac_current, resistance, frequency, waveform, etc. ATML/IVI-derived.

- **Model:** `litmus.models.enums.MeasurementFunction`

### outcome {#outcome}

Canonical terminal outcome of a measurement / step / run — past participles. Severity ladder (worst first): ABORTED > TERMINATED > ERRORED > FAILED > PASSED > DONE > SKIPPED. Use escalate_outcome() everywhere cascading is needed.

- **Model:** `litmus.data.models.Outcome`

## lifecycle

### session {#session}

A pytest session or interactive Connect context, identified by a session_id (UUID). Every event carries session_id; cross-store joins use it as the parent key. No single Pydantic model — the lifecycle is bracketed by SessionStarted/SessionEnded events.

- **Concept doc:** [docs/concepts/sessions.md](/docs/concepts/sessions.md)
- **Relationships:**
    - `contains` → [test_run](#test-run)
    - `emits` → [session_started](#session-started)
    - `emits` → [session_ended](#session-ended)

## external

### pytest_test_function {#pytest-test-function}

A pytest test function (`def test_...`) or test method. Owned by pytest; Litmus markers and sidecar config overlay on top of it.
