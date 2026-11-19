"""Tests for GATE-69 (_strip_ts_comments defined twice; the live copy erased
code after an in-string URL) and GATE-68 (a comment naming
requireSendApproval() satisfied check_send_pass_consent_gate_coverage with
the real call deleted). AuditLab/SecurityLab, 2026-10-24.

    python -m pytest scripts/test_ts_comment_strip_gate.py -q
"""
import inspect
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REPO = Path(HERE).parent


def test_strip_ts_comments_is_defined_once():
    src = Path(inspect.getsourcefile(gate)).read_text(encoding="utf-8")
    assert src.count("\ndef _strip_ts_comments(") == 1


def test_in_string_url_does_not_erase_the_call():
    probe = 'const u = "https://x/p"; await store.write(x);'
    assert gate._strip_ts_comments(probe) == probe


def test_comments_removed_strings_kept():
    out = gate._strip_ts_comments('a(); // store.write(x)\nb(); /* c() */ d("//x");')
    assert "store.write" not in out and "c()" not in out
    assert 'd("//x")' in out and "a();" in out and "b();" in out


def test_mutating_get_extraction_still_sees_sql_in_strings():
    fns = gate._extract_top_level_ts_functions(
        'export async function f(db) {\n  // UPDATE x SET y\n  await db.prepare("INSERT INTO t VALUES (1)").run();\n}\n'
    )
    assert gate._MUTATING_DML_RE.search(fns["f"])


def _consent_fixture(tmp_path, transform):
    src_dir = tmp_path / "worker" / "src"
    src_dir.mkdir(parents=True)
    shutil.copy(REPO / "worker" / "src" / "index.ts", src_dir / "index.ts")
    sched = (REPO / "worker" / "src" / "scheduler.ts").read_text(encoding="utf-8")
    (src_dir / "scheduler.ts").write_text(transform(sched), encoding="utf-8")
    return gate.check_send_pass_consent_gate_coverage(tmp_path)


def _parity_body_span(sched):
    start = sched.index("export async function runStripePriceParityAlertPass(")
    brace = sched.index("{", sched.index(")", start))
    end = gate._bracket_match(sched, brace)
    return brace, end


def test_consent_gate_clean_as_committed(tmp_path):
    assert _consent_fixture(tmp_path, lambda s: s) == []


def test_consent_gate_fails_when_only_a_comment_names_the_gate(tmp_path):
    # SecurityLab's mutation B: real call -> `true`, the comment mention kept.
    real_call = 'requireSendApproval(env, "stripePriceParityAlert")'

    def mutate(s):
        a, b = _parity_body_span(s)
        body = s[a:b]
        assert body.count(real_call) == 1
        body = body.replace(real_call, "true")
        # a comment mention survives, which is what fooled the old gate
        assert "requireSendApproval(" in body
        assert "requireSendApproval(" not in gate._blank_strings_and_comments(body)
        return s[:a] + body + s[b:]

    errs = _consent_fixture(tmp_path, mutate)
    assert len(errs) == 1 and "runStripePriceParityAlertPass" in errs[0]


def test_consent_gate_ignores_the_name_inside_a_string(tmp_path):
    def mutate(s):
        a, b = _parity_body_span(s)
        body = gate._blank_strings_and_comments(s[a:b])
        assert "requireSendApproval(" in body
        return s[:a] + '{ console.log("requireSendApproval(x)"); return; }' + s[b:]

    errs = _consent_fixture(tmp_path, mutate)
    assert len(errs) == 1 and "runStripePriceParityAlertPass" in errs[0]


# GATE-132 (AuditLab 2026-11-19): no regex-literal branch. M1 = a quote in a
# regex flipped string state; M2 = a `//` in a regex read as a line comment
# (both modes). Ablate with `git show 8cd4f4613:scripts/preship_gate.py`.

def test_gate132_m1_quote_in_regex_keeps_following_code_blank_mode():
    out = gate._blank_strings_and_comments('s.replace(/"/g, "&quot;"); await sendEmail(x);')
    assert "sendEmail(" in out


def test_gate132_m1_comment_after_quote_regex_removed_keep_mode():
    out = gate._strip_ts_comments('const r = s.replace(/"/g, "&quot;"); // DeadlineRadar brand')
    assert "DeadlineRadar" not in out and '"&quot;"' in out


def test_gate132_m2_slashes_in_regex_not_a_comment_both_modes():
    src = 'const u = /^https:\/\//.test(raw) ? store.write(x) : null;'
    assert "store.write(" in gate._blank_strings_and_comments(src)
    assert "store.write(" in gate._strip_ts_comments(src)
    b64 = 'btoa(b).replace(/\+/g,"-").replace(/\//g,"_"); store.save(x);'
    assert "store.save(" in gate._blank_strings_and_comments(b64)
    assert "store.save(" in gate._strip_ts_comments(b64)


def test_gate132_division_and_postfix_are_not_regex():
    for src in ('const q = a / b; // c()', 'const q = a! / 2; // c()', 'i++ / 2; // c()'):
        out = gate._strip_ts_comments(src)
        assert "c()" not in out and "/" in out, src


def test_gate132_negative_controls_unchanged():
    assert gate._blank_strings_and_comments('f(); // sendEmail(x)').strip() == "f();"
    assert "sendEmail" not in gate._blank_strings_and_comments('const s = "sendEmail(x)";')


def test_gate132_regex_body_is_a_literal_in_blank_mode():
    assert "demo_locked" not in gate._blank_strings_and_comments("const r = /demo_locked/;")


def test_gate132_nested_template_in_interpolation():
    src = 'const h = `<ul>${rows.map((r) => `<li>${r}</li>`).join("")}</ul>`; store.write(x);'
    assert "store.write(" in gate._blank_strings_and_comments(src)
    assert gate._strip_ts_comments(src) == src


def test_gate132_keep_mode_matches_ts_lex_lines_on_worker_src():
    files = sorted((REPO / "worker" / "src").rglob("*.ts"))
    assert len(files) >= 20
    for p in files:
        text = p.read_text(encoding="utf-8")
        lines, ok = gate._ts_lex_lines(text)
        assert ok, p
        keep = gate._blank_strings_and_comments(text, keep_strings=True)
        assert keep.split("\n") == lines, p
        blank = gate._blank_strings_and_comments(text)
        assert len(blank) == len(keep), p
        assert all(x == y or x == " " for x, y in zip(blank, keep)), p
