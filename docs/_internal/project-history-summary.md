# TesterKit — Executive Summary (one page)

*Jan 27 – Jul 5, 2026 · ~1,350 commits · 6 releases (v0.1.0 → v0.3.0) · 67 merged PRs*

**What it is.** In roughly five months, TesterKit went from an empty repo to a
shipped, PyPI-published hardware test *platform* — pytest-native test authoring,
an event-sourced data layer, an operator UI, and a first-class AI surface
(MCP + CLI) — across three minor releases.

**The arc in one breath.** A complete platform skeleton appeared in the first
*week* (Jan). February taught it to turn datasheets into tests. March made the
pivotal architectural bet — **event-sourcing** — and added parallel multi-DUT
execution. April tore out the bespoke test-orchestration machinery and went
**all-in on pytest**. May shipped **v0.1.0** and wrote/audited an entire docs
corpus. June delivered the **v0.2.0 data plane** (channels, files, streaming)
and then began a careful **schema reckoning** that became **v0.3.0** in July.

**The three decisions that mattered most:**
1. **Everything derives from an append-only source** — the event log (Mar) and
   the content-addressed derived index (Jul) are the same idea at two layers.
2. **Integrate, don't reinvent** — pytest, Pydantic, Parquet, DuckDB, NiceGUI;
   the *bespoke* sequence/decorator layer was deleted once pytest could carry it.
3. **AI is a peer consumer** — MCP + CLI + skills present from week one, kept in
   lockstep with the human UI.

**How the team works (visible in every month):**
- **Audit until clean** — a find → fix → *re-audit* → repeat loop, applied to
  code (subsystem design reviews) and to docs (seven purpose-built audit agents).
- **Vocabulary is design** — a large share of commits are principled renames
  (`product→part`, `dut→uut`, `slot→site`, `attempt→retry`, `units→unit`); the
  project pays the rename cost repeatedly to keep concept names honest.
- **Pre-1.0 means break freely** — backcompat shims are added and then
  deliberately dropped; schema versions reset to 0.1 as an explicit "not frozen."
- **Docs are verified against source**, generated where possible, and written for
  test engineers — not framework authors.

**Biggest direction changes:** journal → event log (Mar); sequences → pytest-native
(Apr); capability model V1 → V2 / unified `SpecBand` (Feb); thread-based
instrument concurrency → `InstrumentServer` with per-resource locking (Mar).

**Tempo signal.** Throughput *accelerated* with maturity: June alone carried
more than a third of all commits, once the foundations were stable enough to
build on fast.

**Where it stands (0.3.1, in flight):** derived-index versioning — the daemon's
DuckDB projections are now content-addressed and rebuilt on fingerprint
mismatch, so the projection layer can evolve without corrupting or blocking
readers.

*Full narrative: `project-history-narrative.md`. Living design records:
`docs/_internal/explorations/`.*
