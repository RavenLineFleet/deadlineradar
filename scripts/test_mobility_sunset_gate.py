"""REG-6 gate: time-limited mobility rules must carry a date-typed rule_expires_on.

    python -m pytest scripts/test_mobility_sunset_gate.py -q
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

REPO_ROOT = Path(HERE).parent
TODAY = date(2026, 10, 12)


def _repo(tmp_path, records):
    p = tmp_path / "worker" / "src"
    p.mkdir(parents=True)
    (p / "mobility_rules.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    return tmp_path


def rec(**kw):
    base = {"state_slug": "xx", "citation": "Code 1", "data_gap_note": None, "notes": None, "verified_date": "2026-10-01"}
    base.update(kw)
    return base


def test_shipped_data_is_clean_today():
    assert gate.check_mobility_sunset_field(REPO_ROOT, today=TODAY) == []


def test_prose_only_sunset_is_a_failure(tmp_path):
    r = rec(notes="This rests on EMERGENCY legislation that expires November 8, 2026.")
    errs = gate.check_mobility_sunset_field(_repo(tmp_path, [r]), today=TODAY)
    assert len(errs) == 1 and "no date-typed rule_expires_on" in errs[0]


def test_each_prose_field_and_keyword_is_seen(tmp_path):
    for f in ("citation", "data_gap_note", "notes"):
        for text in ("temporary act expiring Dec 1", "TIME-LIMITED: expires on 2027-01-01", "emergency amendment that will expire on 2027-03-01"):
            errs = gate.check_mobility_sunset_field(_repo(tmp_path / f"{f}{abs(hash(text))}", [rec(**{f: text})]), today=TODAY)
            assert len(errs) == 1, (f, text)


def test_dated_sunset_passes_and_prose_alone_about_a_repeal_does_not_trip(tmp_path):
    ok = rec(notes="emergency act expiring Nov 8", rule_expires_on="2026-11-08")
    repealed = rec(state_slug="yy", notes="old statute was repealed effective 2025-10-01")
    assert gate.check_mobility_sunset_field(_repo(tmp_path, [ok, repealed]), today=TODAY) == []


def test_malformed_date_is_a_failure(tmp_path):
    errs = gate.check_mobility_sunset_field(_repo(tmp_path, [rec(rule_expires_on="Nov 8")]), today=TODAY)
    assert len(errs) == 1 and "not an ISO date" in errs[0]


def test_arrived_sunset_needs_a_verification_on_or_after_it(tmp_path):
    r = rec(rule_expires_on="2026-11-08", verified_date="2026-10-12")
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "a", [r]), today=date(2026, 11, 7)) == []
    errs = gate.check_mobility_sunset_field(_repo(tmp_path / "b", [r]), today=date(2026, 11, 8))
    assert len(errs) == 1 and "has arrived" in errs[0]
    r2 = rec(rule_expires_on="2026-11-08", verified_date="2026-11-08")
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "c", [r2]), today=date(2026, 11, 20)) == []
    r3 = rec(rule_expires_on="2026-11-08", verified_date=None)
    assert len(gate.check_mobility_sunset_field(_repo(tmp_path / "d", [r3]), today=date(2026, 11, 9))) == 1


def test_advisory_counts_down(tmp_path, capsys):
    gate.print_mobility_sunset_advisory(_repo(tmp_path, [rec(state_slug="dc", rule_expires_on="2026-11-08")]), today=TODAY)
    assert "dc: rule_expires_on 2026-11-08 in 27 day(s)" in capsys.readouterr().out


# ---- REG-7: the detector is a shape, not a list; measured against the real 55 and AuditLab's phrasings ----

FIRES = [
    "emergency act expires November 8, 2026",
    "This rests on emergency legislation. It expires November 8, 2026.",     # [^.] used to stop at the period
    "temporary act sunsets on 2027-03-01",
    "emergency rule lapses 2027-03-01",
    "emergency amendment terminates 2027-03-01",
    "emergency act, repealed effective 2027-03-01",
    "emergency legislation, valid through March 1, 2027",
    "interim rule expires 2027-03-01",
    "this stopgap measure expires 2027-03-01",
    "expires 2027-03-01 under the emergency act",                           # order reversed
    "SUNSET: the entire Act is scheduled for repeal 2030-07-01",
]
SILENT = [
    "The old statute was repealed effective 2025-10-01 and replaced",       # past repeal, no time-limit word
    "The 2023 emergency order was repealed on 2024-01-05.",                  # past-dated: history, not pending
    "3-year cycle expiring June 30, 2026",                                   # renewal cycle, and already past
    "if the home license lapses, the privilege ends",                        # no date
    "temporary practice permit; renew by 2027-12-31",                        # time-limit word, no end verb
]


def test_detector_fires_on_every_phrasing_of_a_pending_sunset():
    for t in FIRES:
        assert gate._sunset_prose_hit(t, TODAY), t


def test_detector_stays_silent_on_history_cycles_and_dateless_text():
    for t in SILENT:
        assert not gate._sunset_prose_hit(t, TODAY), t


def test_no_year_date_counts_as_pending_a_dated_past_one_does_not():
    assert gate._sunset_prose_hit("emergency act expires Nov. 8", TODAY)
    assert not gate._sunset_prose_hit("emergency act expired 2026-10-11", date(2026, 10, 12))
    assert gate._sunset_prose_hit("emergency act expires 2026-10-12", date(2026, 10, 12))   # due today has not passed


def test_corpus_measured_exactly_new_mexico_fires_everything_else_silent():
    # GUARD-22 (2026-10-29): D.C. dropped out. D.C. Law 26-183 made its rule permanent (eff. 2026-09-25), and the
    # record's sunset prose was rewritten.
    recs = json.loads((REPO_ROOT / "worker" / "src" / "mobility_rules.json").read_text(encoding="utf-8"))["records"]
    fired = {r["state_slug"] for r in recs
             if any(gate._sunset_prose_hit(r.get(f) or "", TODAY) for f in ("citation", "data_gap_note", "notes"))}
    assert fired == {"new-mexico"}


def test_every_exemption_names_a_date_the_detector_still_finds_on_that_record():
    recs = {r["state_slug"]: r for r in json.loads((REPO_ROOT / "worker" / "src" / "mobility_rules.json").read_text(encoding="utf-8"))["records"]}
    assert gate.SUNSET_PROSE_EXEMPT
    for slug, ex in gate.SUNSET_PROSE_EXEMPT.items():
        assert ex["reason"].strip() and ex["date"]
        found = {d for f in ("citation", "data_gap_note", "notes") for d in gate._sunset_prose_dates(recs[slug].get(f) or "", TODAY)}
        assert ex["date"] in found, (slug, ex["date"], found)


def test_exemption_is_what_silences_new_mexico_and_removing_it_fails(tmp_path, monkeypatch):
    nm = rec(state_slug="new-mexico", notes="SUNSET: the entire Act is scheduled for repeal 2030-07-01 (routinely extended).")
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "a", [nm]), today=TODAY) == []
    monkeypatch.setattr(gate, "SUNSET_PROSE_EXEMPT", {})
    assert len(gate.check_mobility_sunset_field(_repo(tmp_path / "b", [nm]), today=TODAY)) == 1


# ---- REG-8: the exemption is keyed on the exempted DATE, so another sunset in the same state still errors ----

NM_OLD = "SUNSET: the entire Act is scheduled for repeal 2030-07-01 (routinely extended)."
NM_NEW = " EMERGENCY: NM enacted an emergency mobility amendment effective 2027-01-15 that expires 2027-04-15 unless made permanent."


def test_a_second_sunset_in_an_exempt_state_still_errors(tmp_path):
    only_old = rec(state_slug="new-mexico", notes=NM_OLD)
    both = rec(state_slug="new-mexico", notes=NM_OLD + NM_NEW)
    assert gate.check_mobility_sunset_field(_repo(tmp_path / "a", [only_old]), today=TODAY) == []
    errs = gate.check_mobility_sunset_field(_repo(tmp_path / "b", [both]), today=TODAY)
    assert len(errs) == 1 and "no date-typed rule_expires_on" in errs[0]


def test_identical_prose_on_a_non_exempt_state_errors_control(tmp_path):
    assert len(gate.check_mobility_sunset_field(_repo(tmp_path, [rec(state_slug="connecticut", notes=NM_OLD + NM_NEW)]), today=TODAY)) == 1


def test_a_yearless_sunset_date_is_never_covered_by_an_exemption(tmp_path):
    nm = rec(state_slug="new-mexico", notes=NM_OLD + " EMERGENCY act expires Nov. 8.")
    assert len(gate.check_mobility_sunset_field(_repo(tmp_path, [nm]), today=TODAY)) == 1


def test_a_changed_exempt_date_is_a_stale_exemption(monkeypatch):
    monkeypatch.setitem(gate.SUNSET_PROSE_EXEMPT, "new-mexico", {"date": "2031-07-01", "reason": "x"})
    errs = gate.check_mobility_sunset_field(REPO_ROOT, today=TODAY)
    assert any("new-mexico" in e for e in errs)


# ---- SRC-122: the same detector over the published pages, not just the record prose ----

def _site(tmp_path, pages, records=()):
    _repo(tmp_path, list(records))
    for rel, body in pages.items():
        f = tmp_path / "docs" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return tmp_path


DEAD = "<h1>The Catch</h1><p>D.C. rewrote it by <em>emergency</em> act that expires November 8, 2026.</p>"


def test_shipped_pages_are_clean_today():
    assert gate.check_published_sunset_prose(REPO_ROOT, today=date(2026, 10, 29)) == []


def test_unbacked_published_sunset_fails_and_names_the_page(tmp_path):
    errs = gate.check_published_sunset_prose(_site(tmp_path, {"blog/x/index.html": DEAD}), today=date(2026, 10, 29))
    assert len(errs) == 1 and "docs/blog/x/index.html" in errs[0] and "2026-11-08" in errs[0]


def test_a_record_tracking_the_date_backs_it_control(tmp_path):
    root = _site(tmp_path, {"dc/index.html": DEAD}, [rec(state_slug="dc", rule_expires_on="2026-11-08")])
    assert gate.check_published_sunset_prose(root, today=date(2026, 10, 29)) == []


def test_the_dc_shape_clearing_the_record_field_turns_the_page_red(tmp_path):
    # exactly SRC-122: the record went permanent (field cleared), the page did not
    root = _site(tmp_path, {"blog/x/index.html": DEAD}, [rec(state_slug="dc", rule_expires_on=None)])
    assert len(gate.check_published_sunset_prose(root, today=date(2026, 10, 29))) == 1


def test_yearless_date_and_entity_escaped_text_are_seen(tmp_path):
    card = "<p>by emergency act effective Aug. 10, expiring Nov. 8. Here&rsquo;s more.</p>"
    errs = gate.check_published_sunset_prose(_site(tmp_path, {"blog/index.html": card}), today=date(2026, 10, 29))
    assert len(errs) == 1 and "with no year" in errs[0]


def test_script_and_style_are_not_published_prose(tmp_path):
    page = "<script>var s='emergency act expires 2027-03-01'</script><style>/* temporary, expires 2027-03-01 */</style>"
    assert gate.check_published_sunset_prose(_site(tmp_path, {"a/index.html": page}), today=date(2026, 10, 29)) == []


def test_per_page_exemption_covers_only_its_date_and_page(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "SUNSET_PUBLISHED_EXEMPT", {"blog/x/index.html": {"date": "2026-11-08", "reason": "t"}})
    root = _site(tmp_path, {"blog/x/index.html": DEAD, "blog/y/index.html": DEAD})
    errs = gate.check_published_sunset_prose(root, today=date(2026, 10, 29))
    assert len(errs) == 1 and "blog/y/" in errs[0]


def test_pre_fix_dc_post_from_history_fires():
    import subprocess
    raw = subprocess.run(["git", "show", "86ba28859:docs/blog/virginia-to-washington-dc-cpa-mobility/index.html"],
                         cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    assert raw, "positive control unreadable"
    assert "2026-11-08" in gate._sunset_prose_dates(gate._published_text(raw), date(2026, 10, 29))


# ---- SRC-123: copy outside element text, and a sunset that has already passed ----

SENT = "D.C. rewrote it by emergency act that expires Nov. 8, 2026."


@pytest.mark.parametrize("page", [
    f'<head><meta name="description" content="{SENT}"></head><body><p>ok</p></body>',
    f'<head><meta property="og:description" content=\'{SENT}\'></head>',
    f'<script type="application/ld+json">{{"@type":"Article","description":"{SENT}"}}</script>',
    f'<a href="/x/" title="{SENT}">x</a>',
])
def test_meta_attribute_and_ld_json_copy_is_seen(tmp_path, page):
    errs = gate.check_published_sunset_prose(_site(tmp_path, {"blog/x/index.html": page}), today=date(2026, 10, 29))
    assert len(errs) == 1 and "2026-11-08" in errs[0]


def test_xml_feed_is_published_corpus(tmp_path):
    feed = f"<rss><channel><item><description>{SENT}</description></item></channel></rss>"
    errs = gate.check_published_sunset_prose(_site(tmp_path, {"feed.xml": feed}), today=date(2026, 10, 29))
    assert len(errs) == 1 and "docs/feed.xml" in errs[0]


@pytest.mark.parametrize("today", [date(2026, 11, 8), date(2026, 11, 9), date(2027, 6, 1)])
def test_published_sunset_stays_red_after_the_date_passes(tmp_path, today):
    root = _site(tmp_path, {"blog/x/index.html": DEAD}, [rec(state_slug="dc", rule_expires_on=None)])
    assert len(gate.check_published_sunset_prose(root, today=today)) == 1


def test_record_prose_still_drops_a_passed_sunset_control():
    # include_past is published-copy only; record prose keeps REG-6's "a passed repeal is history"
    assert gate._sunset_prose_dates(SENT, date(2026, 11, 9)) == []
    assert gate._sunset_prose_dates(SENT, date(2026, 11, 9), include_past=True) == ["2026-11-08"]


def test_pre_fix_dc_post_meta_copy_is_seen_and_stays_red_past_nov9():
    import subprocess
    raw = subprocess.run(["git", "show", "86ba28859:docs/blog/virginia-to-washington-dc-cpa-mobility/index.html"],
                         cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    assert raw, "positive control unreadable"
    text = gate._published_text(raw)
    assert text.count("Nov. 8, 2026") >= 3  # meta, og, twitter descriptions; element text alone had 0
    assert "2026-11-08" in gate._sunset_prose_dates(text, date(2026, 12, 1), include_past=True)


# ---- SRC-124: present-tense copy around a dated changeover ----

def test_shipped_changeover_copy_is_period_named():
    assert gate.check_mobility_changeover_tense(REPO_ROOT) == []


@pytest.mark.parametrize("summary", [
    "Right now the test is close to universal.", "Massachusetts would replace today's test.",
    "The Board currently treats both.", "Oklahoma is replacing how it decides.", "In the meantime, the Board ...",
])
def test_present_tense_flux_summary_is_a_failure(tmp_path, summary):
    errs = gate.check_mobility_changeover_tense(_repo(tmp_path, [rec(rule_changes_on="2026-11-01", flux_summary=summary)]))
    assert len(errs) == 1 and "[SRC-124][xx]" in errs[0]


def test_period_named_summary_and_undated_flux_row_pass(tmp_path):
    ok = rec(rule_changes_on="2026-11-01", flux_summary="Through October 31, 2026, the test is X. From November 1, 2026, it is Y.")
    undated = rec(state_slug="yy", rule_changes_on=None, flux_summary="For now the sources disagree.")
    assert gate.check_mobility_changeover_tense(_repo(tmp_path, [ok, undated])) == []


def test_published_is_through_boundary_is_a_failure_and_period_naming_is_not(tmp_path):
    bad = '<meta name="description" content="The rule is open through Oct. 31, 2026 and switches Nov. 1.">'
    good = '<meta name="description" content="Open practice through Oct. 31, 2026, individual criteria from Nov. 1, 2026.">'
    other = '<p>Your license is valid through December 31, 2026.</p>'  # not a changeover boundary
    root = _site(tmp_path, {"blog/a/index.html": bad, "blog/b/index.html": good, "blog/c/index.html": other},
                 [rec(rule_changes_on="2026-11-01", flux_summary=None)])
    errs = gate.check_mobility_changeover_tense(root)
    assert len(errs) == 1 and "docs/blog/a/index.html" in errs[0] and "2026-10-31" in errs[0]


def test_pre_fix_oklahoma_copy_is_seen():
    import subprocess
    raw = subprocess.run(["git", "show", "1083e3dca:worker/src/mobility_rules.json"],
                         cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    assert raw, "positive control unreadable"
    ok = [r for r in json.loads(raw)["records"] if r["state_slug"] == "oklahoma"][0]
    assert gate._CHANGEOVER_PRESENT_RE.search(ok["flux_summary"])
