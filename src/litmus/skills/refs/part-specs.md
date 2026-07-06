# Part specs

A part spec is YAML under `parts/<id>.yaml`, validated against
`Part` (`litmus/models/part.py`). It exists so a **limit** and a
**pin mapping** can live once per product and be referenced by name
from every test, instead of being retyped per test file.

**Gate:** don't scaffold `parts/` for a one-off bench check — sidecar
`limits: {low, high, unit}` (see [`verify`](verify.md)) covers that.
Reach for a part spec at Tier 2+ (see [`tiers`](tiers.md)): several
tests share the same limit, or a non-developer needs to edit a spec
value without touching test code or YAML sidecars per-file.

## Shape

```yaml
# parts/buck_3v3.yaml
id: buck_3v3
part_number: DEMO-BUCK-3V3        # printed/scanned identifier; stamped
name: Demo 3.3 V Buck Converter    # on every run as uut_part_number
revision: A

pins:
  TP_VIN:  {name: TP1, net: VIN_5V}
  TP_VOUT: {name: TP2, net: VOUT_3V3}
  TP_GND:  {name: TP3, net: GND, role: ground}

characteristics:
  rail_3v3:
    function: dc_voltage           # MeasurementFunction enum
    direction: output               # UUT provides this (input = UUT receives)
    unit: V
    pin: TP_VOUT
    bands:
      - when: {temperature: {min: 0, max: 50}}
        value: 3.3
        accuracy: {pct_reading: 3.0}
      - when: {temperature: {min: 50, max: 85}}
        value: 3.3
        accuracy: {pct_reading: 5.0}   # wider tolerance at high temp
```

`function` and `direction` are required on every characteristic.
`pin` / `pins` / `net` / `signal_group` — at least one physical
interface is required; `pin` wins if more than one is set.

`bands:` is a list of `SpecBand` entries; each band's `value` +
`accuracy` (`pct_reading` / `pct_range` / `absolute`) is the spec at
one operating point. `when:` matches by condition (temperature,
load, …) — the first band whose `when:` matches the active vector
params wins; a band with no `when:` matches everything, so it can
sit last as the catch-all.

Full field reference (pins, signal groups, buses, variant
inheritance via `base:`): `docs/concepts/configuration/parts.md`.

## How `verify` reaches a part spec

`verify` never reads `parts/` directly — it walks the [limit
resolution chain](verify.md#limit-resolution-chain), and one link in
that chain is the active part. Three ways a limit ends up sourced
from a characteristic:

1. **Sidecar delegate** — `limits: {name: {characteristic: <id>,
   tolerance_pct: N}}` derives `low`/`high` from the band's `value` ±
   `N %` at the active vector params. `{characteristic: <id>}` alone
   (no tolerance) copies the band's `low`/`high`/`unit`/`spec_ref`
   outright.
2. **Name-match fallback** — no sidecar/marker/profile entry at all
   for the measurement name: the active part context is checked for
   a characteristic whose id equals the name.
3. **Per-call override** — `verify(name, value,
   characteristic="rail_3v3")` binds that call to a specific
   characteristic without a sidecar entry — useful when one test
   measures the same characteristic under two names.

Guardband (`guardband_pct`) tightens whatever bounds the
characteristic derives, applied symmetrically before tolerance math.

## Selecting the active part

```bash
pytest --part buck_3v3
pytest --uut-part-number DEMO-BUCK-3V3
```

The session-scoped `part` fixture resolves (first match wins):
`--part <id-or-path>` → `--uut-part-number <pn>` (content match
against `part_number:`) → the single file in `parts/` if there's
exactly one → `None` (bringup tier). Two-plus `parts/*.yaml` files
with neither flag set is a `UsageError` — Rev-B swaps need an
explicit selector.

`@pytest.mark.litmus_characteristics(["rail_3v3", ...])` (sidecar
key `specs:`) declares which characteristics a test scopes to; it
bounds what `ctx.connections` and per-limit `characteristic:` can
reference.

## When to graduate

- **One test, one bench** — inline `Limit(...)` or a sidecar
  `limits:` entry. No `parts/` directory needed.
- **Several tests share a limit, or it must be operator-editable
  without a code review** — a part spec. Every test referencing
  `characteristic: rail_3v3` picks up an edit to
  `parts/buck_3v3.yaml` with no test-code change.
- **A product family with per-variant tolerances** — `base: <parent
  id>` inherits pins/characteristics from a parent part; a variant
  overrides just the sections that differ (e.g. tighter accuracy on
  an industrial-temp SKU).

## CLI / MCP surface

There is no dedicated `litmus parts` CLI group. Parts are one of the
generic project entity types:

| Surface | Call | HTTP equivalent |
|---------|------|------------------|
| List parts | `litmus_project(action="list", type="part", project=...)` | `GET /parts` |
| Get one part | `litmus_project(action="get", type="part", id=<part_id>, project=...)` | `GET /parts/{part_id}` |
| Create/update a part | `litmus_project(action="save", type="part", id=<part_id>, content={...}, project=...)` | — (MCP-only) |
| Part JSON Schema | `litmus_schema(yaml_type="part")` — call before any `save` | — (MCP-only) |

## Cross-references

- `litmus/models/part.py` — `Part`, `PartCharacteristic`, `Pin`
- `litmus/models/capability.py` — `SpecBand`, `AccuracySpec`, `Capability`
- `litmus/parts/context.py` — `PartContext.get_limit`
- `litmus/execution/sidecar.py` — `resolve_limit`, `_resolve_single`
- [`verify`](verify.md) — full limit resolution chain
- [`tiers`](tiers.md) — when to graduate from sidecar to part spec
- `docs/concepts/configuration/parts.md` — full YAML field reference
