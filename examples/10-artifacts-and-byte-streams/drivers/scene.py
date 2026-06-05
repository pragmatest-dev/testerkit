"""Synthesized DUT scene snapshot — PIL image with text + a marker.

Real benches replace this with a real camera or capture card. The
shape that matters: ``snapshot_dut()`` returns a ``PIL.Image.Image``
that the test layer ``observe()``s into FileStore as a PNG.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from PIL import Image, ImageDraw

_W, _H = 640, 360


def snapshot_dut(serial: str = "SN-DEMO-001") -> Image.Image:
    """Return one synthesized DUT photograph as a PIL Image.

    Renders a dark background with a fake DUT outline, a "LED status"
    dot whose color jitters between green / yellow / red on each call,
    a per-call timestamp, and the DUT serial.
    """
    img = Image.new("RGB", (_W, _H), color=(20, 22, 28))
    draw = ImageDraw.Draw(img)

    # DUT outline
    draw.rectangle((140, 90, 500, 270), outline=(120, 130, 140), width=2)
    draw.rectangle((180, 110, 460, 200), fill=(40, 44, 52), outline=(80, 90, 100))

    # LED status indicator (color jitters slightly per call)
    led_colors = [(80, 220, 100), (240, 200, 60), (235, 80, 80)]
    led_weights = [0.7, 0.2, 0.1]  # mostly green, sometimes yellow, rare red
    led = random.choices(led_colors, weights=led_weights)[0]
    draw.ellipse((430, 130, 450, 150), fill=led, outline=(200, 200, 210))

    # Labels
    draw.text((150, 75), f"DUT  {serial}", fill=(200, 210, 220))
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    draw.text((150, 285), ts, fill=(160, 170, 180))
    draw.text((150, 305), "5V rail • idle • mock capture", fill=(120, 130, 140))

    return img
