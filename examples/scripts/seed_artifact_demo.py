"""Seed a Litmus run with one of every viewable artifact type.

Usage:

    uv run python examples/scripts/seed_artifact_demo.py

Then start the operator UI and click through to the seeded run:

    uv run litmus serve

In the browser:
    Runs → most recent run ("Artifact Viewing Demo") → Measurements tab
    → Artifacts section. Each row has a "View ..." button that opens
    the captured artifact in a NiceGUI dialog.

What the script writes:

* ``scope_capture`` — a 4-cycle 100 Hz sine wave as a ``Waveform``;
  served as JSON, rendered by ECharts in a line plot.
* ``dut_photo`` — a 1×1 red PNG; magic-byte sniffed to ``image/png``,
  rendered inline via ``<img>``.
* ``schematic`` — an SVG drawn inline; served as ``image/svg+xml``.
* ``calibration_cert`` — a minimal valid PDF saying "Hello from
  Litmus"; served as ``application/pdf`` for the browser PDF reader.
* ``debug_log`` — a few lines of UTF-8 text; served as
  ``text/plain``.

This bypasses the pytest plugin so we can populate
``vector.observations`` directly — the plain ``context`` fixture
returns a detached Context, which is fine for simple tests but
doesn't snapshot back to the parquet writer.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import uuid4

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import (
    DUT,
    Measurement,
    Outcome,
    TestRun,
    TestStep,
    TestVector,
    Waveform,
)

# --- Demo artifacts -----------------------------------------------------------

# Minimal valid 1×1 red PNG (67 bytes).
PNG_1X1_RED: bytes = bytes.fromhex(
    "89504e470d0a1a0a"
    "0000000d49484452"
    "0000000100000001"
    "08060000001f15c489"
    "0000000d49444154"
    "789c63fcffff3f00050000ffff"
    "0000000049454e44ae426082"
)

SVG_DEMO: bytes = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 100">
  <rect width="240" height="100" fill="#0f172a"/>
  <circle cx="50" cy="50" r="32" fill="#22d3ee"/>
  <text x="100" y="58" fill="white" font-family="sans-serif" font-size="22">
    Litmus
  </text>
</svg>"""

PDF_DEMO: bytes = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]"
    b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\n"
    b"BT /F1 16 Tf 30 50 Td (Hello from Litmus) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000054 00000 n \n"
    b"0000000101 00000 n \n"
    b"0000000200 00000 n \n"
    b"0000000253 00000 n \n"
    b"trailer<</Size 6/Root 1 0 R>>\n"
    b"startxref\n"
    b"313\n"
    b"%%EOF\n"
)

LOG_TEXT: bytes = b"""\
[12:00:00] PSU output enabled at 5.0 V
[12:00:00] DMM measuring DC voltage on rail
[12:00:01] sample 0: 3.298 V
[12:00:01] sample 1: 3.301 V
[12:00:01] sample 2: 3.304 V
[12:00:01] mean=3.301 V, sigma=0.003 V
[12:00:02] PSU output disabled
"""


def _sine_waveform(freq_hz: float = 100.0, sample_rate_hz: float = 100_000.0) -> Waveform:
    dt = 1.0 / sample_rate_hz
    n = int(sample_rate_hz / freq_hz) * 4  # four cycles
    omega = 2 * math.pi * freq_hz
    return Waveform(
        t0=0.0,
        dt=dt,
        Y=[math.sin(omega * i * dt) for i in range(n)],
        attrs={"units": "V", "channel": "scope1"},
    )


def main() -> None:
    from litmus.data.data_dir import resolve_data_dir

    # Writer convention: ``runs/`` lives under the project / platformdirs
    # results dir; the read side (``ParquetBackend`` used by ``litmus
    # serve``) constructs ``RunStore`` which itself appends ``runs/``.
    # Match the existing writer call sites in
    # ``output_runner.py`` and ``client.py``.
    runs_dir = resolve_data_dir(None) / "runs"
    backend = ParquetBackend(data_dir=runs_dir)

    started = datetime.now(UTC)
    run = TestRun(
        id=uuid4(),
        started_at=started,
        ended_at=started,
        dut=DUT(serial="DEMO-DUT-001", part_number="ART-VIEW", revision="A"),
        product_id=None,
        product_name="Artifact Viewing Demo",
        operator_id="demo",
        test_phase="development",
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name="capture_artifacts",
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        observations={
                            "scope_capture": _sine_waveform(),
                            "dut_photo": PNG_1X1_RED,
                            "schematic": SVG_DEMO,
                            "calibration_cert": PDF_DEMO,
                            "debug_log": LOG_TEXT,
                        },
                        measurements=[
                            Measurement(
                                name="rail_voltage",
                                value=3.301,
                                units="V",
                                limit_low=3.0,
                                limit_high=3.6,
                                outcome=Outcome.PASSED,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    parquet_path = backend.save_test_run(run)
    print(f"Wrote run: {run.id}")
    print(f"Parquet: {parquet_path}")
    print(f"Sidecar: {parquet_path.parent / (parquet_path.stem + '_ref')}")
    print()
    print("Next:")
    print("  uv run litmus serve")
    print(f"  Open http://localhost:8000/results/{run.id}")
    print("  Click the Measurements tab → 'View ...' buttons under Artifacts")


if __name__ == "__main__":
    main()
