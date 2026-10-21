"""Full seal built on Devin's chosen #31 chevron-band background: shield removed and replaced with
a real state outline filled with MyBot's asscher-cut gem texture (green=ok, red=stale), same ring
kept as-is (green for ok, hue-shifted red for stale -- a perfectly matched pair, not two separate
AI generations). Real deployed size (116px)."""
import json
import os
import sys
from PIL import Image

abbr = sys.argv[1] if len(sys.argv) > 1 else "TX"
variant = sys.argv[2] if len(sys.argv) > 2 else "pass"  # pass | fail

bg_file = "seal31_bg_pass.png" if variant == "pass" else "seal31_bg_fail.png"
gem_file = f"enamel_{abbr}_{'green' if variant == 'pass' else 'red'}.png"

# Aspect read directly off the generated PNG rather than a hardcoded per-state table -- works
# for all 51 states/DC without needing an entry added per state.
_w, _h = Image.open(gem_file).size
aspect = _w / _h

# Ring inner edge sits at radius ~29 (viewBox units) around (50,50). Layout: arc text band at
# top (clear of the gold), state shape below it, date plate inlaid directly under that, date
# plate's own bottom fixed just above the gold ring.
MAXW, MAXH = 52, 39  # MAXW trimmed from 58 -- that was within 1 unit of the ring's own inner
# diameter (2*RING_R=59), so any state using the full width read as touching/crowding the ring
# (CO: "still too large" even after being pulled back to exactly 58). 52 leaves real clearance.
RING_R = 29.5  # just inside the measured ~30.57 true inner ring edge -- clip to this so any
# overflow below just crops naturally instead of colliding with the gold band.
MAX_CLIP_FRAC = 0.22  # cap how much width that clip is allowed to crop off -- past this, a very
# long/thin state (e.g. TN at 5.11:1) loses enough off both ends to misread as a shorter state
# entirely (confirmed: uncapped, TN rendered as a dead ringer for Nebraska).

MINH = 24  # floor: below this a wide/flat state reads as an abstract smear. Moderate-aspect
# states (CO 1.75:1, AR 1.42:1) fit fine within MAXW on their own and must NOT be stretched to
# claim the full MAXH -- that was tried and made CO "way too big," overhanging the ring. Only
# states that would otherwise fall below the floor get the width-overflow+clip treatment.
# Trimmed from 26 -- KS at 26 needed gw=64.5 (clipped hard by the ring), read as "a bit too big."

LOCKED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seal_locked_boxes.json")
with open(LOCKED_FILE, encoding="utf-8") as f:
    _locked = json.load(f)

# LOCKED, hard constants -- arc text and date plate never move and never resize, for any state.
# Only the state shape itself varies, and it always centers in the fixed gap between them.
date_h = 5.8
DATE_BOTTOM = 77.4   # fixed -- just above the gold ring
date_y0 = DATE_BOTTOM - date_h   # fixed -- 71.6, independent of any state's own gh
TOP = 32.0           # fixed -- top of the middle band, just below the arc text
date_w_frac = 0.48
date_w_base = 52.0   # fixed pill width -- independent of MAXW or any state's own gw

if abbr in _locked:
    # Devin already passed this state's exact box -- a shared-formula tweak made to fix some
    # OTHER state must never silently move it again. Use the recorded box verbatim.
    box = _locked[abbr]
    x0, y0, gw, gh = box["x0"], box["y0"], box["gw"], box["gh"]
else:
    gw = min(MAXW, MAXH * aspect)
    gh = gw / aspect
    if gh < MINH:
        gh = MINH
        gw = gh * aspect
        gw_cap = (2 * RING_R) / (1 - MAX_CLIP_FRAC)
        if gw > gw_cap:
            gw = gw_cap
            gh = gw / aspect
    x0 = 50 - gw / 2
    # State is the only variable element -- always centered in the fixed TOP..date_y0 band,
    # regardless of how gh was derived (natural fit or floor+clip). Centered on the TRUE band
    # (TOP to date_y0), not MAXH -- splitting slack against MAXH left a baked-in 0.6 asymmetry
    # (bigger gap below than above), which read as "still not centered."
    band_height = date_y0 - TOP
    y0 = TOP + (band_height - gh) / 2


def overlay_svg(uid, stale):
    arc_text = "RE-VERIFY NEEDED" if stale else "VERIFIED SOURCE"
    text_color = "#3a1416" if stale else "#163a24"
    return f'''<svg viewBox="0 0 100 100" style="position:absolute;inset:0;width:100%;height:100%;">
  <defs>
    <path id="sT{uid}" d="M 25.2 50 A 24.8 24.8 0 0 1 74.8 50" fill="none"/>
    <filter id="txt{uid}" x="-40%" y="-40%" width="180%" height="180%">
      <feDropShadow dx="0" dy="0.4" stdDeviation="0.5" flood-color="#000" flood-opacity="0.7"/>
    </filter>
    <clipPath id="ring{uid}"><circle cx="50" cy="50" r="{RING_R}"/></clipPath>
  </defs>
  <text font-family="Georgia, 'Times New Roman', serif" font-size="5.6" font-weight="700" fill="{text_color}" filter="url(#txt{uid})">
    <textPath href="#sT{uid}" startOffset="50%" text-anchor="middle" textLength="48" lengthAdjust="spacingAndGlyphs">{arc_text}</textPath>
  </text>

  <image href="{gem_file}" x="{x0}" y="{y0}" width="{gw}" height="{gh}" preserveAspectRatio="none" clip-path="url(#ring{uid})"/>

  <rect x="{50 - date_w_base * date_w_frac / 2}" y="{date_y0}" width="{date_w_base * date_w_frac}" height="{date_h}" rx="{date_h/2}" fill="{'#2a0c0e' if stale else '#0c2a1c'}" opacity="0.9" stroke="#e4c36e" stroke-width="0.4"/>
  <text x="50" y="{date_y0 + date_h/2 + 1.6}" font-family="Georgia, 'Times New Roman', serif" font-size="4.6" font-weight="700" fill="#fdfaf2"
    text-anchor="middle" textLength="{date_w_base*date_w_frac*0.82}" lengthAdjust="spacingAndGlyphs">OCT 20 2026</text>
</svg>'''


html = f'''<!doctype html>
<html><head><meta charset="utf-8"><style>
body {{ background:#fff; font-family: sans-serif; margin:0; }}
.darkbox {{ background:#12151a; }}
.row {{ display:flex; gap:2.5rem; flex-wrap:wrap; padding:2rem; align-items:flex-start; }}
.seal {{ width:7.25rem; height:7.25rem; position:relative; }}
.seal img.bg {{ width:100%; height:100%; display:block; border-radius:50%; }}
.label {{ color:#888; font-size:0.8rem; text-align:center; margin-top:0.5rem; }}
.col {{ display:flex; flex-direction:column; align-items:center; }}
</style></head><body>
<div class="row">
  <div class="col"><div class="seal"><img class="bg" src="{bg_file}">{overlay_svg("a", variant=="fail")}</div><div class="label">{abbr} {variant} -- real size, light</div></div>
</div>
<div class="row darkbox">
  <div class="col"><div class="seal"><img class="bg" src="{bg_file}">{overlay_svg("b", variant=="fail")}</div><div class="label" style="color:#aaa">{abbr} {variant} -- real size, dark</div></div>
</div>
</body></html>'''

out = f"seal31_test_{abbr}_{variant}.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("wrote", out, "gem box:", (x0, y0, gw, gh))
