"""The Annual/Monthly toggle must actually disappear when JS sets .hidden = true.

`.dr-billing-interval-toggle { display: flex }` outranks the UA `[hidden]` rule, so
toggleEl.hidden = true (pricing page, after an already_subscribed refusal) did nothing.

    python -m pytest scripts/test_interval_toggle_hidden.py -q
"""
import os
import re
from pathlib import Path

REPO = Path(os.path.dirname(os.path.abspath(__file__))).parent
GEN = (REPO / "generate.py").read_text(encoding="utf-8")
HIDDEN_RULE = re.compile(r"\.dr-billing-interval-toggle\[hidden\]\s*\{\s*display:\s*none\s*;?\s*\}")



def test_generate_py_has_hidden_override_after_flex_rule():
    flex = GEN.index(".dr-billing-interval-toggle { display: flex")
    m = HIDDEN_RULE.search(GEN)
    assert m, "missing .dr-billing-interval-toggle[hidden] { display: none }"
    assert m.start() > flex, "[hidden] override must come after the display:flex rule"


def test_built_stylesheet_ships_the_override_and_pricing_links_it():
    css = (REPO / "docs" / "styles.css").read_text(encoding="utf-8")
    assert HIDDEN_RULE.search(css), "docs/styles.css lacks the [hidden] override (regen docs)"
    page = (REPO / "docs" / "pricing" / "index.html").read_text(encoding="utf-8")
    assert "/styles.css?v=" in page and "dr-pricing-interval-toggle" in page
