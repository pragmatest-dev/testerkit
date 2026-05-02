"""Magic-byte sniffer coverage for known browser-renderable formats."""

from __future__ import annotations

import pytest

from litmus.api._mime import sniff_mime


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        # Empty / unknown
        (b"", "application/octet-stream"),
        (b"\x00\x01\x02\x03\x04", "application/octet-stream"),
        # Images
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01", "image/jpeg"),
        (b"\xff\xd8\xff\xdb\x00\x84", "image/jpeg"),
        (b"GIF87a\x01\x00\x01\x00", "image/gif"),
        (b"GIF89a\x01\x00\x01\x00", "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        # Documents / video
        (b"%PDF-1.4\n%\xc7\xec", "application/pdf"),
        (b"\x00\x00\x00\x18ftypmp42", "video/mp4"),
        (b"\x00\x00\x00 ftypisom", "video/mp4"),
        (b"\x1a\x45\xdf\xa3\x9fB\x86\x81", "video/webm"),
        # Markup / text
        (b'<?xml version="1.0"?><svg xmlns', "image/svg+xml"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'>", "image/svg+xml"),
        (b"  <SVG width='10' height='10'>", "image/svg+xml"),
        (b"<!doctype html><html>", "text/html"),
        (b"<HTML><body>", "text/html"),
        (b"\n  <!DOCTYPE HTML>\n", "text/html"),
        (b"hello world\n", "text/plain"),
        (b"line1\nline2\tcol\r\n", "text/plain"),
        # JSON-shaped text still falls under text/plain (callers serving
        # known-JSON go through the typed branch; this is the .bin fallback)
        (b'{"key": "value"}', "text/plain"),
        # Binary blobs without recognized magic
        (b"\x00binary data with NUL", "application/octet-stream"),
        (b"\x01\x02\x03\x04\x05random", "application/octet-stream"),
    ],
)
def test_sniff_mime(head: bytes, expected: str) -> None:
    assert sniff_mime(head) == expected
