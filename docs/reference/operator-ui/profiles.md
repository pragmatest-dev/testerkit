# Profiles

**URLs:** `/profiles` (list), `/profiles/{name}` (detail)

A profile is a named configuration set declared in `litmus.yaml` under `profiles:` or
as a standalone file in `profiles/*.yaml`. It carries session-wide test configuration —
limits, mocks, markers, retry rules — plus metadata that ties the profile to a
station type and a fixture, with optional facet labels. Profiles are config-only: they exist in YAML and
are resolved at session start. The Profiles pages browse declared profiles and inspect
one profile's resolved state.

## List — `/profiles`

A table with one row per configured profile. A badge in the page header shows the
total count.

| Column | What it shows |
|---|---|
| Name | The profile name (key in `litmus.yaml` or YAML filename stem) |
| Extends | The parent profile this profile inherits from, or `—` if none |
| Station Type | The `station_type` this profile requires at session start, or `—` if unset |
| Fixture | The fixture ID this profile selects, or `—` if unset |
| Facets | Key=value pairs from the profile's `facets:` map, comma-separated, or `—` |
| Tests | Count of test-level overrides declared in this profile's `tests:` block |

The table has no Configured / Observed chip or filter. Profiles are declared in YAML
and have no run-history backing, so the merged-with-badge pattern does not apply —
every row is configured-only by definition.

Clicking a row navigates to `/profiles/{name}`.

### Empty state

When no profiles are declared in the project, the table is replaced with a card:

> No profiles configured.
>
> Add profile YAML files under profiles/ or declare them inline in litmus.yaml.
> See the profiles reference for the schema.

## Detail — `/profiles/{name}`

The detail page has no tabs. It renders three cards stacked vertically:

**Summary card** — shows the same five fields as the list (Extends, Station type,
Fixture, Facets, Tests count) in a row of labeled key-value pairs.

**Inheritance card** — present only when the profile has one or more parents. It
displays the full extends chain as a left-to-right sequence of names separated by
arrows, root-first. Parent names are links to their own detail pages. The current
profile's name appears as a primary badge (not a link). The card carries a note:
"Parent profiles are applied first; this profile's fields override the merged result."

The Inheritance card is omitted when the profile has no `extends:` set.

**Resolved YAML card** — displays the profile's merged configuration as YAML in a
code block. It shows the profile's configuration after its parent profiles are
merged in — values inherited from a parent that this profile didn't override appear
here as if declared locally. The YAML is read-only; edits require changing the source
file directly.

### Launch shortcut

A **Launch Test** button appears in the page header (top right). It navigates to
`/launch?test_profile={name}`, which pre-selects this profile in the launch page's
Profile dropdown.

### Not-found state

When `{name}` does not match any declared profile, the page shows a card:

> Profile '{name}' not found.

with a "← Back to Profiles" link.

## Underlying data

Profiles are loaded by merging two sources at server start:

1. The `profiles:` block declared inline in `litmus.yaml`.
2. Any `profiles/*.yaml` files in the project root (loaded by filename stem).

A name collision between an inline entry and a file raises an error at load time.
The detail page's Resolved YAML reflects this merged state — the same config the
session resolver acts on at runtime.

For the full schema of a profile block, see
[Configuration reference → Profile blocks](../configuration.md#profile-blocks-under-profiles).

For the workflow of authoring and selecting profiles, see
[How-to → Profiles](../../how-to/execution/profiles.md).

## See also

- [Configuration reference → Profile blocks](../configuration.md#profile-blocks-under-profiles) —
  every field in a `ProfileConfig`, including `extends`, `facets`, `station_type`,
  `fixture`, and `verify_requires_limit`
- [How-to → Profiles](../../how-to/execution/profiles.md) — how to author, extend,
  and select profiles at session start
- [Launch Test](launch.md) — the `/launch` page the detail page's Launch button
  navigates to
- [Stations](stations.md) — `station_type` ties a profile to a class of station
- [Fixtures](fixtures.md) — `fixture` ties a profile to a fixture ID
