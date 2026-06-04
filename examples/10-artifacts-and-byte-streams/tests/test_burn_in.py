"""Burn-in test — multiple artifact types around one DUT cycle.

Demonstrates the full FileStore use case in v0.2.0:

* **PIL.Image** — a captured photo of the DUT, observe'd at start of
  test. Routes to FileStore via the PIL serializer; lands as PNG;
  ``out_dut_photo`` carries ``file://...`` URI; the operator UI's
  artifact viewer renders it inline.
* **Pydantic model** — a structured burn-in report observed at end
  of test. Routes to FileStore via the BaseModel serializer; lands
  as JSON.
* **bytes** — a vendor capture file (here just synthesized bytes
  with a TDMS-like header). Routes via the bytes serializer; lands
  as ``.bin``.
* **JSONL byte stream** — a streaming event log opened via
  ``files.stream(name, format="jsonl")``. One line per event;
  ``StreamStarted`` + ``StreamEnded`` lifecycle events bracket the
  capture; ``out_burn_log`` on the verify row carries the final
  ``file://...`` URI.

Plus a verify on the rail voltage — same vector links the verify
row to every artifact above via ``out_*`` columns.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from drivers import snapshot_dut
from pydantic import BaseModel

import litmus.files
from litmus import Limit


class BurnInReport(BaseModel):
    """Final burn-in summary — lands in FileStore as JSON."""

    dut_serial: str
    duration_s: float
    started_at: datetime
    ended_at: datetime
    rail_v_min: float
    rail_v_max: float
    notes: str


def test_dut_burn_in(observe, verify, psu) -> None:
    started = datetime.now(UTC)

    # Capture a "before" photo of the DUT — PIL.Image routes to
    # FileStore via the PIL serializer (lands as .png).
    observe("dut_photo", snapshot_dut(serial="SN-DEMO-001"))

    # Attach a vendor capture artifact — raw bytes route via the
    # bytes serializer (lands as .bin). Synthesized here; in a real
    # bench this would be a TDMS or vendor-proprietary file.
    vendor_blob = b"TDMS\x00\x00\x00" + b"\x00" * 240 + b"<synthesized capture payload>"
    observe("vendor_capture", vendor_blob)

    # Open a streaming JSONL log for the run — every operational
    # event the test wants to record, one JSON object per line.
    # files.stream(format="jsonl") returns a sink whose .write()
    # accepts JSON-serializable values directly.
    with litmus.files.stream("burn_log", format="jsonl") as log:
        log.write({"ts": datetime.now(UTC).isoformat(), "event": "psu_on", "voltage_set": 5.0})
        psu.set_voltage(5.0)

        readings: list[float] = []
        for cycle in range(5):
            v = psu.measure_voltage()
            i = psu.measure_current()
            readings.append(v)
            log.write(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "sample",
                    "cycle": cycle,
                    "voltage": v,
                    "current": i,
                }
            )
            time.sleep(0.05)

        log.write({"ts": datetime.now(UTC).isoformat(), "event": "psu_off"})

    psu.set_voltage(0.0)

    # Final report — Pydantic model routes via the BaseModel
    # serializer (lands as .json).
    ended = datetime.now(UTC)
    report = BurnInReport(
        dut_serial="SN-DEMO-001",
        duration_s=(ended - started).total_seconds(),
        started_at=started,
        ended_at=ended,
        rail_v_min=min(readings),
        rail_v_max=max(readings),
        notes="Mock burn-in cycle — synthesized readings.",
    )
    observe("burn_report", report)

    # The judgment: rail voltage stayed within ±5 % of 5.0 V across
    # all samples. ``out_dut_photo`` / ``out_vendor_capture`` /
    # ``out_burn_log`` / ``out_burn_report`` all land on this row;
    # one click takes the analyst from the verify row to any
    # artifact.
    rail_mean = sum(readings) / len(readings)
    verify("rail_v_mean", rail_mean, Limit(low=4.75, high=5.25, units="V"))
