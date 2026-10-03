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
