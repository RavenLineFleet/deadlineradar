"""TABLE-5: a build-time birth-month table cell that has passed rolls forward at view time.

Runs the real _TABLE_ROLL_JS and the calculator's rollCell from generate.py in node against a
stub DOM with a frozen clock, so a script that is present but wrong still fails.

    python -m pytest scripts/test_table5_view_time_roll.py -q
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

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

FROZEN_CLOCK = """
var RealDate = Date;
function FakeDate() {
  if (arguments.length === 0) return new RealDate(FROZEN[0], FROZEN[1], FROZEN[2], 9, 0, 0);
  var a = Array.prototype.slice.call(arguments);
  return new (Function.prototype.bind.apply(RealDate, [null].concat(a)))();
}
FakeDate.UTC = RealDate.UTC; FakeDate.prototype = RealDate.prototype;
Date = FakeDate;
"""


def _run(js):
    out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _roll_table(frozen, period, cells):
    script = re.sub(r"^<script>|</script>$", "", generate._TABLE_ROLL_JS.strip())
    stub = f"""
var FROZEN = {json.dumps(frozen)};
{FROZEN_CLOCK}
var tds = {json.dumps(cells)}.map(function(t) {{ return {{textContent: t}}; }});
var table = {{ getAttribute: function() {{ return {json.dumps(str(period))}; }},
               querySelectorAll: function() {{ return tds; }} }};
var document = {{ querySelectorAll: function() {{ return [table]; }} }};
{script}
console.log(JSON.stringify(tds.map(function(t) {{ return t.textContent; }})));
"""
    return _run(stub)


def test_elapsed_cell_rolls_one_cycle():
    # 2026-12-01 local: arizona's even-year November cell has passed.
    got = _roll_table([2026, 11, 1], 2, ["November", "November 30, 2027", "November 30, 2026"])
    assert got == ["November", "November 30, 2027", "November 30, 2028"]


def test_due_today_does_not_roll():
    got = _roll_table([2026, 10, 30], 2, ["November 30, 2026"])
    assert got == ["November 30, 2026"]


def test_annual_and_month_end_leap():
    got = _roll_table([2027, 2, 1], 1, ["February 28, 2027", "March 31, 2027"])
    assert got == ["February 29, 2028", "March 31, 2027"]


def test_fixed_day_parity_cell_keeps_day():
    got = _roll_table([2027, 6, 16], 2, ["Odd", "July 15, 2027"])
    assert got == ["Odd", "July 15, 2029"]


def _calc_roll(frozen, iso, display, period):
    src = generate._DEADLINE_CALC_JS
    start = src.index("  function fillTpl(")
    end = src.index("\n  }\n", src.index("  function rollCell(")) + 5
    stub = f"""
var FROZEN = {json.dumps(frozen)};
{FROZEN_CLOCK}
var DATE_PARTS = ["{{month}} {{day}}, {{year}}", "January","February","March","April","May","June","July",
                  "August","September","October","November","December"];
var MONTHS = DATE_PARTS.slice(1);
var I18N = {{next_due: "{{label}} {{date}}"}};
{src[start:end]}
console.log(JSON.stringify(rollCell({json.dumps(iso)}, {json.dumps(display)}, {period})));
"""
    return _run(stub)


def test_calc_rolls_passed_cell():
    assert _calc_roll([2026, 11, 1], "2026-11-30", "November 30, 2026", 1) == "November 30, 2027"
    assert _calc_roll([2026, 11, 1], "2026-11-30", "November 30, 2026", 2) == "November 30, 2028"


def test_calc_keeps_future_display_verbatim():
    assert _calc_roll([2026, 10, 24], "2026-11-30", "30 de noviembre de 2026", 2) == "30 de noviembre de 2026"


def test_every_table_renderer_opts_in():
    src = open(os.path.join(os.path.dirname(HERE), "generate.py"), encoding="utf-8").read()
    assert src.count('<table data-dr-roll="') == 5
    assert src.count("{_TABLE_ROLL_JS}") == 5
