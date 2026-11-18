"""A11Y-42: state-search listbox options are not tab stops.

The combobox uses aria-activedescendant, so DOM focus must stay on the input.
Runs the real drRenderDropdown source from generate.py in node against a stub
DOM and checks every rendered role="option" carries tabindex="-1".

    python -m pytest scripts/test_a11y42_search_options_not_tab_stops.py -q
"""
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATE_PY = os.path.join(os.path.dirname(HERE), "generate.py")


def _js_function(src, name):
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _render(n):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    with open(GENERATE_PY, encoding="utf-8") as f:
        src = f.read()
    js = (
        "var attrs={};var input={value:'a',setAttribute:function(k,v){attrs[k]=v},"
        "removeAttribute:function(k){delete attrs[k]}};\n"
        "var dropdown={innerHTML:'',classList:{add:function(){},remove:function(){}}};\n"
        "var document={getElementById:function(id){return id==='state-search-input'?input:dropdown}};\n"
        f"var N={n};function drMatches(){{var r=[];for(var i=0;i<N;i++)r.push({{slug:'s'+i,name:'S'+i}});return r}}\n"
        "var drActiveIndex=-1;\n"
        + _js_function(src, "drCloseDropdown")
        + _js_function(src, "drRenderDropdown")
        + "drRenderDropdown();process.stdout.write(JSON.stringify(dropdown.innerHTML));\n"
    )
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("n", [1, 40])
def test_every_option_is_out_of_tab_order(n):
    html = _render(n)
    options = re.findall(r"<button\b[^>]*>", html)
    assert len(options) == n
    for tag in options:
        assert 'role="option"' in tag
        assert 'tabindex="-1"' in tag, tag
