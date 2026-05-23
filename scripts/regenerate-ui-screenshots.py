"""Regenerate the cropped screenshots used by ``docs/reference/operator-ui/``.

Usage::

    uv run python scripts/regenerate-ui-screenshots.py

What it does:

1. Starts a local ``litmus serve`` instance on a non-default port (so the
   developer's own running server, if any, isn't disturbed).
2. Waits for the server to respond at ``/``.
3. Walks :data:`MANIFEST`. For each :class:`Shot`, navigates to the
   page, waits for the target element, and writes a cropped PNG to
   ``docs/_assets/operator-ui/<output_path>``.
4. Tears the server down.

The manifest is the source of truth. Adding a screenshot to a docs page
means appending a row here and re-running the script. The PNGs are
committed; this script regenerates them in-place when the UI changes.

Each ``selector`` must resolve to exactly one element via a stable
``data-testid`` attribute. If the page doesn't already have a testid
on the target element, add one to the relevant ``src/litmus/ui/pages/``
module first — that's part of writing the matching docs page (see
``scripts/SCREENSHOTS.md``).
"""

from __future__ import annotations

import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import ViewportSize, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSET_ROOT = REPO_ROOT / "docs" / "_assets" / "operator-ui"
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8765
SERVE_URL = f"http://{SERVE_HOST}:{SERVE_PORT}"
VIEWPORT: ViewportSize = {"width": 1440, "height": 900}
SERVER_READY_TIMEOUT_S = 45.0
ELEMENT_WAIT_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class Shot:
    """One cropped screenshot the docs depend on.

    ``url`` is appended to :data:`SERVE_URL` (so use a leading slash:
    ``/results``, ``/metrics``, ...). ``selector`` is a CSS selector
    that must resolve to a single element; the resulting PNG is cropped
    to that element's bounding box. ``output_path`` is relative to
    :data:`ASSET_ROOT` and ends in ``.png``.

    ``viewport_width`` overrides the default capture width for shots
    where a wide element (e.g. a results table) would otherwise render
    far beyond the docs content column. The shot's height tracks the
    default. ``None`` (the default) uses :data:`VIEWPORT`'s width.

    All shots are captured at ``device_scale_factor=2``; the source PNG
    is twice the displayed dimensions so retina downsampling stays crisp.
    """

    url: str
    selector: str
    output_path: str
    viewport_width: int | None = None


# Manifest grows as ``docs/reference/operator-ui/`` pages land. Group
# by screen subdirectory so the file stays scannable.
#
# Example row (kept commented so the first run is a no-op):
#
#   Shot(
#       url="/results",
#       selector="[data-testid='results-table']",
#       output_path="results/table.png",
#   ),
MANIFEST: list[Shot] = [
    # /results — run history
    Shot(
        url="/results",
        selector="[data-testid='results-table']",
        output_path="results/table.png",
    ),
    Shot(
        url="/results",
        selector="[data-testid='results-stats']",
        output_path="results/stats.png",
    ),
    # /results/{run_id} — single-run detail. {LATEST_RUN} resolves at
    # script-start from ``litmus runs --json --limit 1`` so the shots
    # always point at an existing run.
    Shot(
        url="/results/{LATEST_RUN}",
        selector="[data-testid='result-header']",
        output_path="results/detail-header.png",
    ),
    Shot(
        url="/results/{LATEST_RUN}",
        selector="[data-testid='result-overview']",
        output_path="results/detail-overview.png",
    ),
    Shot(
        url="/results/{LATEST_RUN}?tab=Steps",
        selector="[data-testid='result-steps']",
        output_path="results/detail-steps.png",
    ),
    Shot(
        url="/results/{LATEST_RUN}?tab=Measurements",
        selector="[data-testid='result-measurements']",
        output_path="results/detail-measurements.png",
    ),
    # /metrics — yield / pareto / cpk / retest / time-loss / assets
    Shot(
        url="/metrics",
        selector="[data-testid='metrics-filters']",
        output_path="metrics/filters.png",
    ),
    Shot(
        url="/metrics",
        selector="[data-testid='metrics-yield']",
        output_path="metrics/yield.png",
    ),
    Shot(
        url="/metrics?tab=Pareto",
        selector="[data-testid='metrics-pareto']",
        output_path="metrics/pareto.png",
    ),
    Shot(
        url="/metrics?tab=Cpk",
        selector="[data-testid='metrics-cpk']",
        output_path="metrics/cpk.png",
    ),
    # /explore — Measurements (parametric viewer)
    Shot(
        url="/explore",
        selector="[data-testid='explore-filters']",
        output_path="explore/filters.png",
    ),
    Shot(
        url="/explore",
        selector="[data-testid='explore-plot-controls']",
        output_path="explore/plot-controls.png",
    ),
    Shot(
        url="/explore",
        selector="[data-testid='explore-chart']",
        output_path="explore/chart.png",
    ),
    # /events — event log browser
    Shot(
        url="/events",
        selector="[data-testid='events-filters']",
        output_path="events/filters.png",
    ),
    Shot(
        url="/events",
        selector="[data-testid='events-table']",
        output_path="events/table.png",
    ),
    # /channels — streaming-signal browser
    Shot(
        url="/channels",
        selector="[data-testid='channels-table']",
        output_path="channels/table.png",
    ),
    # / — Dashboard landing
    Shot(
        url="/",
        selector="[data-testid='dashboard-stations']",
        output_path="dashboard/stations.png",
    ),
    Shot(
        url="/",
        selector="[data-testid='dashboard-runs']",
        output_path="dashboard/runs.png",
    ),
]


def _wait_for_server(timeout_s: float = SERVER_READY_TIMEOUT_S) -> None:
    """Poll :data:`SERVE_URL` until it returns 200 or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(SERVE_URL, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 — connection refused / timeout are expected during startup
            last_err = exc
        time.sleep(0.5)
    raise RuntimeError(
        f"litmus serve did not respond at {SERVE_URL} within {timeout_s}s; last error: {last_err!r}"
    )


def _capture(shots: list[Shot]) -> int:
    """Drive Playwright through ``shots`` and write the cropped PNGs.

    Returns the number of shots written.
    """
    if not shots:
        print("regenerate-ui-screenshots: manifest is empty, nothing to capture.")
        return 0
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    written = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 2x device pixel ratio so PNGs stay crisp when the browser
        # downsamples them to the docs content column on a retina
        # display. Source dimensions are twice the displayed dimensions.
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = ctx.new_page()
        try:
            for shot in shots:
                target = ASSET_ROOT / shot.output_path
                target.parent.mkdir(parents=True, exist_ok=True)
                # Per-shot viewport override: wide elements like results
                # tables render past the docs column at the default 1440;
                # narrowing the viewport for that shot lets them render
                # at a column-appropriate width before cropping.
                width = (
                    shot.viewport_width if shot.viewport_width is not None else VIEWPORT["width"]
                )
                page.set_viewport_size({"width": width, "height": VIEWPORT["height"]})
                page.goto(SERVE_URL + shot.url, wait_until="networkidle")
                element = page.wait_for_selector(shot.selector, timeout=ELEMENT_WAIT_TIMEOUT_MS)
                if element is None:
                    raise RuntimeError(
                        f"selector {shot.selector!r} did not resolve on {shot.url!r}"
                    )
                element.screenshot(path=str(target))
                print(f"regenerate-ui-screenshots: wrote {target.relative_to(REPO_ROOT)}")
                written += 1
        finally:
            browser.close()
    return written


def _resolve_placeholders(shots: list[Shot]) -> list[Shot]:
    """Substitute dynamic placeholders in shot URLs.

    Currently supports ``{LATEST_RUN}`` — resolved to the most recent
    run id from ``litmus runs --json --limit 1`` so detail-page shots
    don't go stale every time the seed data churns.
    """
    needs_latest = any("{LATEST_RUN}" in s.url for s in shots)
    if not needs_latest:
        return shots
    import json

    raw = subprocess.check_output(
        ["uv", "run", "litmus", "runs", "--json", "--limit", "1"],
        cwd=REPO_ROOT,
        text=True,
    )
    rows = json.loads(raw)
    if not rows:
        raise RuntimeError(
            "{LATEST_RUN} placeholder used but ``litmus runs`` returned no rows; "
            "run a test (e.g. ``cd examples/02-verify && uv run pytest``) and retry."
        )
    latest = rows[0].get("test_run_id") or rows[0].get("run_id")
    if not latest:
        raise RuntimeError(f"{{LATEST_RUN}} resolver: no run_id in row {rows[0]!r}")
    resolved = [
        Shot(
            url=s.url.replace("{LATEST_RUN}", latest),
            selector=s.selector,
            output_path=s.output_path,
            viewport_width=s.viewport_width,
        )
        for s in shots
    ]
    print(f"regenerate-ui-screenshots: resolved {{LATEST_RUN}} -> {latest[:8]}")
    return resolved


def main() -> int:
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "litmus",
            "serve",
            "--host",
            SERVE_HOST,
            "--port",
            str(SERVE_PORT),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server()
        shots = _resolve_placeholders(MANIFEST)
        n = _capture(shots)
        print(f"regenerate-ui-screenshots: done, {n} shot(s) written.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
