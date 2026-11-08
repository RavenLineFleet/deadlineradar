"""A11Y-38: the firm-dashboard tablist has one tab stop and arrow-key traversal.

Runs the real drWireTabKeys/drSwitchView source from generate.py in node
against a stub DOM, so a handler that is present but wrong still fails.

    python -m pytest scripts/test_a11y38_tablist_keyboard.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import generate  # noqa: E402

GENERATE_PY = os.path.join(os.path.dirname(HERE), "generate.py")
VIEWS = ["roster", "calendar", "map", "cpe", "reports", "account"]


def _js_function(src, name):
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _harness(body):
    with open(GENERATE_PY, encoding="utf-8") as f:
        src = f.read()
    return (
        "var focused=null;var switched=[];var drMarkOnboardingVisit=function(){};\n"
        "function el(view,tab){var a={attrs:{},hidden:false,id:'',listeners:{},classList:{toggle:function(){}},"
        "setAttribute:function(k,v){this.attrs[k]=v},getAttribute:function(k){return k==='data-view'?view:this.attrs[k]},"
        "addEventListener:function(t,f){this.listeners[t]=f},focus:function(){focused=this}};a.view=view;return a}\n"
        f"var VIEWS={json.dumps(VIEWS)};\n"
        "var tabs=VIEWS.map(function(v){return el(v)});\n"
        "var panels=VIEWS.map(function(v){var p=el(v);p.id='dr-view-'+v;return p});\n"
        "var document={querySelectorAll:function(s){if(s==='.dr-view')return panels;return tabs}};\n"
        + _js_function(src, "drSwitchView")
        + _js_function(src, "drWireTabKeys")
        + "drWireTabKeys();\n"
        "function press(i,key,mods){var p=false;var e=Object.assign({key:key,preventDefault:function(){p=true}},mods||{});"
        "focused=null;tabs[i].listeners.keydown(e);return {to:focused?focused.view:null,prevented:p}}\n"
        + body
    )


def _run(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    out = subprocess.run([node, "-e", _harness(body)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    "i,key,expected",
    [
        (0, "ArrowDown", "calendar"),
        (0, "ArrowRight", "calendar"),
        (1, "ArrowUp", "roster"),
        (1, "ArrowLeft", "roster"),
        (5, "ArrowDown", "roster"),
        (0, "ArrowUp", "account"),
        (3, "Home", "roster"),
        (3, "End", "account"),
    ],
)
def test_traversal_moves_focus_without_switching_view(i, key, expected):
    got = _run(
        f"var r=press({i},{json.dumps(key)});"
        "console.log(JSON.stringify({to:r.to,prevented:r.prevented,hiddenChanged:panels.some(function(p){return p.hidden})}))"
    )
    assert got == {"to": expected, "prevented": True, "hiddenChanged": False}


def test_space_opens_the_focused_tab_and_rover_follows():
    got = _run(
        "var r=press(2,' ');"
        "console.log(JSON.stringify({prevented:r.prevented,"
        "tabindex:tabs.map(function(t){return t.attrs.tabindex}),"
        "visible:panels.filter(function(p){return !p.hidden}).map(function(p){return p.view})}))"
    )
    assert got == {
        "prevented": True,
        "tabindex": ["-1", "-1", "0", "-1", "-1", "-1"],
        "visible": ["map"],
    }


@pytest.mark.parametrize("key,mods", [("ArrowLeft", {"altKey": True}), ("ArrowDown", {"shiftKey": True}), ("Tab", {}), ("a", {})])
def test_other_keys_are_left_to_the_browser(key, mods):
    got = _run(f"console.log(JSON.stringify(press(1,{json.dumps(key)},{json.dumps(mods)})))")
    assert got == {"to": None, "prevented": False}


def test_rendered_sidebar_has_exactly_one_tab_stop_on_the_active_tab():
    html = generate._dashboard_sidebar_html("roster", tabs_live_here=True)
    tabs = re.findall(r'<a [^>]*role="tab"[^>]*>', html)
    assert len(tabs) == len(VIEWS)
    stops = [t for t in tabs if 'tabindex="0"' in t]
    assert len(stops) == 1 and 'id="dr-tab-roster"' in stops[0]
    assert sum('tabindex="-1"' in t for t in tabs) == len(VIEWS) - 1
    assert 'aria-orientation="vertical"' in html


def test_non_tab_sidebar_links_stay_in_the_tab_order():
    html = generate._dashboard_sidebar_html("mobility", tabs_live_here=False)
    assert "tabindex" not in html
