# UI screenshot regeneration

The cropped PNGs under `docs/_assets/operator-ui/` are produced by
`scripts/regenerate-ui-screenshots.py`. The script starts a local
`litmus serve` on port 8765, drives Playwright through every entry in
its `MANIFEST`, and writes one PNG per shot. The PNGs are committed;
the script regenerates them in-place when the UI changes.

## Seed the data first

The script renders against `examples/07-profiles` (the canonical
fully-featured example), so it needs that project's data populated —
otherwise the Results / Metrics / Explore shots come back empty. Seed it
before regenerating, and re-seed whenever you want fresh numbers:

```bash
cd examples/07-profiles
# many distinct serials under the production profile → a real yield
# distribution (FPY < 100 %, a measurement pareto, finite Cpk):
for i in $(seq 1 24); do
  uv run pytest --test-phase=production --uut-serial="SN-$(printf %03d $i)" -q
done
cd ../..
```

Note: mocked runs are stamped `test_phase=development` by design (mock
data isn't real), and the Metrics dashboards default to `production` — so
the metrics shots are captured with `?phase=development` in the manifest.

## Running

```bash
uv run python scripts/regenerate-ui-screenshots.py
```

Requires `playwright>=1.58` (already a dev dep) with a Chromium install:

```bash
uv run playwright install chromium
```

The script spawns its own server on port 8765 to avoid colliding with a
running `litmus serve` on the default 8000.

## Adding a shot

1. Open the screen in `litmus serve` and pick the element you want to
   crop to.
2. Inspect the element. If it doesn't already have a stable
   `data-testid` attribute, add one to the relevant module under
   `src/litmus/ui/pages/`. Choose a name shaped like
   `<screen>-<region>`: e.g. `results-table`, `metrics-tabs`,
   `launch-station-picker`. Commit the testid addition separately from
   the docs commit so reviewers see them in isolation.
3. Append a row to `MANIFEST` in `regenerate-ui-screenshots.py`:

   ```python
   Shot(
       url="/results",
       selector="[data-testid='results-table']",
       output_path="results/table.png",
   ),
   ```

4. Re-run the script. The new PNG lands at
   `docs/_assets/operator-ui/results/table.png`.
5. Reference it from the docs page with a relative path:

   ```markdown
   ![Results — run table](../../_assets/operator-ui/results/table.png)
   ```

## Updating an existing shot

UI changed and an existing PNG looks stale? Just re-run the script. The
output paths are deterministic — every shot overwrites the previous PNG
in place. The diff in `git status` shows which screenshots actually
changed; commit only those.

The pre-commit hook `screenshot-drift-reminder` (in
`.pre-commit-config.yaml`, implementation at
`scripts/check-screenshot-drift.py`) helps you remember: when you
commit a file under `src/litmus/ui/pages/` that contains a
`data-testid` referenced in this script's `MANIFEST`, the hook prints
the rerun command. It's a reminder, not a gate — it exits 0 either
way. The author still has to actually re-run the script and commit
the regenerated PNGs.

## Conventions

| Topic | Rule |
|---|---|
| Default viewport | 1440×900. Override the width per-shot via `Shot(..., viewport_width=N)` when a wide element (e.g. a results table) would otherwise render past the docs content column. |
| Device pixel ratio | 2× always. Source PNG dimensions are double the displayed dimensions; the browser downsamples for crisp retina rendering. Don't override this per shot — pages assume uniform 2× source. |
| Filename | `<screen>/<region>.png` — match the docs subdirectory structure |
| Selector | `[data-testid='...']` — stable testids only. No CSS class selectors (they break on Tailwind reflows). |
| Page width | Don't capture full-page screenshots from this script — those belong on the tour page only and are captured manually with a viewport screenshot. This script is for cropped element shots. |
| Element scope | One element per shot. If you want two regions on the same screen, write two entries. |

## Sizing for the docs renderer

The cropped PNGs are displayed inside two docs renderers — pragmatest's
Next.js prose column (reference pages cap at `max-w-5xl` ≈ 1024px) and
NiceGUI's in-app docs column (narrower). To fit both without per-image
markup:

* Pick a `viewport_width` that lets the element render at roughly the
  display width you want, knowing the source is 2×.
* If you want a results table to fill ~960px in the docs, capture at
  `viewport_width=960` (the source PNG is then ~1920px wide; browser
  shows it at 960 logical pixels on a 2× display, ~960 CSS pixels in
  the prose column).
* Smaller elements (filter rows, status pills, single buttons) usually
  fit fine at the default 1440 viewport — the cropped output is small
  enough that no scaling is needed.

The renderer-side CSS framing (subtle border + shadow on `.docs img`)
lands in a follow-up commit once we have a real screenshot to tune
against; until then PNGs render bare against the page background.

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| `litmus serve did not respond at http://127.0.0.1:8765 within 45s` | Server failed to start (port in use, missing dep, …) | Run `uv run litmus serve --port 8765` manually and see the real error |
| `selector "[data-testid='foo']" did not resolve on /bar` | Testid not present on the element, or wrong page | Open the page in a browser, verify the testid exists |
| PNG looks empty / mostly white | Element rendered but content loaded after `networkidle` | Add an explicit wait selector for the content you care about, or capture a more specific child element |

## Why this exists

UI screenshots in docs rot fast: layout shifts, label changes, color
tweaks all desync the screenshot from the running app. A reader who
trusts a stale screenshot over the actual UI gets misled. Repeatable
regeneration shortens that gap from "rewrite the docs page" to "re-run
this script."

It's also a forcing function: every documented region needs a stable
testid. If the answer to "what testid?" is "there isn't one" — that's
itself a UI-instrumentation gap worth fixing.
