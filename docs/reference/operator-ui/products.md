# Products

**URLs:** `/products` (list), `/products/new`, `/products/{id}`, `/products/{id}/edit`, `/products/{id}/stations` (the Stations tab as its own URL — bookmarkable)

A product is a DUT under test — its part number, revision,
characteristics (named measurements with limits and units), and pin
map. The Products entity pages are the browser surface for the
project's `products/` directory.

## List — `/products`

A table with one row per product that is either configured in the project
(a YAML file exists) or has been observed in run history (no YAML file,
only referenced by past runs). Columns:

| Column | What it shows |
|---|---|
| Status | **Configured** chip (grey) — a YAML file exists. **Observed** chip (amber) — appears in run history but has no YAML file. |
| ID | Product identifier |
| Name | Human-readable name |
| Rev | Revision string (when set) |
| Chars | Count of characteristics defined on the product; `—` for Observed rows |
| Runs | Total runs that have tested this product |
| Passed | Run count with outcome `passed` |
| Failed | Run count with outcome `failed` |
| Last Run | Most recent run start timestamp, browser-local time |

Above the table, a filter card with **All / Configured / Observed** buttons
narrows the view. The active filter is mirrored into the URL so the view is
bookmarkable.

A **New Product** button at the top right jumps to `/products/new`.
Clicking a Configured row jumps to the detail view at `/products/{id}`.
Observed rows are not clickable — no YAML exists to display.

When no products are configured or observed, the table is replaced with an
empty-state card explaining the entity and offering a New Product button.

## Detail — `/products/{id}`

A header bar (Back + Edit buttons), one Product Information card
(ID, name, revision, description), then a tab strip with three tabs:

| Tab | Content |
|---|---|
| Pins | The DUT pin map (one row per pin: pin, signal, description) |
| Characteristics | One row per characteristic: name, function, direction, units, limit shape |
| Stations | Compatible stations + the required capabilities the matcher resolved against. Each entry is a card with a View button that opens the matching station detail page. |

When the URL points at a missing product, the page shows a "Product
not found." card.

## Edit — `/products/{id}/edit`

A header bar with the product name + a "Changes auto-saved" hint
(no Save button), then a tab strip with three tabs:

| Tab | Content |
|---|---|
| Info | Name / Description / Revision (Product ID is read-only) |
| Pins | Per-pin rows — add, remove, or edit pin / signal / description |
| Characteristics | Existing characteristics show Function / Direction / Units as a read-only summary; the **Add Characteristic** dialog is where you set those fields on new entries. |

## New — `/products/new`

Different shape from Edit — a **single non-tabbed form** with just
Product ID, Name, and Description fields. After creation, the page
redirects to `/products/{id}/edit` so you can add pins and
characteristics in their dedicated tabs.

## Underlying data

Products are stored as YAML — either flat (`products/<id>.yaml`) or
in a per-product subdirectory (`products/<id>/<id>.yaml`); both
layouts are supported.

For the YAML schema, see
[Configuration reference → Products](../configuration.md#product-yaml).
For the concept, see [Concepts → Products](../../concepts/configuration/products.md).

## Common tasks

- **Add a characteristic** — open `/products/{id}/edit`, switch to
  the Characteristics tab, click Add. The dialog captures name,
  function, direction, and units. **Limits and tolerance bands are
  set in the product YAML**, not in this UI — edit the YAML in
  `products/<id>/<id>.yaml` (or the flat-layout equivalent) and the
  Detail page picks them up on next load.
- **Check which stations can run this product** — pre-fill the
  [Launch Test](launch.md) page with `?product=<id>` and read the
  Station hint.

## See also

- [Configuration reference → Products](../configuration.md#product-yaml)
- [Concepts → Products](../../concepts/configuration/products.md)
- [Launch Test](launch.md) — start a run for a product
