"""One-time preprocessing: downsize the 56-state seal source art (full-res AI
output, up to 1299x1054 / 1024x1024) to production web sizes before they ever
reach docs/. The locked box geometry in seal_locked_boxes.json is in SVG
viewBox units (0-100), not pixels, so resizing the underlying raster changes
nothing about the Devin-approved per-state placement -- only file weight.

Display size is a 7.25rem (116px) circular badge; sized here for up to 3x
device-pixel-ratio with headroom, not full source resolution. Run once,
output committed to assets/seal_states/web/ alongside the untouched
originals (source_paths/, the full enamel/ art, build_seal31_full.py stay as
the reference + regeneration path if the art is ever redone).
"""
import json
import pathlib

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "web"
OUT.mkdir(exist_ok=True)

BG_MAX = 360  # background ring, shared across every state page
ENAMEL_MAX = 400  # per-state gem fill, longest edge

locked = json.loads((HERE / "seal_locked_boxes.json").read_text(encoding="utf-8"))
abbrs = sorted(locked.keys())
print(f"{len(abbrs)} locked states")


def resize_to(src: pathlib.Path, dst: pathlib.Path, max_edge: int) -> None:
    im = Image.open(src)
    w, h = im.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    im.save(dst, optimize=True)


for bg in ("seal31_bg_pass.png", "seal31_bg_fail.png"):
    resize_to(HERE / bg, OUT / bg, BG_MAX)
    print("wrote", bg, Image.open(OUT / bg).size)

missing = []
for abbr in abbrs:
    for color in ("green", "red"):
        name = f"enamel_{abbr}_{color}.png"
        src = HERE / "enamel" / name
        if not src.exists():
            missing.append(name)
            continue
        resize_to(src, OUT / name, ENAMEL_MAX)

if missing:
    raise SystemExit(f"MISSING source art for locked states: {missing}")

print(f"wrote {len(abbrs) * 2} enamel files to {OUT}")
total = sum(f.stat().st_size for f in OUT.iterdir())
print(f"total web/ size: {total / 1_000_000:.2f} MB")
