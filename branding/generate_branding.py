"""Regenerate the TesterKit brand assets into this branding/ folder.

    uv run --with fonttools --with cairosvg python branding/generate_branding.py

Self-contained (Selawik Bold, OFL, in ./selawik/). Part of the PragmaTest family
(see DESIGN-SPEC.md) — LVKit / TesterKit / PragmaTest share one design language.

To tweak: edit `mark()` below (path coordinates only — NEVER non-uniformly scale
a mark, it distorts the constant stroke-width of 5) and/or the palette, then
re-run. All marks live on a 64 artboard, ink rectangle x[5,59] × y[8,56].
"""

from pathlib import Path

import cairosvg
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent

# ---- brand ------------------------------------------------------------------
SLUG, WORD, SPLIT = "testerkit", "TesterKit", 6  # SPLIT: neutral [:6] + accent [6:]
SLATE, CREAM, ACCENT = "#2b3038", "#faf7f2", "#16a34a"
STROKE = 'fill="none" stroke="{c}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"'


def mark(c):
    """TesterKit: a measurement trace held between two spec-limit rails.
    Rails span the box edges; trace amplitude (locked "C") keeps its endpoints
    ~24% in from each rail. EDIT path coords to tweak — keep stroke-width 5."""
    return (
        f'<path d="M7.5 10.5 H56.5 M7.5 53.5 H56.5" {STROKE.format(c=c)}/>'
        f'<path d="M8.5 43 Q19.3 17.1 32 31 Q44.7 44.7 55.5 21" {STROKE.format(c=c)}/>'
    )


# ---- wordmark type (Selawik Bold, outlined to paths) ------------------------
font = TTFont(HERE / "selawik" / "selawkb.ttf")
cap = getattr(font["OS/2"], "sCapHeight", 0) or int(font["head"].unitsPerEm * 0.7)
cmap, gs = font.getBestCmap(), font.getGlyphSet()
S = 30.0 / cap  # cap height -> 30 units
TRACK_U = -1.5 / S
BY = 42.0
CLEAR = 0.4 * 30.0  # 12-unit clear space, all sides
MS = 31.68 / 48.0  # mark placed ink height 31.68 (mark box is 48 tall)
MTX, MTY = 5.6 - 5 * MS, 27.04 - 32 * MS
TX = (MTX + 59 * MS) + 5.5  # mark-right + gap -> text start


def _glyph(ch):
    pen = SVGPathPen(gs)
    gs[cmap[ord(ch)]].draw(pen)
    return pen.getCommands(), gs[cmap[ord(ch)]].width


def _gb(ch):
    pen = BoundsPen(gs)
    gs[cmap[ord(ch)]].draw(pen)
    return pen.bounds


def wordmark(neutral):
    x, segs = 0.0, []
    for ch in WORD:
        d, w = _glyph(ch)
        segs.append((ch, d, x))
        x += w + TRACK_U
    markg = f'<g transform="translate({MTX:.3f},{MTY:.3f}) scale({MS:.5f})">{mark(ACCENT)}</g>'

    def grp(which, color):
        p = "".join(
            f'<path d="{d}" transform="translate({xo:.1f},0)"/>'
            for i, (ch, d, xo) in enumerate(segs)
            if (i >= SPLIT) == (which == "accent")
        )
        return f'<g fill="{color}">{p}</g>'

    textg = (
        f'<g transform="translate({TX:.2f},{BY}) scale({S:.5f},{-S:.5f})">'
        f"{grp('neutral', neutral)}{grp('accent', ACCENT)}</g>"
    )
    gb = [(_gb(ch), xo) for ch, d, xo in segs if _gb(ch)]
    il = min(TX + (xo + b[0]) * S for b, xo in gb)
    ir = max(TX + (xo + b[2]) * S for b, xo in gb)
    it = min(BY - b[3] * S for b, xo in gb)
    ib = max(BY - b[1] * S for b, xo in gb)
    ml, mr, mt, mb = MTX + 5 * MS, MTX + 59 * MS, MTY + 8 * MS, MTY + 56 * MS
    cl, cr, ct, cb = min(ml, il), max(mr, ir), min(mt, it), max(mb, ib)
    vbx, vby = cl - CLEAR, ct - CLEAR
    vbw, vbh = (cr - cl) + 2 * CLEAR, (cb - ct) + 2 * CLEAR
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vbx:.2f} {vby:.2f} '
        f'{vbw:.2f} {vbh:.2f}" width="{round(vbw)}" height="{round(vbh)}" '
        f'role="img" aria-label="{WORD}">{markg}{textg}</svg>\n'
    )
    return svg, vbw


def mark_svg():
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" '
        f'height="64" role="img" aria-label="{SLUG}">{mark(ACCENT)}</svg>\n'
    )


def tile_svg():
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" '
        f'height="128"><rect width="128" height="128" rx="28" fill="{SLATE}"/>'
        f'<g transform="translate(16,16) scale(1.5)">{mark(ACCENT)}</g></svg>\n'
    )


def out(name, data, mode="w"):
    with open(HERE / name, mode) as f:
        f.write(data)


PPU = 7
msvg, tsvg = mark_svg(), tile_svg()
out(f"{SLUG}-mark.svg", msvg)
out(f"{SLUG}-icon-tile.svg", tsvg)
cairosvg.svg2png(
    bytestring=msvg.encode(), output_width=256, write_to=str(HERE / f"{SLUG}-mark-256.png")
)
for sz in (128, 256):
    cairosvg.svg2png(
        bytestring=tsvg.encode(),
        output_width=sz,
        output_height=sz,
        write_to=str(HERE / f"{SLUG}-icon-{sz}.png"),
    )
for sz in (16, 32, 48):
    cairosvg.svg2png(
        bytestring=msvg.encode(), output_width=sz, write_to=str(HERE / f"favicon-{sz}.png")
    )

sl, vbw = wordmark(SLATE)  # slate text -> light ground
sd, _ = wordmark(CREAM)  # cream text -> dark ground
pngw = round(vbw * PPU)
out(f"{SLUG}-wordmark-light.svg", sl)
out(f"{SLUG}-wordmark-dark.svg", sd)
out(f"{SLUG}-wordmark.svg", sd)
cairosvg.svg2png(
    bytestring=sl.encode(),
    output_width=pngw,
    background_color=CREAM,
    write_to=str(HERE / f"{SLUG}-wordmark-light.png"),
)
cairosvg.svg2png(
    bytestring=sd.encode(),
    output_width=pngw,
    background_color=SLATE,
    write_to=str(HERE / f"{SLUG}-wordmark-dark.png"),
)
cairosvg.svg2png(
    bytestring=sd.encode(), output_width=pngw, write_to=str(HERE / f"{SLUG}-wordmark.png")
)
print(f"regenerated {WORD} branding -> {HERE}")
