---
name: litmus-datasheets
description: Use ONLY when the user has an instrument or part datasheet PDF to import into Litmus config (an instrument → catalog entry, or a part → part spec). For writing tests without a datasheet, use litmus-tests instead.
---

# Datasheet import

Turn a datasheet PDF into Litmus config: an **instrument** datasheet becomes a
catalog entry, a **part/DUT** datasheet becomes a part spec plus a test. This is
a deliberate, gated, multi-phase procedure — run it because the user asked to
import a datasheet, never as a reflex to "write me a test." If there's no PDF in
hand, you're in the wrong skill (see `litmus-tests`).

## 1. Route: what did they hand you?

| The user has… | Pipeline | Reference |
|---|---|---|
| An **instrument** datasheet PDF (DMM, PSU, scope, load…) — need exact specs, channels, accuracy | catalog pipeline | `references/catalog-pipeline.md` |
| A **part/DUT** datasheet PDF — need characteristics, pins, and a test that exercises them | test pipeline | `references/test-pipeline.md` |
| A **well-known instrument, no PDF** (Keysight, Tektronix, Rigol, Fluke…) — fast approximate entry | scaffold | `references/scaffold.md` |
| A **batch** of pending PDFs already queued in `catalog/QUEUE.md` | queue processor | `references/process-queue.md` |

If the user's ask doesn't fit any row — e.g. they want a test but have no
datasheet at all — hand off to `litmus-tests` instead of forcing a pipeline here.

## 2. Instrument datasheet → catalog entry

Read `references/catalog-pipeline.md` and follow it exactly, phase by phase. Do
not summarize or shortcut the phases here — that file is the orchestration
contract, not this one.

In outline: the orchestrator never reads the PDF itself. It spawns five
sub-agents from `agents/` in sequence — `section-splitter` maps the PDF into
sections, `scaffold-writer` writes the device-level YAML (channels, connectors,
board attributes), then per section `section-extractor` → `section-writer` →
`section-reviewer` cycle until a mechanical audit script and the reviewer both
report clean. Every phase ends in a checkpoint or gate tag; do not proceed past
one that hasn't fired.

The output is a catalog YAML entry — the same shape a station's
`catalog_ref:` resolves against (see `litmus-stations`).

## 3. Part/DUT datasheet → part spec + test

Read `references/test-pipeline.md` and follow it exactly, phase by phase. This
pipeline is collaborative — it gates on user approval at every phase (part spec,
instrument recommendation, station config, generated test) via
`ask_user_input_v0` / `AskUserQuestion`. Never assume approval and skip a gate.

In outline: parse the datasheet into characteristics + pins → save a part spec
via `litmus_project(action="save", type="part", ...)` → recommend catalog
instruments via `litmus_match` (falling back to generic instruments or the
scaffold path below if the catalog has no match) → build a station → generate
the test file plus its sidecar YAML. Characteristic and limit shapes match what
`litmus-parts` and `litmus-tests` describe — this pipeline is how those artifacts
get their first draft from a datasheet instead of from scratch.

## 4. No PDF, well-known instrument → scaffold

If the user names a common instrument by model number and doesn't have (or need)
the datasheet, read `references/scaffold.md` and follow it. It writes a catalog
entry from model knowledge, marks it `scaffold: true`, and uses conservative
ranges — never claim accuracy you can't back with a source. This is the fast
path; the catalog pipeline in step 2 is the thorough path when exact
specifications matter. If confidence in the instrument is low, say so and
recommend the catalog pipeline instead of guessing.

## 5. Batch of queued datasheets

If the user asks to work through a backlog — "process the queue," "do all the
pending datasheets" — read `references/process-queue.md` and follow it. It loops
the catalog pipeline (step 2) over every `pending` / `pending:redo` row in
`catalog/QUEUE.md`, updating each row's status as it completes, and does not stop
to ask the user between instruments.

## 6. Validate before declaring done

Every pipeline above ends with the artifact still unverified until you run:

```bash
litmus validate --type catalog <path>.yaml   # instrument pipeline
litmus validate --type part <path>.yaml      # test pipeline part spec
litmus validate --type station <path>.yaml   # test pipeline station config
```

or, for the test pipeline, the equivalent MCP save calls
(`litmus_project(action="save", type=..., ...)`), which validate server-side
against the same Pydantic models. A pipeline is not complete until its YAML
validates clean — do not report success on an artifact you haven't checked.

## Deeper

`references/`: `catalog-pipeline.md` (instrument → catalog, full phase spec),
`test-pipeline.md` (part → spec/station/test, full phase spec), `scaffold.md`
(no-PDF fast path), `process-queue.md` (batch driver). `agents/`: the five
sub-agent prompts the catalog pipeline spawns
(`section-splitter`, `scaffold-writer`, `section-extractor`, `section-writer`,
`section-reviewer`). Sibling skills: `litmus-tests` (writing tests without a
datasheet), `litmus-stations` (what a catalog entry feeds into), `litmus-parts`
(part characteristics and limits once the spec exists).
