# Store Module Audit — Phase 2 Module 3

**Scope:** `litmus/store.py` and all YAML I/O across the package.
**Verdict:** the "loaders consolidation" debt is already resolved — `store.py`
IS the single YAML I/O module. No `loaders.py` exists. The remaining issue
was internal DRY.

## Architecture compliance

All 7 files that touch `yaml` were audited:

| File | Verdict |
|---|---|
| `litmus/store.py` | Canonical — all entity YAML load/save |
| `litmus/config/loader.py` | Legitimate bypass — test-config YAML, not entity YAML |
| `litmus/config/fmt.py` | Legitimate — generic formatter used by store.py |
| `litmus/reports/datasheet.py` | Compliant — uses `store.load_catalog_entry` |
| `litmus/ui/pages/designer/page.py` | Legitimate — `yaml.dump()` for preview display only |
| `litmus/ui/shared/services.py` | Compliant — wraps store.py functions |
| `litmus/validation.py` | Compliant — delegates to FILE_LOADERS registry |

No violations of the "all YAML through store.py" rule.

## DRY fix applied

Extracted `_get_by_id()` and `_list_all()` generic helpers, replacing five
copy-pasted get/list pairs (station, fixture, sequence, instrument_asset,
plus the matching portion of the pattern). Catalog's `get_catalog_entry`
and `list_catalog_entries` stay custom (fast-path rglob + type-based
fallback + `load_catalog_from_directory`). Product's `get_product` stays
custom (inheritance resolution).

## Not changed

- Station `save_station()` prefix-matching logic. Quirky but documented
  inline. Used by exactly one code path.
- Catalog inheritance in `load_catalog_entry()`. Complex but correct —
  tested by `test_catalog/test_loader.py`.
- Product inheritance in `load_product()`. Same.
