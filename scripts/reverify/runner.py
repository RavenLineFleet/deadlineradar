"""Monthly deterministic re-verification of every DeadlineRadar record.

    python scripts/reverify/runner.py --all          # dry run over every record (acceptance); writes nothing
    python scripts/reverify/runner.py --apply        # DAILY task: re-verify records older than 20 days + failed
                                                     # retries; bump CONFIRMED, file CHANGED/FAILED, write status

Recipes live in data/reverify_recipes.json (schema: see RECIPE_DOC below). No LLM anywhere.

Outcomes per record (Orchestrator directive 2026-10-02):
  CONFIRMED  every check found its anchor and the extracted value equals the value stored in the
             data file (or the recipe's expected text is present). With --apply: verified date bumped,
             verified_method="auto-anchor", one prose line appended to verification_history.
             last_manual_verified_date is NEVER written.
  CHANGED    an anchor was found but the value next to it differs from the stored value.
             Never auto-edited; a note is filed to the AssetLab and AuditLab inboxes.
  FAILED     a fetch failed, robots disallowed it, or the anchor wasn't found. Retried on the next
             daily run; after 2 consecutive failures, a recipe-fix note is filed to AssetLab.
  MANUAL     the recipe says this record can't be automated (reason recorded). Never bumped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch import Fetcher, normalise  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "data")
RECIPES = os.path.join(DATA, "reverify_recipes.json")
STATE_DIR = os.environ.get("REVERIFY_STATE_DIR", r"C:\Users\Devin\Orchestrator\state")
INBOXES = {
    "assetlab": os.environ.get("REVERIFY_ASSETLAB_INBOX", r"C:\Users\Devin\AssetLab\inbox"),
    "auditlab": os.environ.get("REVERIFY_AUDITLAB_INBOX", r"C:\Users\Devin\AuditLab\inbox"),
}
DATASETS = ("cpa_deadlines", "cpe_hours", "reinstatement", "renewal_fees")
# which field holds the "verified" date per dataset (they differ; AssetLab owns the names)
DATE_FIELD = {"cpa_deadlines": "last_verified", "cpe_hours": "verified_date",
              "reinstatement": "last_verified", "renewal_fees": "verified_date"}
FAILS_BEFORE_ESCALATION = 2
DUE_DAYS = 20      # re-verify anything older than this (daily rolling run) -- the backstop, see TRANCHE
# STALE-27 (AuditLab/Orchestrator 2026-10-02 22:15): selecting only "older than DUE_DAYS" kept every record
# confirmed on the same day on the same date forever, so they all went stale together. Each daily run now
# ALSO re-verifies ceil(automatable/TRANCHE_CYCLE_DAYS) of the oldest records per dataset, so dates spread
# over a rolling TRANCHE_CYCLE_DAYS window (at most ~8 cpa_deadlines records share a date).
TRANCHE_CYCLE_DAYS = 10
STALE_DAYS = 25    # watchdog alert threshold

RECIPE_DOC = """
{ "<record id>": {
    "dataset": "renewal_fees",
    "manual": null | "<why this record cannot be checked automatically>",
    "combine": "each" | "sum",          # sum: the check values are added, then compared to `field`
    "field": "fee_usd" | null,          # data field the extracted value(s) must equal; null = text-only
    "derivation": "<human note, e.g. 12 AAC 04.440(a) = 02.340(4) $300 + (13) $100>",
    "checks": [ { "url": "...", "alt_urls": ["..."], "method": "http" | "pdf" | "browser",
                  "anchor": "<literal text located first>",
                  "window": 600,               # chars after the anchor to search
                  "pattern": "<regex with ONE group>" | null,
                  "expect": 300 | "<literal>" | null,   # per-check value (needed for sum)
                  "expect_text": "<literal that must appear in the window>" | null } ] } }
"""


# ---------------- judging (pure; unit-tested offline) ----------------
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
NUM_WORDS = "|".join(sorted([*_UNITS, *_TENS, "hundred"], key=len, reverse=True))
# "eighty", "one hundred twenty", "twenty-four" (statutes spell hours out; fees are always $digits)
WORD_NUM_RE = rf"(?:(?:{NUM_WORDS})(?:[\s-]+(?:and\s+)?(?:{NUM_WORDS}))*)"


def _words_to_int(s: str):
    total, cur, seen = 0, 0, False
    for w in re.split(r"[\s-]+", s.lower()):
        if w == "and" or not w:
            continue
        if w in _UNITS:
            cur += _UNITS[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        else:
            return None
        seen = True
    return total + cur if seen else None


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("$", "").strip()
    try:
        f = float(s)
    except ValueError:
        return _words_to_int(s)
    return int(f) if f.is_integer() else f


def _scaled(got, check: dict):
    """Extracted value in the record's unit. `divide` converts e.g. CPE minutes -> hours (50 min = 1 hour,
    NASBA), so '2,000 CPE minutes' verifies a stored 40 hours."""
    v = _num(got)
    d, m = check.get("divide"), check.get("multiply")
    if v is None or not (d or m):
        return v
    q = v * (m or 1) / (d or 1)       # multiply: e.g. FL 8 ethics hours per set x 2 sets = 16
    return int(q) if float(q).is_integer() else q


def judge_check(check: dict, text: str | None) -> tuple[str, object]:
    """-> (status, extracted). status in MATCH, DIFFERENT, NO_ANCHOR, AMBIGUOUS_ANCHOR, NO_TEXT."""
    if not text:
        return "NO_TEXT", None
    anchor = normalise(check["anchor"]).lower()
    low = text.lower()
    i = low.find(anchor)
    if i < 0:
        return "NO_ANCHOR", None
    # AuditLab 10-02: an anchor that occurs more than once is pinned by document order, not content
    # (NH's row label occurred 98x with 27 amounts). Fail closed instead of trusting the first hit.
    if low.find(anchor, i + 1) >= 0:
        if not check.get("duplicated_source"):
            return "AMBIGUOUS_ANCHOR", None
        # explicitly flagged: the source repeats the same passage verbatim (e.g. a rule rendered twice).
        # Accept only if EVERY occurrence yields the same result; any disagreement is still ambiguous.
        results, j = set(), i
        sub = dict(check, duplicated_source=False)
        while j >= 0:
            nxt = low.find(anchor, j + 1)
            end = j + len(anchor) + int(check.get("window", 600))
            results.add(judge_check(sub, text[j: end if nxt < 0 else min(end, nxt)]))   # each copy judged alone
            j = nxt
        return results.pop() if len(results) == 1 else ("AMBIGUOUS_ANCHOR", None)
    window = text[i: i + len(anchor) + int(check.get("window", 600))]
    if check.get("expect_text"):
        want = normalise(check["expect_text"]).lower()
        return ("MATCH" if want in window.lower() else "DIFFERENT"), check["expect_text"]
    # value search starts AFTER the anchor: digits inside the anchor itself (e.g. "(2)") must never match
    m = re.search(check["pattern"], window[len(anchor):], flags=re.I | re.S)
    if not m:
        return "DIFFERENT", None   # the anchor is there but the value shape next to it isn't
    got = m.group(1)
    want = check.get("expect")
    if want is None:
        return "MATCH", got        # value is compared at record level (field)
    same = (_scaled(got, check) == _num(want)) if _num(want) is not None else (normalise(got).lower() == normalise(str(want)).lower())
    return ("MATCH" if same else "DIFFERENT"), got


def judge_record(recipe: dict, stored: dict, fetched: dict[int, object]) -> dict:
    """fetched: check index -> Fetched-like (needs .ok .text .url .sha256 .reason)."""
    if recipe.get("manual"):
        return {"outcome": "MANUAL", "detail": recipe["manual"]}
    per = []
    for idx, chk in enumerate(recipe["checks"]):
        f = fetched.get(idx)
        if f is None or not f.ok:
            per.append({"i": idx, "status": "FETCH_FAILED", "reason": getattr(f, "reason", "not_fetched"),
                        "url": getattr(f, "url", chk["url"])})
            continue
        st, got = judge_check(chk, f.text)
        per.append({"i": idx, "status": st, "got": got, "url": f.url, "sha256": f.sha256})
    if any(p["status"] == "DIFFERENT" for p in per):
        return {"outcome": "CHANGED", "checks": per}
    if any(p["status"] in ("FETCH_FAILED", "NO_ANCHOR", "NO_TEXT", "AMBIGUOUS_ANCHOR") for p in per):
        return {"outcome": "FAILED", "checks": per}
    field = recipe.get("field")
    if field:
        have = _num(stored.get(field))
        if recipe.get("combine") == "sum":
            # only checks WITHOUT their own field are summed into `field`; fielded checks compare alone below
            # text checks (expect_text) guard the formula's wording and are never summed
            vals = [_scaled(p["got"], c) for p, c in zip(per, recipe["checks"]) if not c.get("field") and not c.get("expect_text")]
            got_val = sum(vals) if vals and None not in vals else None
            if got_val != have:
                return {"outcome": "CHANGED", "checks": per, "detail": f"sum {got_val} != stored {field}={have}"}
    # "each": every check compares to its own field (check.field) or the record-level field
    for p, chk in zip(per, recipe["checks"]):
        f = chk.get("field") or (field if recipe.get("combine") != "sum" else None)
        if f and chk.get("pattern") and _scaled(p["got"], chk) != _num(stored.get(f)):
            return {"outcome": "CHANGED", "checks": per,
                    "detail": f"check {p['i']}: extracted {p['got']!r} != stored {f}={stored.get(f)!r}"}
    return {"outcome": "CONFIRMED", "checks": per}


def history_line(day: str, check_results: list[dict], recipe: dict) -> str:
    """The exact prose format Orchestrator/AssetLab approved (2026-10-02 12:23)."""
    parts = []
    for p in check_results:
        anchor = recipe["checks"][p["i"]]["anchor"].replace("'", "\u2019")
        parts.append(f"confirmed via {p['url']}, sha256={p['sha256']}, anchor='{anchor}'")
    return f"{day} (automated re-verification, auto-anchor): " + "; ".join(parts)


# AuditLab STALE-21 ruling (2026-10-02 16:15): a manual_verify_gap_reason means "the old manual-verify
# pass did NOT confirm this". An auto-anchor CONFIRMED makes that marker false -- but ONLY when the reason
# was a tooling/fetch failure (the old fetcher couldn't see the page). Substantive reasons (sources
# disagree, rule may be superseded, needs judgment) are never cleared by an anchor match: the record is
# HELD for a human and its date does not move.
# STALE-25 (AuditLab 2026-10-02): free-text matching of gap reasons cannot be made safe -- substantive
# reasons naturally contain "was not re-confirmed", "claim-anchor", "cannot read". So the runner no longer
# reads the prose at all. A gap marker is clearable ONLY if it carries the structured field
# manual_verify_gap_kind == "tooling", which the tool that writes tooling failures sets (AssetLab's
# manual-verify tool). Anything else -- hand-written, legacy, or unmarked -- is HELD for a human by
# construction. (Closed-world: an unmarked reason can never be silently cleared.)
TOOLING_KIND = "tooling"


def gap_is_tooling(rec_or_reason) -> bool:
    """True only for a record whose gap marker is structurally declared a tooling failure."""
    if not isinstance(rec_or_reason, dict):
        return False           # bare prose is never enough (STALE-25)
    return bool(rec_or_reason.get("manual_verify_gap_reason")) and \
        rec_or_reason.get("manual_verify_gap_kind") == TOOLING_KIND


def apply_confirmed(rec: dict, dataset: str, line: str, day: str) -> bool:
    """Bump the record. Returns False (and changes nothing) if a substantive gap marker must hold it."""
    gap = rec.get("manual_verify_gap_reason")
    if gap and not gap_is_tooling(rec):
        return False
    if gap:   # declared tooling failure now resolved by a real confirmation: clear it, quoting it in the history
        rec["manual_verify_gap_reason"] = None
        rec["manual_verify_gap_kind"] = None
        line = f"{line}; cleared manual_verify_gap_reason (tooling failure, resolved by this confirmation): '{gap}'"
    rec[DATE_FIELD[dataset]] = day
    rec["verified_method"] = "auto-anchor"
    prev = rec.get("verification_history")
    rec["verification_history"] = (prev.rstrip() + "\n\n" + line) if isinstance(prev, str) and prev.strip() else line
    return True


# ---------------- io ----------------
def _load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _note(inbox_key, slug, body, now):
    d = INBOXES[inbox_key]
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"reverify_{now:%Y%m%d_%H%M%S}_{slug}")
    p, n = base + ".md", 2
    while os.path.exists(p):          # never overwrite an earlier note filed in the same second
        p, n = f"{base}_{n}.md", n + 1
    with open(p, "x", encoding="utf-8") as f:
        f.write(body)
    return p


def _age_days(rec: dict, dataset: str, today: date) -> int:
    v = rec.get(DATE_FIELD[dataset])
    try:
        return (today - date.fromisoformat(str(v)[:10])).days
    except ValueError:
        return 10_000          # no/invalid date: treat as maximally stale (always due)


def tranche(automatable: list[str], by_id: dict, today: date) -> list[str]:
    """Per dataset, the ceil(n/TRANCHE_CYCLE_DAYS) oldest automatable records; ties broken by sha256(id) so
    the order is stable from run to run and does not follow alphabetical (state-clustered) order."""
    picked = []
    for ds in DATASETS:
        ids = [i for i in automatable if by_id[i][0] == ds]
        ids.sort(key=lambda i: (-_age_days(by_id[i][1], ds, today), hashlib.sha256(i.encode()).hexdigest()))
        picked += ids[:math.ceil(len(ids) / TRANCHE_CYCLE_DAYS)]
    return picked


def advance_as_of(data: dict) -> str | None:
    """STALE-27 ruling (Orchestrator 2026-10-02 22:17): cpa_deadlines.as_of_date = the OLDEST record
    last_verified, forward-only. It can never read fresher than the data under it (STALE-5) and is never
    bulk-bumped. Any missing/unparseable record date -> no move (fail toward the older stamp)."""
    cpa = data["cpa_deadlines"]
    try:
        oldest = min(date.fromisoformat(str(r.get("last_verified"))[:10]) for r in cpa["records"])
        cur = date.fromisoformat(str(cpa.get("as_of_date"))[:10])
    except ValueError:
        return None
    if oldest > cur:
        cpa["as_of_date"] = oldest.isoformat()
        return cpa["as_of_date"]
    return None


def _excerpt_line(rec, ds, urls, fetcher, excerpter) -> str:
    """One ticket line: a model-picked, code-verified verbatim excerpt with provenance, or why there isn't one.
    Robots-disallowed sources are never fetched (the model doesn't change what we may fetch)."""
    import llm_assist
    for u in urls:
        if not str(u).startswith("http"):
            continue
        u = str(u).split("#")[0]
        try:
            if not fetcher.allowed(u):
                return f"  excerpt: none -- {u} disallows automated fetching (robots.txt); read it by hand"
        except Exception:
            pass
        f = fetcher.get(u, "pdf" if u.lower().endswith(".pdf") else "http")
        if not f.ok:
            continue
        ex = excerpter(rec, ds, f.text, f.url, f.sha256)
        if ex:
            return "  excerpt (verify against the source; offset is in the normalised page text):\n" + llm_assist.render(ex)
        return f"  excerpt: none -- the model found no verbatim passage on {u}"
    return "  excerpt: none -- no source could be fetched"


def run(apply: bool, all_records: bool = False, fetcher=None, today: str | None = None,
        ids: list[str] | None = None, excerpter=None) -> dict:
    """Daily rolling mode (Orchestrator 2026-10-02 12:43, from AuditLab STALE-23; tranches STALE-27): re-verify
    today's tranche, every automatable record older than DUE_DAYS, and any with pending failures.
    all_records=True checks every record (acceptance dry run)."""
    now = datetime.now(timezone.utc).astimezone()
    day = today or date.today().isoformat()
    tday = date.fromisoformat(day)
    recipes = _load(RECIPES)
    data = {ds: _load(os.path.join(DATA, ds + ".json")) for ds in DATASETS}
    by_id = {r["id"]: (ds, r) for ds in DATASETS for r in data[ds]["records"]}
    if apply:
        os.makedirs(STATE_DIR, exist_ok=True)
    status_path = os.path.join(STATE_DIR, "reverify_status.json")
    prev = _load(status_path) if os.path.exists(status_path) else {}
    fail_counts = dict(prev.get("fail_counts", {}))
    changed_open = dict(prev.get("changed_open", {}))   # rid -> extracted-value signature already notified
    fetcher = fetcher or Fetcher()
    if excerpter is None and os.environ.get("REVERIFY_LLM_EXCERPTS") == "1":
        import llm_assist
        excerpter = llm_assist.excerpt_for       # opt-in; the job sets it for real runs
    results, missing_recipe = {}, sorted(set(by_id) - set(recipes))
    automatable = sorted(i for i in by_id if i in recipes and not recipes[i].get("manual"))
    if ids:
        todo = ids
    elif all_records:
        todo = sorted(set(by_id) & set(recipes))
    else:
        due = set(tranche(automatable, by_id, tday))
        todo = [i for i in automatable if i in due or _age_days(by_id[i][1], by_id[i][0], tday) > DUE_DAYS
                or i in fail_counts]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for rid in todo:
        ds, rec = by_id[rid]
        recipe = recipes[rid]
        fetched = {}
        if not recipe.get("manual"):
            for idx, chk in enumerate(recipe["checks"]):
                f = None
                for u in [chk["url"], *chk.get("alt_urls", [])]:
                    f = fetcher.get(u, chk.get("method", "http"))
                    if f.ok:
                        break
                fetched[idx] = f
        res = judge_record(recipe, rec, fetched)
        res["dataset"] = ds
        results[rid] = res
        if not apply:
            continue
        if res["outcome"] == "CONFIRMED":
            if apply_confirmed(rec, ds, history_line(day, res["checks"], recipe), day):
                fail_counts.pop(rid, None)
                changed_open.pop(rid, None)
            else:
                res["outcome"] = "HELD"
                res["detail"] = f"source confirms, but a substantive manual_verify_gap_reason holds it: {rec.get('manual_verify_gap_reason')!r}"
                sig = "HELD:" + str(rec.get("manual_verify_gap_reason"))
                if changed_open.get(rid) != sig:      # one finding per distinct hold, not one per day
                    changed_open[rid] = sig
                    _note("assetlab", f"HELD_{rid}",
                          f"---\nfrom: reverify-runner\nkind: finding\nneeds_devin: no\n"
                          f"summary: {rid} confirmed at source but HELD by a substantive gap reason; date NOT moved\n---\n"
                          f"{res['detail']}\nA human must resolve the gap reason (STALE-21 ruling, AuditLab 2026-10-02).\n", now)
        elif res["outcome"] == "CHANGED":
            sig = json.dumps([p.get("got") for p in res["checks"]], default=str)
            if changed_open.get(rid) != sig:          # one note per distinct change, not one per day
                changed_open[rid] = sig
                body = (f"---\nfrom: reverify-runner\nkind: alert\nneeds_devin: no\n"
                        f"summary: {rid} source value CHANGED; record NOT edited\n---\n"
                        f"{json.dumps(res, indent=2, default=str)}\n")
                _note("assetlab", f"CHANGED_{rid}", body, now)
                _note("auditlab", f"CHANGED_{rid}", body, now)
        elif res["outcome"] == "FAILED":
            n = fail_counts.get(rid, 0) + 1
            fail_counts[rid] = n
            if n == FAILS_BEFORE_ESCALATION:
                _note("assetlab", f"RECIPE_FIX_{rid}",
                      f"---\nfrom: reverify-runner\nkind: finding\nneeds_devin: no\n"
                      f"summary: {rid} failed {n} runs in a row; please fix its recipe\n---\n"
                      f"{json.dumps(res, indent=2, default=str)}\n", now)
    # MANUAL records can't be fetched, but they still go stale: once one is older than DUE_DAYS, the
    # daily run files a re-verification ticket to AssetLab (one ticket per run, listing every newly-due
    # manual record), so a fleet worker re-checks it every month without a human trigger
    # (Orchestrator 2026-10-02 12:47). Deduped per record per verified date.
    manual_notified = dict(prev.get("manual_notified", {}))
    manual_due = []
    for i in sorted(by_id):
        if i not in recipes or not recipes[i].get("manual"):
            continue
        ds, rec = by_id[i]
        vd = str(rec.get(DATE_FIELD[ds]) or "")
        if _age_days(rec, ds, tday) > DUE_DAYS and manual_notified.get(i) != vd:
            manual_due.append((i, ds, rec, vd))
    if apply and manual_due and not ids:
        lines = [f"---\nfrom: reverify-runner\nkind: finding\nneeds_devin: no\n"
                 f"summary: {len(manual_due)} MANUAL record(s) older than {DUE_DAYS} days need a hand re-verification\n---\n"
                 f"These records can't be checked automatically (reason per record). Please re-verify each against its "
                 f"source and update its verified date + verification_history; the reverify watchdog alerts at {STALE_DAYS} days.\n"]
        for i, ds, rec, vd in manual_due:
            urls = [rec.get(k) for k in ("source_url", "citation_url", "secondary_source_url") if rec.get(k)]
            lines.append(f"- **{i}** ({ds}, verified {vd or 'never'}): {recipes[i]['manual']}\n  sources: {urls}")
            if excerpter:
                lines.append(_excerpt_line(rec, ds, urls, fetcher, excerpter))
            manual_notified[i] = vd
        _note("assetlab", f"MANUAL_DUE_{len(manual_due)}", "\n".join(lines) + "\n", now)
    as_of_moved = advance_as_of(data) if apply else None
    if apply and (results or as_of_moved):
        for ds in DATASETS:
            _dump(os.path.join(DATA, ds + ".json"), data[ds])
    counts = {}
    for r in results.values():
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    # Devin's bar (Orchestrator 10-02 12:48): EVERY record is watched, automated or MANUAL
    verified = {i: str(by_id[i][1].get(DATE_FIELD[by_id[i][0]]) or "") for i in sorted(by_id)}
    ages = {i: _age_days(by_id[i][1], by_id[i][0], tday) for i in sorted(by_id)}
    report = {"mode": "apply" if apply else "dry-run", "selection": "ids" if ids else ("all" if all_records else "due"),
              "run_date": day, "started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "total_records": len(by_id), "checked": len(todo), "counts": counts, "as_of_moved_to": as_of_moved,
              "missing_recipe": missing_recipe,
              "manual_ids": sorted(i for i in by_id if recipes.get(i, {}).get("manual")),
              "not_confirmed_this_run": sorted(i for i, r in results.items() if r["outcome"] != "CONFIRMED"),
              "stale_records": sorted(i for i, a in ages.items() if a > STALE_DAYS),
              "oldest_record_age_days": max(ages.values()) if ages else None,
              "verified_dates": verified,
              "fail_counts": fail_counts, "changed_open": changed_open,
              "manual_notified": manual_notified,
              "manual_due_filed_this_run": [m[0] for m in manual_due] if apply and not ids else []}
    if apply:
        _dump(status_path, report)
    return {"report": report, "results": results}


REPLAY_WRITES = {"verified_date", "last_verified", "verified_method", "verification_history",
                 "manual_verify_gap_reason", "manual_verify_gap_kind"}


def _git_base_loader(base: str):
    import subprocess
    def load(ds):
        out = subprocess.run(["git", "show", f"{base}:data/{ds}.json"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout
        return json.loads(out)["records"]
    return load


def replay(results_path: str, today: str | None = None, base: str | None = None, base_loader=None) -> int:
    """Re-apply a finished run's CONFIRMED records onto freshly-reset data. The fetch evidence (url, sha256,
    anchor) and the run date come from the saved results, so the history line is identical to the original.
    REPLAY-1 (AuditLab 2026-10-02): a record is bumped ONLY if it is unchanged since the commit the fetch
    ran against -- every field compared except the ones replay itself writes -- so a correction landed on
    main mid-run is never stamped as verified. This covers all recipes, text-only ones included. Without a
    base the replay refuses to run (fail closed)."""
    loader = base_loader or (_git_base_loader(base) if base else None)
    if loader is None:
        raise SystemExit("replay needs --base <commit the fetch ran against> (REPLAY-1: fail closed)")
    base_recs = {r["id"]: r for ds in DATASETS for r in loader(ds)}
    with open(results_path, encoding="utf-8") as f:
        saved = json.load(f)
    day = today or saved["report"]["run_date"]
    recipes = _load(RECIPES)
    data = {ds: _load(os.path.join(DATA, ds + ".json")) for ds in DATASETS}
    by_id = {r["id"]: (ds, r) for ds in DATASETS for r in data[ds]["records"]}
    n = 0
    for rid, res in saved["results"].items():
        if res.get("outcome") != "CONFIRMED" or rid not in by_id or rid not in recipes:
            continue
        ds, rec = by_id[rid]
        b = base_recs.get(rid)
        strip = lambda r: {k: v for k, v in r.items() if k not in REPLAY_WRITES}
        if b is None or strip(b) != strip(rec):
            continue                      # main changed (or added) this record mid-run: not ours to stamp
        # belt and braces: also re-judge numeric values against the CURRENT stored values (no network)
        stored_ok = True
        rcp = recipes[rid]
        for p, chk in zip(res["checks"], rcp["checks"]):
            # same field rule as judge_record: the check's own field, else the record field (not in sum mode)
            f = chk.get("field") or (rcp.get("field") if rcp.get("combine") != "sum" else None)
            if f and chk.get("pattern") and _scaled(p.get("got"), chk) != _num(rec.get(f)):
                stored_ok = False
        if recipes[rid].get("combine") == "sum" and recipes[rid].get("field"):
            vals = [_scaled(p.get("got"), c) for p, c in zip(res["checks"], recipes[rid]["checks"])
                    if not c.get("field") and not c.get("expect_text")]
            if None in vals or sum(vals) != _num(rec.get(recipes[rid]["field"])):
                stored_ok = False
        if stored_ok and apply_confirmed(rec, ds, history_line(day, res["checks"], recipes[rid]), day):
            n += 1
    advance_as_of(data)               # same rule as run(): follows the oldest record, forward-only
    for ds in DATASETS:
        _dump(os.path.join(DATA, ds + ".json"), data[ds])
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true", help="check every record, not just the due ones")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--out", help="write full per-record results JSON here")
    ap.add_argument("--sample", type=int, help="print N random CONFIRMED ids (AuditLab spot-check)")
    ap.add_argument("--replay", help="re-apply the CONFIRMED results saved by an earlier --out onto the current "
                                     "data (no fetching, no notes, no status); used by job.py after a lost push race")
    ap.add_argument("--base", help="with --replay: the commit the original fetch ran against (REPLAY-1)")
    a = ap.parse_args(argv)
    if a.replay:
        n = replay(a.replay, base=a.base)
        print(json.dumps({"REPLAYED": n}))
        return 0
    out = run(apply=a.apply, all_records=a.all, ids=a.ids)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
    print(json.dumps(out["report"]["counts"]), "missing_recipe:", len(out["report"]["missing_recipe"]))
    if a.sample:
        conf = [i for i, r in out["results"].items() if r["outcome"] == "CONFIRMED"]
        print("spot-check:", sorted(random.sample(conf, min(a.sample, len(conf)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
