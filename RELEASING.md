# Releasing Litmus

What to check before cutting any release. Copy the checklist into the
release issue/PR and work top-down. The outward-facing steps are
irreversible — do them last, deliberately.

## 1. Code gate

- [ ] `ruff check .` clean
- [ ] `pyright` clean — no new diagnostics
- [ ] `pytest` — full suite green
- [ ] Examples run end-to-end (`tests/test_e2e/`)

## 2. Surfaces reflect this release's API / schema changes

The platform exposes the same data through several surfaces. When the
data model, an API, or terminology changes, these drift silently —
check each against the new shape:

- [ ] **MCP tools** (`src/litmus/mcp/`) — every store/query has a tool;
      no renamed or removed fields; **every MCP tool has an HTTP peer**
      in `src/litmus/api/` (and vice-versa)
- [ ] **Skills + generated `CLAUDE.md`** (`src/litmus/skills/`) — verbs,
      the MCP tool list, and terminology are current; `litmus refs`
      topics cover any new primitive
- [ ] **Grafana dashboards** (`src/litmus/grafana/dashboards/`) — every
      SQL query's columns match the current parquet / channel schema
      (renamed columns break panels silently)
- [ ] **Operator UI** — pages read through the Query API; no stale
      column or field names
- [ ] **Reference docs** regenerated:
      `uv run python scripts/generate_reference_docs.py --all`
      (the `reference-docs-drift` hook must pass)
- [ ] Grep the tree for renamed/removed identifiers — no stragglers

## 3. Data & demos

- [ ] Regenerate demo / example data so no pre-rename or old-schema
      parquet ships (stale columns leak into UI dropdowns)
- [ ] Regenerate UI screenshots if the UI changed

## 4. Version & changelog

- [ ] Bump `version` in `pyproject.toml`; refresh `uv.lock`
- [ ] CHANGELOG: `[Unreleased] → [X.Y.Z] - <date>`, add the compare
      link refs at the bottom, leave a fresh empty `[Unreleased]`
- [ ] Breaking changes called out. Pre-1.0 there are no back-compat
      shims, so any rename / removal is breaking
- [ ] Don't document never-shipped experiments — no tombstones; the
      commit history is the record

## 5. Ship — explicit approval required (irreversible, outward-facing)

- [ ] Merge the integration branch → `main`
- [ ] Tag `vX.Y.Z`
- [ ] Publish to PyPI (`litmus-test`)
- [ ] GitHub release notes from the CHANGELOG section
