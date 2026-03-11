# Core Concepts

Litmus organizes hardware testing around five key concepts: **Products**, **Pins & Channels**, **Stations**, **Capabilities**, and **Matching**.

Understanding these concepts is essential for effective use of Litmus, whether you're writing tests, configuring stations, or integrating with existing infrastructure.

## The Big Picture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           LITMUS CONCEPTS                                    │
└─────────────────────────────────────────────────────────────────────────────┘

    WHAT to test          WHERE to test         HOW they connect
    ────────────          ─────────────         ────────────────

    ┌──────────┐          ┌──────────┐          ┌──────────┐
    │ Product  │          │ Station  │          │ Fixture  │
    │ ─ pins   │          │ ─ instrs │          │ ─ points │
    │ ─ chars  │◄────────►│ ─ caps   │◄────────►│ ─ routes │
    └──────────┘          └──────────┘          └──────────┘
          │                     │
          │                     │
          ▼                     ▼
    ┌──────────────────────────────────┐
    │         Capability Matcher        │
    │   "Can this station test this    │
    │         product?"                 │
    └──────────────────────────────────┘
```

## Concept Summary

| Concept | What It Is | Example |
|---------|-----------|---------|
| **Product** | Spec defining what you're testing | TPS54302 DC-DC converter |
| **Pin** | Physical connection point on DUT | J1.3 (output voltage) |
| **Characteristic** | Measurable property of product | output_voltage: 3.3V ±5% |
| **Station** | Physical test bench with instruments | Bench 1 with DMM, PSU, ELoad |
| **Capability** | What an instrument can do | DMM: measure DC voltage |
| **Fixture** | Maps DUT pins to instruments | VOUT → DMM channel 1 |
| **Matching** | Determines station-product compatibility | Bench 1 can test TPS54302 |

## Direction: The Key Insight

The most important concept in Litmus is **direction** — understanding who provides what:

- **Product characteristics** describe what the DUT does
  - `direction: output` = DUT provides this (e.g., output voltage)
  - `direction: input` = DUT receives this (e.g., input power)

- **Instrument capabilities** describe what instruments can do
  - `direction: input` = Instrument measures (receives signal)
  - `direction: output` = Instrument sources (provides signal)

**The matcher flips directions:**
- DUT `output` → Requires instrument `input` (to measure)
- DUT `input` → Requires instrument `output` (to source)

## Learn More

- [Products](products.md) — Defining what you're testing
- [Stations](stations.md) — Configuring test benches
- [Capabilities](capabilities.md) — Understanding capability matching
- [Fixtures](fixtures.md) — Pin-to-instrument mapping
- [Architecture](architecture.md) — System architecture and data flow
- [Event Log](event-log.md) — Unified event stream for all test activity
- [Three Stores](three-stores.md) — EventStore, ChannelStore, and ParquetBackend
- [Sessions](sessions.md) — Sessions as observation windows
- [Flight Streaming](flight-streaming.md) — Cross-process data access via Arrow Flight
