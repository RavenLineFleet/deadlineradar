"""Regenerate assets/og-image.png (1200x630 Open Graph / Twitter card).

Oct-1 readiness sweep (2026-09-02): the shipped image carried the pre-rename
"DeadlineRadar" wordmark and a tagline claiming a codified rule "for every
state" -- six records (FL firm, IL firm, CO x2, ME, AK individual) are
sourced to the regulator's operational records instead, and the site itself
labels that distinction ("Source of record (official records, not codified
rule text)"). The card is the first thing a firm sees when a link is pasted
into Slack/Teams/LinkedIn, so it has to meet the same honesty bar as the
pages. No generator existed before; this is it. Re-run with
`python scripts/make_og_image.py` from the deadlineradar root, then
`python generate.py` copies it to docs/.

Fonts: the site's own Fraunces ships only as a variable woff2 (Pillow can't
load it), so this uses Windows' Georgia Bold (a close serif cousin) for the
display lines and Segoe UI for the tagline. Palette is copied from the
page CSS: ink #1f3d54, ground #f7f9fb, accent #8a6a33, ring #c8d2db.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "og-image.png"
FONTS = Path(r"C:\Windows\Fonts")

W, H = 1200, 630
INK = (31, 61, 84)
GROUND = (247, 249, 251)
ACCENT = (138, 106, 51)
RING = (200, 210, 219)
MUTED = (78, 92, 105)
TEXT = (17, 24, 39)

WORDMARK = "Deadline-Radar"
HEADLINE = ["CPA license renewal deadlines,", "verified and kept current."]
# Mirrors the site's own phrasing (generate.py, "a board page, plus the
# codified rule itself where we could confirm it"). Never "every state".
TAGLINE = [
    "Sourced to the state board's own records, plus the codified",
    "rule wherever we could confirm it \u2014 not a guess, never a placeholder.",
]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def draw_logo(draw: ImageDraw.ImageDraw, cx: int, cy: int, scale: float) -> None:
    """The header's 32x32 radar mark (docs/index.html inline SVG), scaled."""
    r_outer, r_inner, r_dot = 13.5 * scale, 8 * scale, 2.3 * scale
    draw.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], outline=INK, width=max(2, int(1.6 * scale)))
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], outline=RING, width=max(1, int(1.2 * scale)))
    draw.ellipse([cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot], fill=ACCENT)
    # sweep hand: (16,16) -> (26,9) in the 32-unit SVG box
    draw.line([(cx, cy), (cx + 10 * scale, cy - 7 * scale)], fill=ACCENT, width=max(2, int(1.8 * scale)))
    # 12 o'clock tick: (16,3.5) -> (16,6)
    draw.line([(cx, cy - 12.5 * scale), (cx, cy - 10 * scale)], fill=INK, width=max(2, int(1.6 * scale)))


def main() -> None:
    im = Image.new("RGB", (W, H), GROUND)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 12, H], fill=INK)

    x = 100
    draw_logo(d, x + 20, 130, 1.5)
    d.text((x + 56, 106), WORDMARK, font=font("georgiab.ttf", 44), fill=TEXT)

    head = font("georgiab.ttf", 58)
    y = 210
    for line in HEADLINE:
        d.text((x, y), line, font=head, fill=TEXT)
        y += 70

    tag = font("segoeui.ttf", 25)
    y = 372
    for line in TAGLINE:
        d.text((x, y), line, font=tag, fill=MUTED)
        y += 36

    im.save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} {im.size}")


if __name__ == "__main__":
    main()
