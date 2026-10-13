"""Offline tests for the monthly re-verification runner (no network).

    python -m pytest scripts/reverify -q
"""
import collections
import io
import json
import os
import re
import sys
import urllib.error

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch  # noqa: E402
import runner  # noqa: E402
from fetch import Fetched, Fetcher  # noqa: E402

PAGE = "Fee schedule. Renewal of certificate (biennial): $150.00. Late renewal penalty $50."


# ---------------- judge_check ----------------
def chk(**kw):
    base = {"url": "https://x.gov/a", "anchor": "Renewal of certificate", "window": 80,
            "pattern": r"\$\s*([\d,]+(?:\.\d\d)?)"}
    base.update(kw)
    return base


def test_match_numeric_ignores_cents_and_commas():
    assert runner.judge_check(chk(expect=150), PAGE) == ("MATCH", "150.00")


def test_different_value_next_to_anchor():
    assert runner.judge_check(chk(expect=175), PAGE)[0] == "DIFFERENT"


def test_missing_anchor_is_not_a_change():
    assert runner.judge_check(chk(anchor="Reinstatement"), PAGE)[0] == "NO_ANCHOR"


def test_anchor_matching_survives_pdf_whitespace_and_quotes():
    text = fetch.normalise("Renewal  of certificate\n(biennial): $150")
    assert runner.judge_check(chk(expect=150), text)[0] == "MATCH"


def test_digits_inside_anchor_are_not_the_value():
    text = "Rule 1.6(G)(2) requires 80 hours of CPE"
    c = chk(anchor="Rule 1.6(G)(2) requires", pattern=r"\b(\d{1,3})\b", expect=80)
    assert runner.judge_check(c, text) == ("MATCH", "80")


def test_spelled_out_numbers():
    assert runner._num("one hundred twenty") == 120 and runner._num("eighty") == 80
    assert runner._num("twenty-four") == 24 and runner._num("forty") == 40 and runner._num("banana") is None
    c = chk(anchor="in the amount of", pattern=rf"\b({runner.WORD_NUM_RE}|\d{{1,3}})\b", expect=120)
    assert runner.judge_check(c, "in the amount of one hundred twenty (120) hours")[0] == "MATCH"


def test_ambiguous_anchor_fails_closed():
    """AuditLab 10-02 (NH): a repeated row label must not confirm by document order."""
    text = "Nursing: Initial, renewal 2 years $121 ... Accountancy: Initial, renewal 2 years $201"
    assert runner.judge_check(chk(anchor="Initial, renewal 2 years", expect=201), text)[0] == "AMBIGUOUS_ANCHOR"
    assert runner.judge_check(chk(anchor="Accountancy: Initial, renewal 2 years", expect=201), text)[0] == "MATCH"
    r = {"field": "fee_usd", "checks": [chk(anchor="Initial, renewal 2 years")]}
    assert runner.judge_record(r, {"fee_usd": 121}, {0: ok(text)})["outcome"] == "FAILED"   # never CONFIRMED


def test_duplicated_source_flag_requires_all_occurrences_agree():
    same = "renewal fee in the amount of $100; ... renewal fee in the amount of $100;"
    diff = "renewal fee in the amount of $100; ... renewal fee in the amount of $150;"
    c = chk(anchor="renewal fee in the amount of", expect=100)
    assert runner.judge_check(c, same)[0] == "AMBIGUOUS_ANCHOR"                       # unflagged: strict
    assert runner.judge_check(dict(c, duplicated_source=True), same) == ("MATCH", "100")
    assert runner.judge_check(dict(c, duplicated_source=True), diff)[0] == "AMBIGUOUS_ANCHOR"


def test_expect_text_mode():
    c = chk(anchor="Fee schedule", pattern=None, expect_text="biennial")
    assert runner.judge_check(c, PAGE)[0] == "MATCH"
    assert runner.judge_check(dict(c, expect_text="annual renewal"), PAGE)[0] == "DIFFERENT"


# ---------------- judge_record ----------------
def ok(text, url="https://x.gov/a"):
    return Fetched(url, True, 200, "", text, "abc123")


def test_record_confirmed_against_stored_field():
    r = {"field": "fee_usd", "checks": [chk()]}
    assert runner.judge_record(r, {"fee_usd": 150}, {0: ok(PAGE)})["outcome"] == "CONFIRMED"


def test_record_changed_when_source_differs_from_stored():
    r = {"field": "fee_usd", "checks": [chk()]}
    assert runner.judge_record(r, {"fee_usd": 125}, {0: ok(PAGE)})["outcome"] == "CHANGED"


def test_composite_sum_alaska_300_plus_100():
    """AuditLab 10-02: alaska-reinstatement $400 = 12 AAC 02.340(4) $300 + (13) $100."""
    text = "(4) renewal fee for a certificate $300 ... (13) delayed renewal penalty fee $100"
    r = {"field": "reinstatement_fee_usd", "combine": "sum", "checks": [
        chk(anchor="(4) renewal fee", expect=300), chk(anchor="(13) delayed renewal penalty", expect=100)]}
    assert runner.judge_record(r, {"reinstatement_fee_usd": 400}, {0: ok(text), 1: ok(text)})["outcome"] == "CONFIRMED"
    assert runner.judge_record(r, {"reinstatement_fee_usd": 350}, {0: ok(text), 1: ok(text)})["outcome"] == "CHANGED"


def test_sum_plus_fielded_checks_mixed():
    text = "Processing Fee $200 ... Registration Fee $110 ... must report 40 CPE credit hours"
    r = {"field": "reinstatement_fee_usd", "combine": "sum", "checks": [
        chk(anchor="Processing Fee", expect=200), chk(anchor="Registration Fee", expect=110),
        chk(anchor="must report", pattern=r"(\d+)", field="penalty_cpe_hours")]}
    fx = {0: ok(text), 1: ok(text), 2: ok(text)}
    assert runner.judge_record(r, {"reinstatement_fee_usd": 310, "penalty_cpe_hours": 40}, fx)["outcome"] == "CONFIRMED"
    assert runner.judge_record(r, {"reinstatement_fee_usd": 310, "penalty_cpe_hours": 80}, fx)["outcome"] == "CHANGED"


def test_sum_with_formula_text_guard_idaho():
    """IDAPA 24.30.01.400: reinstatement = sum of unpaid license fees for the preceding 3 cycles = 3 x $120."""
    text = "Active License $120 ... Reinstatement License Sum of unpaid license fees for the preceding 3 license renewal cycles"
    lic = chk(anchor="Active License", expect=120)
    r = {"field": "reinstatement_fee_usd", "combine": "sum", "checks": [lic, lic, lic,
         chk(anchor="Reinstatement License", pattern=None, expect_text="preceding 3 license renewal cycles")]}
    fx = {i: ok(text) for i in range(4)}
    assert runner.judge_record(r, {"reinstatement_fee_usd": 360}, fx)["outcome"] == "CONFIRMED"
    r2 = dict(r, checks=r["checks"][:3] + [dict(r["checks"][3], expect_text="preceding 4 license renewal cycles")])
    assert runner.judge_record(r2, {"reinstatement_fee_usd": 360}, fx)["outcome"] == "CHANGED"


def test_minutes_to_hours_divide_nc():
    """21 NCAC 08G .0401: 2,000 CPE minutes (= 40 h) and 50 ethics minutes (= 1 h); 50 min per hour."""
    text = "(d) Active CPAs shall complete 2,000 CPE minutes ... (e) A CPA shall complete a minimum of 50 CPE minutes annually in ethics"
    r = {"checks": [chk(anchor="(d) Active CPAs shall complete", pattern=r"([\d,]+) CPE minutes", divide=50, field="total_hours"),
                    chk(anchor="(e) A CPA shall complete a minimum of", pattern=r"([\d,]+) CPE minutes", divide=50, field="ethics_hours")]}
    fx = {0: ok(text), 1: ok(text)}
    assert runner.judge_record(r, {"total_hours": 40, "ethics_hours": 1}, fx)["outcome"] == "CONFIRMED"
    assert runner.judge_record(r, {"total_hours": 2000, "ethics_hours": 1}, fx)["outcome"] == "CHANGED"


def test_multiply_florida_two_sets():
    text = "two sets of certificates ... Each set must include 120 total CPE hours, to include ii. 8 hours in ethics"
    c = chk(anchor="to include ii.", pattern=r"\b(\d{1,3})\b", multiply=2, field="penalty_ethics_hours")
    r = {"checks": [c]}
    assert runner.judge_record(r, {"penalty_ethics_hours": 16}, {0: ok(text)})["outcome"] == "CONFIRMED"
    assert runner.judge_record(r, {"penalty_ethics_hours": 8}, {0: ok(text)})["outcome"] == "CHANGED"


def test_per_check_fields():
    text = "total of 80 hours ... at least 4 hours in ethics"
    r = {"checks": [chk(anchor="total of", pattern=r"(\d+) hours", field="total_hours"),
                    chk(anchor="at least", pattern=r"(\d+) hours", field="ethics_hours")]}
    assert runner.judge_record(r, {"total_hours": 80, "ethics_hours": 4}, {0: ok(text), 1: ok(text)})["outcome"] == "CONFIRMED"
    assert runner.judge_record(r, {"total_hours": 80, "ethics_hours": 2}, {0: ok(text), 1: ok(text)})["outcome"] == "CHANGED"


def test_fetch_failure_is_failed_not_changed():
    r = {"field": "fee_usd", "checks": [chk()]}
    bad = Fetched("https://x.gov/a", False, 404, "http_404", None, None)
    assert runner.judge_record(r, {"fee_usd": 150}, {0: bad})["outcome"] == "FAILED"


def test_manual_recipe_never_confirmed():
    assert runner.judge_record({"manual": "board email only"}, {}, {})["outcome"] == "MANUAL"


# ---------------- history format (Orchestrator 2026-10-02 12:23) ----------------
def test_history_line_exact_format_and_append():
    recipe = {"checks": [chk()]}
    line = runner.history_line("2026-10-05", [{"i": 0, "url": "https://x.gov/a", "sha256": "deadbeef"}], recipe)
    assert line == ("2026-10-05 (automated re-verification, auto-anchor): confirmed via https://x.gov/a, "
                    "sha256=deadbeef, anchor='Renewal of certificate'")
    rec = {"verification_history": "2026-08-13: manual re-read.", "last_manual_verified_date": "2026-08-13"}
    runner.apply_confirmed(rec, "renewal_fees", line, "2026-10-05")
    assert rec["verification_history"] == "2026-08-13: manual re-read.\n\n" + line
    assert isinstance(rec["verification_history"], str)
    assert rec["verified_date"] == "2026-10-05" and rec["verified_method"] == "auto-anchor"
    assert rec["last_manual_verified_date"] == "2026-08-13"


# ---------------- end-to-end run() on temp data ----------------
class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, method="http"):
        self.calls.append(url)
        t = self.pages.get(url)
        return ok(t, url) if t else Fetched(url, False, 404, "http_404", None, None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    recs = {
        "renewal_fees": [{"id": "a-fee", "fee_usd": 150, "verified_date": "2026-09-01", "verified_method": "manual",
                          "verification_history": "old", "last_manual_verified_date": "2026-09-01"},
                         {"id": "b-fee", "fee_usd": 99, "verified_date": "2026-09-01", "verification_history": "old"},
                         {"id": "c-fee", "fee_usd": 10, "verified_date": "2026-09-01", "verification_history": "old"}],
        "cpa_deadlines": [], "cpe_hours": [], "reinstatement": []}
    for ds, r in recs.items():
        (data / f"{ds}.json").write_text(json.dumps({"_meta": {}, "records": r}), encoding="utf-8")
    recipes = {"a-fee": {"dataset": "renewal_fees", "field": "fee_usd", "checks": [chk(url="https://x.gov/a")]},
               "b-fee": {"dataset": "renewal_fees", "field": "fee_usd", "checks": [chk(url="https://x.gov/b")]},
               "c-fee": {"dataset": "renewal_fees", "field": "fee_usd",
                         "checks": [chk(url="https://x.gov/dead", alt_urls=["https://x.gov/gone"])]}}
    (data / "reverify_recipes.json").write_text(json.dumps(recipes), encoding="utf-8")
    monkeypatch.setattr(runner, "DATA", str(data))
    monkeypatch.setattr(runner, "RECIPES", str(data / "reverify_recipes.json"))
    monkeypatch.setattr(runner, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(runner, "INBOXES", {"assetlab": str(tmp_path / "asset"), "auditlab": str(tmp_path / "audit")})
    pages = {"https://x.gov/a": PAGE, "https://x.gov/b": PAGE}
    return tmp_path, FakeFetcher(pages)


def _recs(tmp):
    return {r["id"]: r for r in json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))["records"]}


def test_dry_run_all_writes_nothing(env):
    tmp, ff = env
    before = (tmp / "data" / "renewal_fees.json").read_bytes()
    out = runner.run(apply=False, all_records=True, fetcher=ff, today="2026-10-01")
    assert out["report"]["counts"] == {"CONFIRMED": 1, "CHANGED": 1, "FAILED": 1}
    assert (tmp / "data" / "renewal_fees.json").read_bytes() == before
    assert not (tmp / "state").exists() and not (tmp / "asset").exists()


def test_apply_bumps_confirmed_never_edits_changed_and_files_notes(env):
    tmp, ff = env
    runner.run(apply=True, fetcher=ff, today="2026-10-01")
    r = _recs(tmp)
    assert r["a-fee"]["verified_date"] == "2026-10-01" and r["a-fee"]["verified_method"] == "auto-anchor"
    assert r["a-fee"]["last_manual_verified_date"] == "2026-09-01"
    assert r["b-fee"]["fee_usd"] == 99 and r["b-fee"]["verified_date"] == "2026-09-01"      # CHANGED: untouched
    assert r["c-fee"]["verified_date"] == "2026-09-01"                                        # FAILED: untouched
    assert any("CHANGED_b-fee" in n for n in os.listdir(tmp / "asset"))
    assert any("CHANGED_b-fee" in n for n in os.listdir(tmp / "audit"))
    st = json.loads((tmp / "state" / "reverify_status.json").read_text(encoding="utf-8"))
    assert st["not_confirmed_this_run"] == ["b-fee", "c-fee"] and st["fail_counts"] == {"c-fee": 1}
    assert st["stale_records"] == ["b-fee", "c-fee"] and st["verified_dates"]["a-fee"] == "2026-10-01"
    assert "https://x.gov/gone" in ff.calls                                                  # alt url tried


def test_daily_selection_only_due_records(env):
    tmp, ff = env
    runner.run(apply=True, fetcher=ff, today="2026-10-01")          # a-fee confirmed today
    ff.calls.clear()
    out = runner.run(apply=True, fetcher=ff, today="2026-10-05")   # a-fee 4 days old: not due
    assert "https://x.gov/a" not in ff.calls and out["report"]["checked"] == 2
    ff.calls.clear()
    runner.run(apply=True, fetcher=ff, today="2026-10-22")         # 21 days old: due again
    assert "https://x.gov/a" in ff.calls


def test_tranche_spreads_a_same_day_cohort_stale27():
    """STALE-27: 81 records confirmed on one day must not stay on one date. Simulate 30 daily runs with
    every pick confirmed: each record re-verified within 10 days, never more than ceil(n/10) per date."""
    from datetime import date, timedelta
    ids = [f"r{i:02d}" for i in range(81)]
    by_id = {i: ("cpa_deadlines", {"id": i, "last_verified": "2026-10-02"}) for i in ids}
    by_id["old"] = ("cpa_deadlines", {"id": "old", "last_verified": "2026-09-21"})
    by_id["f1"] = ("renewal_fees", {"id": "f1", "verified_date": "2026-10-02"})
    first = runner.tranche(sorted(by_id), by_id, date(2026, 10, 4))
    assert len(first) == 9 + 1 and "old" in first and "f1" in first        # ceil(82/10)=9 cpa, ceil(1/10)=1
    assert first == runner.tranche(sorted(by_id), by_id, date(2026, 10, 4))  # deterministic
    d = date(2026, 10, 4)
    for _ in range(30):
        for i in runner.tranche(sorted(by_id), by_id, d):
            ds, r = by_id[i]
            r[runner.DATE_FIELD[ds]] = d.isoformat()
        ages = [runner._age_days(r, ds, d) for ds, r in by_id.values()]
        assert max(ages) <= 10
        d += timedelta(days=1)
    per = collections.Counter(r["last_verified"] for ds, r in by_id.values() if ds == "cpa_deadlines")
    assert max(per.values()) <= 9 and len(per) >= 9


def test_tranche_failing_record_does_not_take_a_quota_slot():
    from datetime import date
    by_id = {f"r{i:02d}": ("cpa_deadlines", {"id": f"r{i:02d}", "last_verified": "2026-10-02"}) for i in range(19)}
    by_id["down"] = ("cpa_deadlines", {"id": "down", "last_verified": "2026-09-21"})      # oldest, source down
    assert "down" in runner.tranche(sorted(by_id), by_id, date(2026, 10, 4))
    picks = runner.tranche(sorted(by_id), by_id, date(2026, 10, 4), failing={"down": 1})
    assert len(picks) == 2 and "down" not in picks                       # quota ceil(20/10)=2, both drain 10-02


def test_cpa_cap_bounds_backstop_and_recoveries_stale39():
    from datetime import date
    by_id = {f"r{i:02d}": ("cpa_deadlines", {"id": f"r{i:02d}", "last_verified": "2026-10-02"}) for i in range(81)}
    by_id["old"] = ("cpa_deadlines", {"id": "old", "last_verified": "2026-09-21"})
    by_id["fee"] = ("renewal_fees", {"id": "fee", "verified_date": "2026-10-02"})
    allids = sorted(by_id)                                                  # 10-23: whole cohort past DUE_DAYS
    kept = runner.cap_cpa(allids, by_id, date(2026, 10, 23))
    cpa = [i for i in kept if by_id[i][0] == "cpa_deadlines"]
    assert len(cpa) == runner.CPA_STAMP_CAP and "old" in cpa and "fee" in kept   # oldest kept; other datasets untouched
    assert runner.cap_cpa(allids[:5], by_id, date(2026, 10, 23)) == allids[:5]    # under the cap: unchanged


def _cpa_by_id(n_old, n_today, today="2026-10-23"):
    by_id = {f"old{i:02d}": ("cpa_deadlines", {"id": f"old{i:02d}", "last_verified": "2026-10-02"}) for i in range(n_old)}
    by_id.update({f"hand{i:02d}": ("cpa_deadlines", {"id": f"hand{i:02d}", "last_verified": today}) for i in range(n_today)})
    return by_id


def test_cpa_cap_is_derived_from_what_already_carries_today_stale53():
    """AuditLab STALE-53: hand stamps dated today BEFORE the run use up the budget, so the cohort on today's
    date can never exceed the preship ratchet (10) however many hand stamps landed first."""
    from datetime import date
    today = date(2026, 10, 23)
    for hand, expect_room in ((0, 8), (1, 8), (2, 7), (3, 6), (9, 0), (10, 0)):
        by_id = _cpa_by_id(30, hand)
        old = [i for i in by_id if i.startswith("old")]
        kept = [i for i in runner.cap_cpa(sorted(by_id), by_id, today) if i.startswith("old")]
        assert len(kept) == min(expect_room, 30), (hand, len(kept))
        assert hand + len(kept) <= 10 - runner.CPA_MARGIN or hand >= 10 - runner.CPA_MARGIN    # never past the margin
        assert hand + len(kept) <= 10                                                          # never past the gate


def test_cpa_cap_keeps_records_already_dated_today_for_free_stale53():
    from datetime import date
    by_id = _cpa_by_id(30, 2)
    kept = runner.cap_cpa(sorted(by_id), by_id, date(2026, 10, 23))
    assert {"hand00", "hand01"} <= set(kept)               # a same-day re-check does not move the cohort


def test_cpa_cap_equals_the_preship_ratchet_constant_stale33():
    src = open(os.path.join(os.path.dirname(__file__), "..", "preship_gate.py"), encoding="utf-8").read()
    m = re.search(r"^CPA_DEADLINES_MAX_SHARED_VERIFICATION_DATE\s*=\s*(\d+)", src, re.M)
    if not m:
        pytest.skip("ratchet not on this tree yet (AssetLab 831fed08a)")
    assert runner.CPA_STAMP_CAP == int(m.group(1)) - runner.HAND_STAMPS_PER_DAY - runner.CPA_MARGIN   # STALE-40/43


def test_tranche_runs_daily_and_backstop_still_applies(env):
    tmp, ff = env
    runner.run(apply=True, fetcher=ff, today="2026-10-01")            # a-fee confirmed 10-01
    ff.calls.clear()
    out = runner.run(apply=True, fetcher=ff, today="2026-10-02")      # quota ceil(3/10)=1 + 2 failing
    assert out["report"]["checked"] == 2 and "https://x.gov/a" not in ff.calls


def test_as_of_date_follows_oldest_record_forward_only_stale27():
    data = {"cpa_deadlines": {"as_of_date": "2026-10-02", "records": [
        {"id": "a", "last_verified": "2026-10-05"}, {"id": "b", "last_verified": "2026-10-09"}]}}
    assert runner.advance_as_of(data) == "2026-10-05" and data["cpa_deadlines"]["as_of_date"] == "2026-10-05"
    data["cpa_deadlines"]["records"][0]["last_verified"] = "2026-09-21"  # an older record: never moves back
    assert runner.advance_as_of(data) is None and data["cpa_deadlines"]["as_of_date"] == "2026-10-05"
    data["cpa_deadlines"]["records"][0]["last_verified"] = None          # unparseable: no move (fail closed)
    data["cpa_deadlines"]["records"][1]["last_verified"] = "2026-11-01"
    assert runner.advance_as_of(data) is None and data["cpa_deadlines"]["as_of_date"] == "2026-10-05"
    assert runner.advance_as_of({"cpa_deadlines": {"records": []}}) is None   # empty dataset: no move


def test_run_and_replay_both_advance_as_of(env, tmp_path):
    tmp, ff = env
    p = tmp / "data" / "cpa_deadlines.json"
    p.write_text(json.dumps({"as_of_date": "2026-09-01", "records": [{"id": "z", "last_verified": "2026-09-20"}]}),
                 encoding="utf-8")
    out = runner.run(apply=True, fetcher=ff, today="2026-10-01")
    assert out["report"]["as_of_moved_to"] == "2026-09-20"
    assert json.loads(p.read_text(encoding="utf-8"))["as_of_date"] == "2026-09-20"
    p.write_text(json.dumps({"as_of_date": "2026-09-01", "records": [{"id": "z", "last_verified": "2026-09-20"}]}),
                 encoding="utf-8")
    saved = runner.run(apply=False, all_records=True, fetcher=ff, today="2026-10-02")
    sp = tmp_path / "results.json"
    sp.write_text(json.dumps(saved, default=str), encoding="utf-8")
    base_txt = (tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8")
    runner.replay(str(sp), base_loader=lambda ds: json.loads(base_txt)["records"] if ds == "renewal_fees" else
                  [{"id": "z", "last_verified": "2026-09-20"}] if ds == "cpa_deadlines" else [])
    assert json.loads(p.read_text(encoding="utf-8"))["as_of_date"] == "2026-09-20"


def test_failed_escalates_after_two_runs_and_changed_notified_once(env):
    tmp, ff = env
    runner.run(apply=True, fetcher=ff, today="2026-10-01")
    assert not any("RECIPE_FIX" in n for n in os.listdir(tmp / "asset"))
    runner.run(apply=True, fetcher=ff, today="2026-10-02")
    names = os.listdir(tmp / "asset")
    assert any("RECIPE_FIX_c-fee" in n for n in names)
    assert sum("CHANGED_b-fee" in n for n in names) == 1          # same change on day 2: no second note


def test_missing_date_is_always_due(env):
    tmp, ff = env
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    recs["records"][0]["verified_date"] = None
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    assert "a-fee" in runner.run(apply=False, fetcher=ff, today="2026-09-02")["results"]


# ---------------- fetcher politeness (fake network) ----------------
class Resp(io.BytesIO):
    def __init__(self, body, status=200, ctype="text/html"):
        super().__init__(body)
        self.status, self.headers = status, {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_fetcher(handler, robots="User-agent: *\nAllow: /"):
    sleeps, t = [], [0.0]

    def opener(req, timeout=None):
        if req.full_url.endswith("/robots.txt"):
            return Resp(robots.encode())
        return handler(req)

    f = Fetcher(opener=opener, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    return f, sleeps


def test_honest_user_agent_and_rate_limit():
    seen = []

    def h(req):
        seen.append(req.get_header("User-agent"))
        return Resp(b"<p>hello</p>")
    f, sleeps = make_fetcher(h)
    f.get("https://b.gov/1")
    f.get("https://b.gov/2")
    assert all("DeadlineRadar-Reverify" in ua and "Mozilla" not in ua for ua in seen)
    assert sleeps and abs(sum(sleeps) - 2.0) < 1e-9     # robots + 2 pages on one host => two 1s waits


def test_robots_disallow_is_failed_fetch():
    f, _ = make_fetcher(lambda req: Resp(b"x"), robots="User-agent: *\nDisallow: /")
    r = f.get("https://b.gov/private")
    assert not r.ok and r.reason == "robots_disallow"


def test_403_retried_once_404_decisive():
    calls = []

    def h(req):
        calls.append(req.full_url)
        code = 403 if "forbid" in req.full_url else 404
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    f, _ = make_fetcher(h)
    assert f.get("https://b.gov/forbid").reason == "http_403"
    assert f.get("https://b.gov/missing").reason == "http_404"
    assert calls.count("https://b.gov/forbid") == 2 and calls.count("https://b.gov/missing") == 1


# ---------------- watchdog check (directive item 4) ----------------
def test_watchdog_alerts():
    import watchdog_check as wd
    from datetime import datetime as D, timezone as TZ
    now = D(2026, 10, 20, 15, 0, tzinfo=TZ.utc)
    assert wd.check(None, now)[0] is False                                              # never ran
    st = {"mode": "apply", "finished": "2026-10-20T08:02:00+00:00",
          "verified_dates": {"a": "2026-10-01", "b": "2026-09-25"}}
    assert wd.check(st, now)[0] is True                                                 # 19 and 25 days: fine
    assert wd.check(dict(st, verified_dates={"a": "2026-09-24"}), now)[0] is False      # 26 days > 25
    assert wd.check(dict(st, finished="2026-10-18T08:00:00+00:00"), now)[0] is False    # silent > 30h
    assert wd.check(dict(st, mode="dry-run"), now)[0] is False                          # dry run != daily job


def _ls(d):
    return os.listdir(d) if d.exists() else []


def test_manual_records_auto_ticketed_to_assetlab_when_due(env):
    """Orchestrator 10-02 12:47: MANUAL records past 20 days are filed to AssetLab automatically."""
    tmp, ff = env
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    recs["records"].append({"id": "m-fee", "fee_usd": 66, "verified_date": "2026-09-01",
                            "verification_history": "old", "source_url": "https://board.example/fees"})
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    rp = tmp / "data" / "reverify_recipes.json"
    recipes = json.loads(rp.read_text(encoding="utf-8"))
    recipes["m-fee"] = {"dataset": "renewal_fees", "manual": "fee confirmed by board email only", "checks": []}
    rp.write_text(json.dumps(recipes), encoding="utf-8")

    runner.run(apply=True, fetcher=ff, today="2026-09-15")                 # 14 days old: not due
    assert not any("MANUAL_DUE" in n for n in _ls(tmp / "asset"))
    runner.run(apply=True, fetcher=ff, today="2026-10-01")                 # 30 days old: ticket filed
    tickets = [n for n in _ls(tmp / "asset") if "MANUAL_DUE" in n]
    assert len(tickets) == 1
    body = (tmp / "asset" / tickets[0]).read_text(encoding="utf-8")
    assert "m-fee" in body and "board email only" in body and "https://board.example/fees" in body
    runner.run(apply=True, fetcher=ff, today="2026-10-02")                 # same verified date: no repeat
    assert len([n for n in _ls(tmp / "asset") if "MANUAL_DUE" in n]) == 1
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    next(r for r in recs["records"] if r["id"] == "m-fee")["verified_date"] = "2026-10-03"   # AssetLab re-verified
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    runner.run(apply=True, fetcher=ff, today="2026-10-20")                 # 17 days: quiet
    assert len([n for n in _ls(tmp / "asset") if "MANUAL_DUE" in n]) == 1
    runner.run(apply=True, fetcher=ff, today="2026-10-25")                 # 22 days: next month's ticket
    assert len([n for n in _ls(tmp / "asset") if "MANUAL_DUE" in n]) == 2
    assert "m-fee" not in runner.run(apply=False, fetcher=ff, today="2026-10-25")["results"]   # never fetched


def test_watchdog_covers_manual_records_too(env):
    """Orchestrator 10-02 12:48: alert if ANY record, including MANUAL ones, is older than 25 days."""
    import watchdog_check as wd
    from datetime import datetime as D, timezone as TZ
    tmp, ff = env
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    for r in recs["records"]:
        r["verified_date"] = "2026-10-15"
    recs["records"].append({"id": "m-fee", "fee_usd": 66, "verified_date": "2026-09-01", "verification_history": "old"})
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    rp = tmp / "data" / "reverify_recipes.json"
    recipes = json.loads(rp.read_text(encoding="utf-8"))
    recipes["m-fee"] = {"dataset": "renewal_fees", "manual": "board email only", "checks": []}
    rp.write_text(json.dumps(recipes), encoding="utf-8")
    runner.run(apply=True, fetcher=ff, today="2026-10-20")
    st = json.loads((tmp / "state" / "reverify_status.json").read_text(encoding="utf-8"))
    assert "m-fee" in st["verified_dates"] and st["stale_records"] == ["m-fee"]
    ok, msg = wd.check(st, D.fromisoformat(st["finished"]).astimezone(TZ.utc))
    assert ok is False and "m-fee" in msg                       # the MANUAL record alone trips the alert


def test_duplicated_source_disagreement_never_confirms_record():
    """Orchestrator 10-02 13:08 / AuditLab: force two verbatim copies to disagree -> FAILED, never CONFIRMED,
    whichever copy happens to match the stored value."""
    text = ("(2) Pay a non-refundable renewal fee in the amount of $100; and (3) ... "
            "(2) Pay a non-refundable renewal fee in the amount of $150; and (3)")
    c = chk(anchor="Pay a non-refundable renewal fee in the amount of", duplicated_source=True, field="fee_usd")
    r = {"checks": [c]}
    for stored in (100, 150):
        out = runner.judge_record(r, {"fee_usd": stored}, {0: ok(text)})
        assert out["outcome"] == "FAILED" and out["checks"][0]["status"] == "AMBIGUOUS_ANCHOR"


def test_stale25_only_structured_tooling_kind_is_cleared():
    """AuditLab STALE-25: clearing keys on manual_verify_gap_kind == "tooling", never on prose. The 4
    adversarial reasons that fooled the regex are held unless explicitly marked tooling."""
    line = "2026-10-02 (automated re-verification, auto-anchor): x"
    rec = {"manual_verify_gap_reason": "fetch not 200 (status=403, error=HTTPError 403)",
           "manual_verify_gap_kind": "tooling", "verified_date": "2026-09-12", "verification_history": "old"}
    assert runner.apply_confirmed(rec, "cpe_hours", line, "2026-10-02")
    assert rec["manual_verify_gap_reason"] is None and rec["manual_verify_gap_kind"] is None
    assert "cleared manual_verify_gap_reason" in rec["verification_history"] and "HTTPError 403" in rec["verification_history"]
    for gap in ["the rule may have been superseded and was not re-confirmed",
                "the chapter was rescinded; claim-anchor still matches the old text",
                "board confirmed by email; cannot read the fee from any public page",
                "awaiting board reply; was not independently re-confirmed",
                "fetch not 200 (status=403, error=HTTPError 403)"]:          # even tooling prose, if UNMARKED
        rec = {"manual_verify_gap_reason": gap, "verified_date": "2026-09-12", "verification_history": "old"}
        assert not runner.gap_is_tooling(rec) and not runner.gap_is_tooling(gap)
        assert runner.apply_confirmed(rec, "cpe_hours", line, "2026-10-02") is False
        assert rec["verified_date"] == "2026-09-12" and rec["manual_verify_gap_reason"] == gap
    rec = {"manual_verify_gap_reason": "sources disagree", "manual_verify_gap_kind": "substantive",
           "verified_date": "2026-09-12"}
    assert runner.apply_confirmed(rec, "cpe_hours", line, "2026-10-02") is False


def test_substantive_gap_record_reported_held_not_bumped(env):
    tmp, ff = env
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    recs["records"][0]["manual_verify_gap_reason"] = "two official sources disagree"
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    out = runner.run(apply=True, fetcher=ff, today="2026-10-01")
    assert out["results"]["a-fee"]["outcome"] == "HELD"
    assert _recs(tmp)["a-fee"]["verified_date"] == "2026-09-01"
    assert any("HELD_a-fee" in n for n in os.listdir(tmp / "asset"))


def test_replay_reapplies_confirmed_and_respects_changed_main(env, tmp_path):
    """job.py lost-push-race path (REPLAY-1): replay saved CONFIRMED results onto fresh data without
    refetching, but only for records unchanged since the fetch's base commit -- text-only fields included."""
    tmp, ff = env
    saved = runner.run(apply=False, all_records=True, fetcher=ff, today="2026-10-02")
    sp = tmp_path / "results.json"
    sp.write_text(json.dumps(saved, default=str), encoding="utf-8")
    base_txt = (tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8")
    base = lambda ds: json.loads(base_txt)["records"] if ds == "renewal_fees" else []
    assert runner.replay(str(sp), base_loader=base) == 1               # only a-fee was CONFIRMED
    r = _recs(tmp)
    assert r["a-fee"]["verified_date"] == "2026-10-02" and r["a-fee"]["verified_method"] == "auto-anchor"
    assert "sha256=abc123" in r["a-fee"]["verification_history"]       # original evidence, not re-fetched
    assert r["b-fee"]["verified_date"] == "2026-09-01"                  # CHANGED stays untouched
    # main corrects a NON-numeric field mid-run (the 77 text-only recipes' case): must not be stamped
    recs = json.loads(base_txt)
    recs["records"][0]["fee_notes"] = "corrected on main mid-run"
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    assert runner.replay(str(sp), base_loader=base) == 0 and _recs(tmp)["a-fee"]["verified_date"] == "2026-09-01"
    # and a numeric correction too
    recs = json.loads(base_txt)
    recs["records"][0]["fee_usd"] = 175
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    assert runner.replay(str(sp), base_loader=base) == 0
    with pytest.raises(SystemExit):                                     # no base -> refuse (fail closed)
        runner.replay(str(sp))


# ---------------- LLM ticket excerpts (design PASS-with-conditions, AuditLab 2026-10-02) ----------------
import llm_assist  # noqa: E402

PAGE2 = "Section 4. Fees. The reinstatement fee for a lapsed certificate is $150 payable to the board. Other text."


def test_excerpt_verbatim_gets_provenance():
    ask = lambda prompt: {"quote": "The reinstatement fee for a lapsed certificate is $150 payable to the board."}
    ex = llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "https://b.gov/p", "deadbeef", ask=ask)
    assert ex and ex["offset"] == PAGE2.index("The reinstatement") and ex["sha256"] == "deadbeef"
    assert ex["url"] == "https://b.gov/p" and ex["before"].endswith("Fees. ")


def test_excerpt_fabricated_or_abstained_or_model_down_is_dropped():
    fab = lambda prompt: {"quote": "The reinstatement fee for a lapsed certificate is $200 payable to the board."}
    assert llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "u", "s", ask=fab) is None   # not verbatim
    assert llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "u", "s", ask=lambda p: {"quote": ""}) is None
    assert llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "u", "s", ask=lambda p: None) is None
    down = lambda req, timeout=None: (_ for _ in ()).throw(OSError("connection refused"))
    assert llm_assist.ollama_json("hi", opener=down) is None                                      # fail closed


def test_excerpt_invented_citation_prefix_is_dropped():
    # bake-off 2026-10-02: 7b AND 14b both prefixed a real nj-firm passage with "N.J.A.C. 13:29-1A.11(b)"
    pre = lambda p: {"quote": "N.J.A.C. 13:29-1A.11(b) The reinstatement fee for a lapsed certificate is $150"}
    assert llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "u", "s", ask=pre) is None


class _Resp:
    def __init__(self, quote):
        self.b = json.dumps({"response": json.dumps({"quote": quote})}).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self.b


def _opener(answers, calls):
    def op(req, timeout=None):
        model = json.loads(req.data.decode())["model"]
        calls.append(model)
        a = answers[model]
        if isinstance(a, Exception):
            raise a
        return _Resp(a)
    return op


def test_fallback_model_only_when_primary_gives_no_answer(monkeypatch):
    good = "The reinstatement fee for a lapsed certificate is $150 payable to the board."
    m, fb = llm_assist.MODEL, llm_assist.FALLBACK_MODEL
    assert m == "qwen2.5:14b-instruct" and fb == "qwen2.5:7b-instruct"
    calls = []                                                           # 14b times out -> 7b answers
    out, used = llm_assist.ask_with_fallback("p", opener=_opener({m: TimeoutError("timed out"), fb: good}, calls))
    assert calls == [m, fb] and used == fb and out == {"quote": good}
    calls = []                                                           # 14b abstains -> final, no 7b retry
    out, used = llm_assist.ask_with_fallback("p", opener=_opener({m: "", fb: good}, calls))
    assert calls == [m] and used == m and out == {"quote": ""}
    calls = []                                                           # both down -> fail closed
    down = OSError("connection refused")
    assert llm_assist.ask_with_fallback("p", opener=_opener({m: down, fb: down}, calls)) == (None, None)
    calls = []                                                           # provenance names the model that answered
    real = llm_assist._post
    monkeypatch.setattr(llm_assist, "_post", lambda p, model, opener=None:
                        real(p, model, _opener({m: TimeoutError("t"), fb: good}, calls)))
    ex = llm_assist.excerpt_for({"id": "x"}, "reinstatement", PAGE2, "u", "s")
    assert ex and ex["model"] == fb


def test_excerpt_render_is_inert_text():
    ex = {"offset": 3, "quote": "a ```html<script>x</script>``` b", "before": "", "after": "", "url": "u", "sha256": "s"}
    out = llm_assist.render(ex)
    assert out.count("```") == 2 and "```html" not in out                  # only our own fence survives


def test_manual_ticket_carries_excerpt_and_robots_reason(env):
    tmp, ff = env
    recs = json.loads((tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8"))
    recs["records"] += [{"id": "m-ok", "fee_usd": 150, "verified_date": "2026-09-01", "source_url": "https://x.gov/m"},
                        {"id": "m-blocked", "fee_usd": 66, "verified_date": "2026-09-01", "source_url": "https://blocked.gov/f"}]
    (tmp / "data" / "renewal_fees.json").write_text(json.dumps(recs), encoding="utf-8")
    rp = tmp / "data" / "reverify_recipes.json"
    recipes = json.loads(rp.read_text(encoding="utf-8"))
    for rid in ("m-ok", "m-blocked"):
        recipes[rid] = {"dataset": "renewal_fees", "manual": "NOT YET AUTOMATED: test", "checks": []}
    rp.write_text(json.dumps(recipes), encoding="utf-8")
    ff.pages["https://x.gov/m"] = PAGE2
    ff.allowed = lambda u: "blocked.gov" not in u
    ask = lambda prompt: {"quote": "The reinstatement fee for a lapsed certificate is $150 payable to the board."}
    exc = lambda rec, ds, text, url, sha: llm_assist.excerpt_for(rec, ds, text, url, sha, ask=ask)
    runner.run(apply=True, fetcher=ff, today="2026-10-01", excerpter=exc)
    body = next((tmp / "asset" / n).read_text(encoding="utf-8") for n in os.listdir(tmp / "asset") if "MANUAL_DUE" in n)
    assert "offset=" in body and "[[The reinstatement fee" in body and "https://x.gov/m" in body
    assert "blocked.gov/f disallows automated fetching (robots.txt)" in body
    assert "https://blocked.gov/f" not in ff.calls                           # never fetched


# ---------------- _extract_html UTF-16 detection (2026-10-02, HomeLab P0 sweep) ----------------
# sdlegislature.gov serves its /api/Rules/*.html pages as UTF-16LE with no BOM and no charset in
# Content-Type. The old code decoded every body as UTF-8 with errors="replace", which never raises
# on UTF-16 bytes -- it silently produces plausible character-spaced text ("R u l e 2 0") that
# passes every downstream check as a real document while no anchor or value in it can ever match.

def test_extract_html_detects_bomless_utf16le():
    # Real shape: an ASCII-heavy HTML page, UTF-16LE encoded, no BOM.
    html = "<html><body><p>Rule 20:75:04:08. Returning active certificate holders. 24 hours.</p></body></html>"
    body = html.encode("utf-16-le")
    text = fetch._extract_html(body)
    assert "24 hours" in text, f"UTF-16LE body not detected/decoded: {text[:120]!r}"
    assert " R u l e " not in f" {text} ", f"decoded as UTF-8 instead of UTF-16LE: {text[:120]!r}"


def test_extract_html_detects_utf16_with_bom():
    html = "<html><body><p>Rule text with a real BOM. 24 hours.</p></body></html>"
    body = b"\xff\xfe" + html.encode("utf-16-le")
    text = fetch._extract_html(body)
    assert "24 hours" in text, f"BOM'd UTF-16LE body not decoded: {text[:120]!r}"


def test_extract_html_still_reads_normal_utf8():
    # Regression control: an ordinary ASCII/UTF-8 page must not be misdetected as UTF-16 by the
    # byte-parity heuristic (it has no systematic run of null bytes at either parity).
    html = "<html><body><p>Fee schedule. Renewal of certificate (biennial): $150.00.</p></body></html>"
    body = html.encode("utf-8")
    text = fetch._extract_html(body)
    assert "$150.00" in text, f"normal UTF-8 body mis-detected as UTF-16: {text[:120]!r}"


def test_watchdog_live_worker_age_alert_stale27():
    import watchdog_check as wd
    assert wd.live_check({"status": "ok", "worst_record_age_days": 20})[0] is True
    ok, msg = wd.live_check({"status": "ok", "worst_record_age_days": 21})
    assert ok is False and "LIVE" in msg and "deploy_worker" in msg
    from datetime import date
    assert wd.live_check({"status": "ok"}, today=date(2026, 10, 2)) == (True, "live age: not exposed by /api/health yet")
    ok, msg = wd.live_check({"status": "ok"}, today=date(2026, 10, 3))   # MON-10/13: live since 10-03, absence alerts
    assert ok is False and "worst_record_age_days" in msg and "unparseable" in msg
    assert wd.live_check({"worst_record_age_days": True}, today=date(2026, 10, 2))[0] is True     # not a number
    ok, msg = wd.live_check({"status": "ok", "stale": True})                  # MON-13: stale alerts with the key omitted
    assert ok is False and "stale=true" in msg
    assert wd.live_check({"status": "ok", "stale": True, "worst_record_age_days": 5})[0] is False # ...or with a fine age
    assert wd.live_check({"status": "ok", "stale": False, "worst_record_age_days": 12}) == (True, "live age 12d")
    assert wd.live_check(None)[0] is True                                                         # unreachable
    for unknown in (-1, float("nan")):                                                           # MON-12: unknown is not fine
        ok, msg = wd.live_check({"status": "ok", "worst_record_age_days": unknown})
        assert ok is False and "unknown" in msg
    assert wd.live_check({"status": "ok", "worst_record_age_days": 0})[0] is True
    down = lambda url, timeout=None: (_ for _ in ()).throw(OSError("down"))
    assert wd.fetch_health(opener=down) is None


# ---------------- RVF-1 / RVF-1A: redirects re-checked per hop, PDF magic (real loopback servers) ----------------
import http.server
import threading


def _serve(routes):
    """routes: path -> (status, headers dict, body bytes). Returns (server, base_url, hits list)."""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            status, headers, body = routes.get(self.path, (404, {}, b"nf"))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", hits


@pytest.fixture
def two_hosts():
    html = {"Content-Type": "text/html"}
    srv_b, B, hits_b = _serve({
        "/robots.txt": (200, {}, b"User-agent: *\nDisallow: /private/\n"),
        "/public/page.html": (200, html, b"<p>PUBLIC-B-BODY</p>"),
        "/private/page.html": (200, html, b"<p>PRIVATE-B-BODY</p>"),
        "/notice.html": (200, html, b"<html><body><h1>NOT-A-PDF</h1><p>this is an HTML notice page</p></body></html>"),
    })
    srv_a, A, hits_a = _serve({
        "/robots.txt": (200, {}, b"User-agent: *\nAllow: /\n"),
        "/plain.html": (200, html, b"<p>PLAIN-A-BODY</p>"),
        "/jump301": (301, {"Location": B + "/private/page.html"}, b""),
        "/jump302": (302, {"Location": B + "/private/page.html"}, b""),
        "/jump307": (307, {"Location": B + "/private/page.html"}, b""),
        "/jump308": (308, {"Location": B + "/private/page.html"}, b""),
        "/to_public": (302, {"Location": B + "/public/page.html"}, b""),
        "/rel": (302, {"Location": "/plain.html"}, b""),
        "/loop": (302, {"Location": "/loop"}, b""),
        "/schedule.pdf": (302, {"Location": B + "/notice.html"}, b""),
        "/ftp": (302, {"Location": "ftp://example.invalid/x"}, b""),
    })
    yield A, B, hits_a, hits_b
    srv_a.shutdown()
    srv_b.shutdown()


def _real_fetcher():
    return Fetcher(sleep=lambda s: None, allow_private=True)   # fixtures live on loopback


@pytest.mark.parametrize("hop", ["jump301", "jump302", "jump307", "jump308"])
def test_rvf1_redirect_into_robots_disallowed_path_is_refused(two_hosts, hop):
    A, B, hits_a, hits_b = two_hosts
    f = _real_fetcher()
    r = f.get(f"{A}/{hop}")
    assert not r.ok and r.reason == "robots_disallow"
    assert r.final_url == B + "/private/page.html" and r.url == f"{A}/{hop}"
    assert "/private/page.html" not in hits_b            # the disallowed path was never requested
    assert "/robots.txt" in hits_b                       # ...because B's robots.txt WAS consulted


def test_rvf1_negative_controls_still_fetch(two_hosts):
    A, B, hits_a, hits_b = two_hosts
    f = _real_fetcher()
    direct = f.get(f"{B}/public/page.html")
    assert direct.ok and "PUBLIC-B-BODY" in direct.text and direct.final_url == direct.url
    plain = f.get(f"{A}/plain.html")
    assert plain.ok and "PLAIN-A-BODY" in plain.text
    hop = f.get(f"{A}/to_public")                        # cross-host redirect to an ALLOWED path succeeds
    assert hop.ok and "PUBLIC-B-BODY" in hop.text
    assert hop.url == f"{A}/to_public" and hop.final_url == f"{B}/public/page.html"
    rel = f.get(f"{A}/rel")                              # relative Location resolves against the current URL
    assert rel.ok and "PLAIN-A-BODY" in rel.text and rel.final_url == f"{A}/plain.html"


def test_rvf1_each_hop_host_is_rate_limited_under_its_own_key(two_hosts):
    A, B, _, _ = two_hosts
    f = _real_fetcher()
    calls = []
    orig = f._throttle
    f._throttle = lambda h: (calls.append(h), orig(h))[1]
    f.get(f"{A}/to_public")
    assert A.split("//")[1] in f._last_hit and B.split("//")[1] in f._last_hit
    # RVF-3 (SecurityLab): B is keyed by its robots.txt fetch alone, so the key check cannot fail. The
    # serving host must be throttled for its robots.txt AND its page (A: robots+page, B: robots+page).
    assert calls.count(B.split("//")[1]) >= 2 and calls.count(A.split("//")[1]) >= 2


def test_rvf1_redirect_loop_and_bad_scheme_fail_closed(two_hosts):
    A, *_ = two_hosts
    f = _real_fetcher()
    assert f.get(f"{A}/loop").reason == "too_many_redirects"
    assert f.get(f"{A}/ftp").reason == "redirect_bad_scheme"


def test_rvf1_robots_txt_redirect_is_followed():
    html = {"Content-Type": "text/html"}
    srv_b, B, _ = _serve({"/real-robots": (200, {}, b"User-agent: *\nDisallow: /private/\n"),
                          "/private/x": (200, html, b"<p>x</p>")})
    srv_a, A, _ = _serve({"/robots.txt": (301, {"Location": B + "/real-robots"}, b""),
                          "/private/x": (200, html, b"<p>SECRET</p>")})
    try:
        r = _real_fetcher().get(f"{A}/private/x")
        assert not r.ok and r.reason == "robots_disallow"
    finally:
        srv_a.shutdown()
        srv_b.shutdown()


def test_rvf1a_html_served_for_a_pdf_is_not_a_pdf(two_hosts):
    A, *_ = two_hosts
    r = _real_fetcher().get(f"{A}/schedule.pdf", "pdf")
    assert not r.ok and r.reason == "not_a_pdf" and r.text is None
    assert r.final_url.endswith("/notice.html")
    from source_check import _extract_pdf
    assert _extract_pdf(b"<html><body><h1>NOT-A-PDF</h1></body></html>") is None


def test_rvf1a_real_pdf_still_extracts():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "REAL-PDF-BODY fee 150")
    data = doc.tobytes()
    srv, base, _ = _serve({"/robots.txt": (404, {}, b""), "/ok.pdf": (200, {"Content-Type": "application/pdf"}, data)})
    try:
        r = _real_fetcher().get(f"{base}/ok.pdf", "pdf")
        assert r.ok and "REAL-PDF-BODY" in r.text
    finally:
        srv.shutdown()


def test_rvf1_browser_path_rechecks_redirect_target(two_hosts):
    pytest.importorskip("playwright.sync_api")
    A, B, hits_a, hits_b = two_hosts
    f = _real_fetcher()
    r = f.get(f"{A}/jump302", "browser")
    assert not r.ok and r.reason == "robots_disallow"
    assert "/private/page.html" not in hits_b
    ok = f.get(f"{A}/to_public", "browser")              # negative control: allowed cross-host target succeeds
    assert ok.ok and "PUBLIC-B-BODY" in ok.text and ok.final_url == f"{B}/public/page.html"
    plain = f.get(f"{A}/plain.html", "browser")            # no-redirect page still renders through the guard
    assert plain.ok and "PLAIN-A-BODY" in plain.text and plain.final_url == f"{A}/plain.html"
    assert f.get(f"{A}/loop", "browser").reason == "too_many_redirects"


def test_rvf1_trail_names_the_host_that_served_the_bytes():
    r = {"field": "fee_usd", "checks": [chk(anchor="fee", pattern=r"(\d+)", field="fee_usd")]}
    moved = Fetched("https://a.gov/x", True, 200, "", "the fee 150", "ab" * 32, "https://b.gov/y")
    same = Fetched("https://a.gov/x", True, 200, "", "the fee 150", "ab" * 32, "https://a.gov/x")
    assert runner.judge_record(r, {"fee_usd": 150}, {0: moved})["checks"][0]["final_url"] == "https://b.gov/y"
    assert "final_url" not in runner.judge_record(r, {"fee_usd": 150}, {0: same})["checks"][0]


def test_rvf1b_history_line_names_the_serving_url_only_when_redirected():
    recipe = {"checks": [chk()]}
    moved = runner.history_line("2026-10-12", [{"i": 0, "url": "https://www.x.gov/a.pdf", "sha256": "ab",
                                                  "final_url": "https://x.gov/a.pdf"}], recipe)
    assert moved == ("2026-10-12 (automated re-verification, auto-anchor): confirmed via https://www.x.gov/a.pdf "
                     "(served by https://x.gov/a.pdf), sha256=ab, anchor='Renewal of certificate'")
    same = runner.history_line("2026-10-12", [{"i": 0, "url": "https://x.gov/a", "sha256": "ab"}], recipe)
    assert "served by" not in same


# ---------------- RVF-2: the address actually connected to must be public ----------------
@pytest.mark.parametrize("ip,ok", [
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.9", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("::1", False), ("fc00::1", False), ("fe80::1", False), ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False),
    ("8.8.8.8", True), ("151.101.1.1", True), ("2606:4700:4700::1111", True), ("::ffff:8.8.8.8", True),
    ("64:ff9b::7f00:1", False), ("64:ff9b::a9fe:a9fe", False), ("64:ff9b::808:808", True),   # NAT64 embeddings
])
def test_rvf2_address_policy(ip, ok):
    assert fetch._address_allowed(ip, 80) is ok


@pytest.fixture
def public_a_private_c(monkeypatch):
    """A is treated as a public host, C as an internal service (both are loopback fixtures; the policy
    hook tells them apart by port). C 404s robots.txt like a real internal service does."""
    html = {"Content-Type": "text/html"}
    srv_c, C, hits_c = _serve({"/admin/metrics": (200, html, b"<p>INTERNAL-SERVICE-BODY</p>")})
    srv_a, A, hits_a = _serve({
        "/robots.txt": (200, {}, b"User-agent: *\nAllow: /\n"),
        "/plain.html": (200, html, b"<p>PLAIN-A-BODY</p>"),
        "/to-internal": (302, {"Location": C + "/admin/metrics"}, b""),
    })
    a_port = srv_a.server_address[1]
    real = fetch._address_allowed
    monkeypatch.setattr(fetch, "_address_allowed",
                        lambda ip, port: True if port == a_port else real(ip, port))
    yield A, C, hits_a, hits_c
    srv_a.shutdown()
    srv_c.shutdown()


def test_rvf2_redirect_onto_internal_service_is_refused_before_any_request(public_a_private_c):
    A, C, hits_a, hits_c = public_a_private_c
    f = Fetcher(sleep=lambda s: None)                    # production default: allow_private=False
    ok = f.get(f"{A}/plain.html")                        # negative control: the "public" host still fetches
    assert ok.ok and "PLAIN-A-BODY" in ok.text
    r = f.get(f"{A}/to-internal")
    assert not r.ok and r.reason == "blocked_address"
    assert r.final_url == C + "/admin/metrics"
    assert hits_c == []                                  # not even robots.txt or the page: no request was sent


def test_rvf2_direct_dataset_url_on_private_address_is_refused(public_a_private_c):
    A, C, hits_a, hits_c = public_a_private_c
    r = Fetcher(sleep=lambda s: None).get(f"{C}/admin/metrics")
    assert not r.ok and r.reason == "blocked_address" and hits_c == []


def test_rvf2_allow_private_is_the_only_way_through(public_a_private_c):
    A, C, hits_a, hits_c = public_a_private_c
    r = Fetcher(sleep=lambda s: None, allow_private=True).get(f"{A}/to-internal")
    assert r.ok and "INTERNAL-SERVICE-BODY" in r.text   # proves the refusal above is the guard, not a dead fixture


def test_rvf2_browser_path_refuses_redirect_onto_internal_service(public_a_private_c):
    pytest.importorskip("playwright.sync_api")
    A, C, hits_a, hits_c = public_a_private_c
    f = Fetcher(sleep=lambda s: None)
    assert f.get(f"{A}/plain.html", "browser").ok         # negative control
    r = f.get(f"{A}/to-internal", "browser")
    assert not r.ok and r.reason == "blocked_address"
    assert hits_c == []


def test_rvf4a_browser_hop_resolver_error_has_its_own_reason_but_still_refuses(public_a_private_c, monkeypatch):
    """Hop half of RVF-4a: a resolver error on a mid-redirect target is fail-closed and labelled
    preflight_unresolved; the same hop answering with a non-public address stays blocked_address."""
    pytest.importorskip("playwright.sync_api")
    A, C, hits_a, hits_c = public_a_private_c
    c_port = C.rsplit(":", 1)[1]
    real = fetch._preflight_reason
    monkeypatch.setattr(fetch, "_preflight_reason",
                        lambda u: "preflight_unresolved" if f":{c_port}/" in u else real(u))
    f = Fetcher(sleep=lambda s: None)
    assert f.get(f"{A}/plain.html", "browser").ok         # negative control
    r = f.get(f"{A}/to-internal", "browser")
    assert not r.ok and r.reason == "preflight_unresolved"
    assert hits_c == []                                   # refused before any request reached the target


def test_rvf2a_production_never_disables_the_address_guard():
    import inspect
    import cache_sources
    assert inspect.signature(Fetcher).parameters["allow_private"].default is False
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):      # every module in scripts/reverify/, not a hand-kept list
        if name.endswith(".py") and name not in ("test_reverify.py", "fetch.py"):
            assert "allow_private" not in open(os.path.join(here, name), encoding="utf-8").read(), name


def test_rvf3_connect_uses_the_validated_address_not_the_hostname(public_a_private_c, monkeypatch):
    """Anti-rebind pin (SecurityLab N5): the socket must be connected to an address taken from the answer
    that was validated, never to the hostname (which would resolve a second time). A resolution-count
    test cannot see this - socket.connect resolves in C - so record what connect() is actually given."""
    import socket
    A, C, hits_a, hits_c = public_a_private_c
    url = A.replace("127.0.0.1", "localhost")
    targets = []
    real_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (targets.append(addr), real_connect(self, addr))[1])
    assert Fetcher(sleep=lambda s: None).get(f"{url}/plain.html").ok
    assert targets, "connect() was never observed"
    for addr in targets:
        assert addr[0] != "localhost" and addr[0].replace(".", "").replace(":", "").isalnum(), addr
        assert addr[0] in ("127.0.0.1", "::1"), addr


def test_rvf3_one_private_answer_among_public_ones_refuses(monkeypatch):
    """SecurityLab N4: validate EVERY getaddrinfo answer, not just the first."""
    import socket
    real = socket.getaddrinfo
    mixed = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
             (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: mixed)
    with pytest.raises(fetch.BlockedAddress):
        fetch._guarded_create_connection(("example.test", 80), 1)
    monkeypatch.setattr(socket, "getaddrinfo", real)


def test_browser_preflight_fails_closed_on_unresolvable_host(monkeypatch):
    import socket

    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert fetch._url_resolves_public("http://unresolvable.invalid/x") is False


def test_browser_preflight_resolver_error_has_its_own_reason_but_still_refuses(monkeypatch):
    """RVF-4a: a resolver failure must stay fail-closed but not be logged as an address-POLICY refusal."""
    import socket

    f = Fetcher(sleep=lambda s: None)                    # production default: allow_private=False
    monkeypatch.setattr(f, "allowed", lambda u: True)    # isolate the pre-flight from the robots fetch

    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    r = f._get_browser("http://unresolvable.invalid/x")
    assert not r.ok and r.reason == "preflight_unresolved"

    def private(*a, **k):                                # positive control: a real policy refusal keeps its reason
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", private)
    r = f._get_browser("http://localhost-alias.invalid/x")
    assert not r.ok and r.reason == "blocked_address"


def test_load_bearing_optional_dependencies_are_installed():
    """Three tests importorskip these and guard the RVF-1A real-PDF control and both browser guards; a host
    without them would report green with those arms unexercised. Fail loudly instead."""
    import fitz  # noqa: F401
    import playwright.sync_api  # noqa: F401


# ---------------- SRC-41: only the issuing state's own host may confirm ----------------
MIRROR = "https://www.law.cornell.edu/regulations/x/1"


def test_src41_mirror_match_is_not_confirmed():
    r = {"field": "fee_usd", "checks": [chk(url=MIRROR)]}
    out = runner.judge_record(r, {"fee_usd": 150}, {0: ok(PAGE, url=MIRROR)})
    assert out["outcome"] == "FAILED"
    assert out["checks"][0]["status"] == "NONOFFICIAL_HOST"


def test_src41_grandfathered_mirror_still_confirms():
    r = {"field": "fee_usd", "checks": [chk(url=MIRROR, nonofficial_host_ok="SRC-41 grandfathered")]}
    assert runner.judge_record(r, {"fee_usd": 150}, {0: ok(PAGE, url=MIRROR)})["outcome"] == "CONFIRMED"


def test_src41_judges_the_host_that_served_the_bytes_not_the_recipe_url():
    # recipe points at an official host but it redirected to a mirror: the sha256 covers the mirror's page.
    r = {"field": "fee_usd", "checks": [chk(url="https://x.gov/a")]}
    f = Fetched("https://x.gov/a", True, 200, "", PAGE, "abc123", final_url=MIRROR)
    assert runner.judge_record(r, {"fee_usd": 150}, {0: f})["outcome"] == "FAILED"
    # ...and the reverse: a mirror URL that redirects to the state's own host is fine.
    r2 = {"field": "fee_usd", "checks": [chk(url=MIRROR)]}
    g = Fetched(MIRROR, True, 200, "", PAGE, "abc123", final_url="https://x.gov/a")
    assert runner.judge_record(r2, {"fee_usd": 150}, {0: g})["outcome"] == "CONFIRMED"


def test_src41_official_host_predicate():
    for u in ("https://adminrules.idaho.gov/a", "https://boa.state.mn.us/", "https://flrules.org/x",
              "https://www2.myfloridalicense.com/x", "https://www.flrules.org/gateway/x",
              "https://cpaboard.state.la.us/cpa-resources/", "https://secure.sos.state.or.us/x"):
        assert runner.is_official_host(u), u
    for u in ("https://www.law.cornell.edu/x", "https://nasba.org/", "https://x.gov.evil.com/", "https://evilgov/x", "https://notflrules.org/x", "",
              "https://cpa-mirror.us/rules", "https://nasba.us/x", "https://law.cornell.edu.us/x"):
        assert not runner.is_official_host(u), u


# Ratchet over the REAL recipes file: the grandfathered backlog may only shrink.
SRC41_GRANDFATHERED_CEILING = 38


def _all_checks():
    with open(runner.RECIPES, encoding="utf-8") as fh:
        recipes = json.load(fh)
    for rid, rc in recipes.items():
        for i, c in enumerate(rc.get("checks") or []):
            yield rid, i, c


def test_src41_every_nonofficial_check_is_explicitly_grandfathered():
    bad = []
    for rid, i, c in _all_checks():
        for u in [c["url"], *c.get("alt_urls", [])]:
            if not runner.is_official_host(u) and not c.get("nonofficial_host_ok"):
                bad.append((rid, i, u))
    assert not bad, f"new non-state check URL(s) in recipes (SRC-41): {bad[:5]}"


def test_src41_grandfather_ratchet_only_shrinks_and_has_no_stale_flags():
    flagged = [(rid, i) for rid, i, c in _all_checks() if c.get("nonofficial_host_ok")]
    assert len(flagged) <= SRC41_GRANDFATHERED_CEILING, "grandfathered mirror backlog grew; lower the ceiling as it shrinks, never raise it"
    stale = [(rid, i) for rid, i, c in _all_checks()
             if c.get("nonofficial_host_ok") and all(runner.is_official_host(u) for u in [c["url"], *c.get("alt_urls", [])])]
    assert not stale, f"flag left on an already-official check, delete it: {stale}"


# SRC-42 (second half): replay must re-assert the host guard, not trust a saved CONFIRMED.
def test_src42_replay_refuses_a_saved_mirror_confirmation(env, tmp_path):
    tmp, ff = env
    saved = runner.run(apply=False, all_records=True, fetcher=ff, today="2026-10-02")
    saved["results"]["a-fee"]["checks"][0]["url"] = MIRROR            # a results file from before the guard
    saved["results"]["a-fee"]["checks"][0].pop("final_url", None)
    sp = tmp_path / "results.json"
    sp.write_text(json.dumps(saved, default=str), encoding="utf-8")
    base_txt = (tmp / "data" / "renewal_fees.json").read_text(encoding="utf-8")
    base = lambda ds: json.loads(base_txt)["records"] if ds == "renewal_fees" else []
    assert runner.replay(str(sp), base_loader=base) == 0
    assert _recs(tmp)["a-fee"]["verified_date"] == "2026-09-01"        # not stamped


# SRC-43: a recipe with nothing to check confirms nothing.
def test_src43_zero_check_recipe_is_not_confirmed():
    out = runner.judge_record({"checks": []}, {}, {})
    assert out["outcome"] == "FAILED"


def test_src43_every_non_manual_recipe_has_at_least_one_check():
    with open(runner.RECIPES, encoding="utf-8") as fh:
        recipes = json.load(fh)
    empty = [rid for rid, rc in recipes.items() if not rc.get("manual") and not rc.get("checks")]
    assert not empty, f"non-manual recipe(s) with no checks would confirm on no evidence: {empty}"
