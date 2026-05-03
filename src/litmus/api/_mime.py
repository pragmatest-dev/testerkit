"""Magic-byte content sniffer for `/api/runs/{id}/ref` responses.

When ``load_ref`` materializes a ``.bin`` payload, we get raw bytes
with no extension to drive ``Content-Type`` selection. ``imghdr`` was
removed in Python 3.13 and ``python-magic`` is a system dep we don't
want to require, so we hand-roll a small magic-byte table covering
the formats a hardware-test workflow realistically captures —
operator screenshots, scope photos, test recordings, calibration
certs, log files, exported HTML reports.

Anything we can't identify falls back to ``application/octet-stream``
so the browser offers a download.
"""

from __future__ import annotations


def sniff_mime(head: bytes) -> str:
    """Return a MIME type string for *head* (first ~16+ bytes of payload).

    Falls back to ``application/octet-stream`` for unknown content.
    """
    if not head:
        return "application/octet-stream"

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"

    # Text-leading formats: strip whitespace before checking, since YAML /
    # XML / HTML often have leading newlines or BOMs.
    stripped = head.lstrip()
    if stripped.startswith(b"<?xml") or stripped[:4].lower() == b"<svg":
        return "image/svg+xml"
    lowered = stripped.lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "text/html"

    if _looks_like_text(head):
        return "text/plain"
    return "application/octet-stream"


def _looks_like_text(head: bytes) -> bool:
    """Heuristic: decodes as UTF-8 and contains no NUL / control gore.

    Allows tab / LF / CR / form-feed; rejects other low control bytes
    and non-decodable sequences.
    """
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    allowed_controls = {"\t", "\n", "\r", "\x0b", "\x0c"}
    for ch in text:
        if ch < " " and ch not in allowed_controls:
            return False
    return True
