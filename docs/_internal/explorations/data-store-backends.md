# Data-store architecture & backend-swap targets

> What's shared across the four stores, what differs, and — per store — the
> proven server-grade systems each can be swapped out for if/when Litmus
> runs with real infrastructure (requirement #6). Recorded so the embedded
> tier is built to a contract that keeps these doors open, not painted into
> a corner.

## The shared thing is the SERVICE, not the data shape

All four stores mirror **one service contract** (this is the six requirements):

> a singleton **daemon owns a warm index** over that store's durable data,
> serves **at-rest query + live push**, runs **parallel / no-poll**, behind a
> **backend-neutral verb API** that is swappable to a remote backend by
> changing only a connection address.

What they do **not** share is the data model. They are four stores precisely
because the data differs:

| Store | Native data shape | Daemon's index is… | Ordering key |
|---|---|---|---|
| **Events** | append **log** of immutable records | a projection of the log (log-centric) | per-stream offset |
| **Runs** | **derived from events** (mutable aggregate) | a materialized **view/consumer** of the events log | (run, step, vector) keys |
| **Channels** | **time-series** sample segments | a time-indexed view (range / decimate / last-N) | time / sample index |
| **Files** | write-once **blobs** + sidecar metadata | a **catalog over the metadata** (blobs by URI) | created-at / metadata |

"Log-centric" is the right model for **events** (and the write side of
channels). Runs is the **view** half of that picture. Channels is a
**time-series** store. Files is **object storage + a metadata catalog**. A
10 MB artifact is not a log record; a run is not a log. Forcing one shape on
all four is wrong.

## Backend-swap targets (requirement #6)

The embedded tier (Flight transport + append files + DuckDB view + our
pub/sub) is the **no-infra default**. With server infrastructure, each store
swaps to a system that provides its shape **natively**, behind the same verb
API:

### Events — durable ordered log + pub/sub
- **Log / push:** Apache Kafka · Apache Pulsar · Redpanda · NATS JetStream ·
  Redis Streams · AWS Kinesis. (Topic = store; partition = our per-stream;
  offset = our per-stream offset; consumer = our SUB tail.)
- **At-rest query / search view:** ksqlDB · Flink SQL · Materialize ·
  RisingWave (streaming materialized views) — or sink to ClickHouse /
  Elasticsearch for event search.

### Runs — relational / analytic materialized view
- **Serving store:** PostgreSQL (operational, mutable run/step/measurement
  rows) · ClickHouse · Snowflake · BigQuery · MotherDuck (analytics scale).
- **View maintenance from the events log:** Materialize · RisingWave ·
  Flink — the same "runs is a consumer of events" shape, server-grade.

### Channels — time-series database
- InfluxDB · TimescaleDB (Postgres extension) · QuestDB · VictoriaMetrics ·
  ClickHouse · AWS Timestream. (Native time-range, downsampling/decimation,
  retention; some offer live tailing for the push side.)

### Files — object storage + metadata catalog
- **Blobs:** AWS S3 · GCS · Azure Blob · MinIO (native HTTP Range,
  durability, lifecycle).
- **Catalog:** PostgreSQL · DynamoDB · a metastore (the sidecar metadata
  index).
- **Live:** S3 event notifications · SNS/SQS · object-store change feeds
  (replaces our file-landed push).

### Cross-cutting — unified at-rest analytics (optional)
- Land all stores' at-rest data in a **lakehouse** — Delta Lake · Apache
  Iceberg · Apache Hudi — queried by DuckDB / Trino / Spark / ClickHouse.
  (See the separate follow-up to evaluate ClickStack vs Delta/Iceberg as
  peers.)

## What keeps the swap clean (build to this now)

The swap is a backend substitution, not a rewrite, **iff** the embedded tier
holds these contracts:

1. **Client API is verb-level and backend-neutral** — `emit` / `events` /
   `on_event` / `observe` / `stream` / `write`. No DuckDB, Flight ticket
   shapes, `nextval`, or file paths leak to test code.
2. **Order lives in the data, not in write-timing** — events carry a
   per-stream offset (a Kafka partition offset's role); `event_number` is an
   internal index detail, demoted from the public contract.
3. **The index is a derived view with a single writer** — never a
   dual-write. A backend swap then means "the view consumes the new backend
   instead of our files."
4. **Consumer contract = in-order delivery per stream + resume from an
   opaque cursor** — identical to a broker consumer.
5. **Connection is config-driven** — local subprocess address or a remote
   address; nothing else changes.

Honest limits: single-node → distributed is more than a swap (partitioning
across nodes); each backend needs a thin protocol adapter under the verb
API; we provide at-least-once + id-dedup (forward-compatible with brokers'
exactly-once).

## req-6 serving-tier swap — proven recipe (deferred until a real server)

Contract #5 (config-driven connection) is the **serving-tier** half of req 6:
point a client at a *remote* daemon instead of spawning a local one. This was
built and tested green on 2026-06-09, then **reverted** — until a real remote
daemon exists the hook is four dead env vars nobody points at, so we don't ship
it. It's recorded here because the proof is the deliverable: req 6's gate is
"architecture *proven* swap-ready," not "hook shipped."

**Why deferring is safe (not the FileStore trap).** Every store's client already
resolves its daemon via `<store>_manager.acquire(dir) -> opaque grpc:// location`,
then `FlightQueryClient(location)`. The location is *already opaque* — clients
connect by address, never knowing local vs remote. So the hook is purely
**additive inside `acquire()`**, not a rewrite. (Contrast FileStore's old bespoke
local I/O, which leaked a `Path` and would have forced a rewrite for S3.)

**Recipe** (≈ one helper + four one-line hooks + a test):

1. `src/litmus/data/_daemon_lifecycle.py`:
   ```python
   def configured_remote_location(env_key: str) -> str | None:
       loc = os.environ.get(env_key, "").strip()
       return loc or None
   ```
2. At the top of each module `acquire(dir)`, before `mgr = ...; mgr.acquire()`:
   ```python
   remote = configured_remote_location("LITMUS_<STORE>_DAEMON")
   if remote:
       return remote
   ```

   | store | module | env var |
   |---|---|---|
   | events | `duckdb_manager.py` | `LITMUS_EVENTS_DAEMON` |
   | runs | `runs_duckdb_manager.py` | `LITMUS_RUNS_DAEMON` |
   | channels | `channels/flight_manager.py` | `LITMUS_CHANNELS_DAEMON` |
   | files | `files/catalog_manager.py` | `LITMUS_FILES_DAEMON` |
3. Proof `tests/test_data/test_daemon_swap_seam.py`: parametrize over the four
   `acquire` functions — env set → the address is returned and **no local daemon
   is spawned** (`not list(dir.iterdir())`); unset/empty → `None` (local path).
4. Landing for real also gets a `litmus.yaml` / `ProjectConfig` field (mirroring
   F1's `files_backend` + `LITMUS_FILES_BACKEND`) and one end-to-end test against
   an actual remote daemon. The remote daemon is externally managed — the client
   connects, it does not spawn or supervise it.
