"""Offline tests for the monthly re-verification runner (no network).

    python -m pytest scripts/reverify -q
"""
import io
import json
import os
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
    assert st["stale_automatable"] == ["b-fee", "c-fee"] and st["verified_dates"]["a-fee"] == "2026-10-01"
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
