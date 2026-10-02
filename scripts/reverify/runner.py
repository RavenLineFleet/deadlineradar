"""Monthly deterministic re-verification of every DeadlineRadar record.

    python scripts/reverify/runner.py            # dry run: fetch + judge, write nothing but a report
    python scripts/reverify/runner.py --apply    # live: bump CONFIRMED, file CHANGED/FAILED, write status
    python scripts/reverify/runner.py --retry    # live, only ids not yet CONFIRMED this cycle (daily task)

Recipes live in data/reverify_recipes.json (schema: see RECIPE_DOC below). No LLM anywhere.

Outcomes per record (Orchestrator directive 2026-10-02):
  CONFIRMED  every check found its anchor and the extracted value equals the value stored in the
             data file (or the recipe's expected text is present). With --apply: verified date bumped,
             verified_method="auto-anchor", one prose line appended to verification_history.
             last_manual_verified_date is NEVER written.
  CHANGED    an anchor was found but the value next to it differs from the stored value.
             Never auto-edited; a note is filed to the AssetLab and AuditLab inboxes.
  FAILED     a fetch failed, robots disallowed it, or the anchor wasn't found. Retried daily; after
             2 consecutive failures, a recipe-fix note is filed to AssetLab.
  MANUAL     the recipe says this record can't be automated (reason recorded). Never bumped.
"""
from __future__ import annotations

import argparse
import json
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


def judge_check(check: dict, text: str | None) -> tuple[str, object]:
    """-> (status, extracted). status in MATCH, DIFFERENT, NO_ANCHOR, NO_TEXT."""
    if not text:
        return "NO_TEXT", None
    anchor = normalise(check["anchor"]).lower()
    low = text.lower()
    i = low.find(anchor)
    if i < 0:
        return "NO_ANCHOR", None
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
    same = (_num(got) == _num(want)) if _num(want) is not None else (normalise(got).lower() == normalise(str(want)).lower())
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
    if any(p["status"] in ("FETCH_FAILED", "NO_ANCHOR", "NO_TEXT") for p in per):
        return {"outcome": "FAILED", "checks": per}
    field = recipe.get("field")
    if field:
        have = _num(stored.get(field))
        if recipe.get("combine") == "sum":
            # only checks WITHOUT their own field are summed into `field`; fielded checks compare alone below
            # text checks (expect_text) guard the formula's wording and are never summed
            vals = [_num(p["got"]) for p, c in zip(per, recipe["checks"]) if not c.get("field") and not c.get("expect_text")]
            got_val = sum(vals) if vals and None not in vals else None
            if got_val != have:
                return {"outcome": "CHANGED", "checks": per, "detail": f"sum {got_val} != stored {field}={have}"}
    # "each": every check compares to its own field (check.field) or the record-level field
    for p, chk in zip(per, recipe["checks"]):
        f = chk.get("field") or (field if recipe.get("combine") != "sum" else None)
        if f and chk.get("pattern") and _num(p["got"]) != _num(stored.get(f)):
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


def apply_confirmed(rec: dict, dataset: str, line: str, day: str) -> None:
    rec[DATE_FIELD[dataset]] = day
    rec["verified_method"] = "auto-anchor"
    prev = rec.get("verification_history")
    rec["verification_history"] = (prev.rstrip() + "\n\n" + line) if isinstance(prev, str) and prev.strip() else line


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
    p = os.path.join(d, f"reverify_{now:%Y%m%d_%H%M%S}_{slug}.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    return p


def run(apply: bool, retry_only: bool, fetcher=None, today: str | None = None, ids: list[str] | None = None) -> dict:
    now = datetime.now(timezone.utc).astimezone()
    day = today or date.today().isoformat()
    cycle = day[:7]
    recipes = _load(RECIPES)
    data = {ds: _load(os.path.join(DATA, ds + ".json")) for ds in DATASETS}
    by_id = {r["id"]: (ds, r) for ds in DATASETS for r in data[ds]["records"]}
    if apply:
        os.makedirs(STATE_DIR, exist_ok=True)
    status_path = os.path.join(STATE_DIR, "reverify_status.json")
    prev = _load(status_path) if os.path.exists(status_path) else {}
    if prev.get("cycle") != cycle:
        prev = {"cycle": cycle, "fail_counts": {}, "confirmed": []}
    fetcher = fetcher or Fetcher()
    results, missing_recipe = {}, sorted(set(by_id) - set(recipes))
    todo = ids or sorted(set(by_id) & set(recipes))
    if retry_only:
        todo = [i for i in todo if i not in set(prev.get("confirmed", []))]
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
            apply_confirmed(rec, ds, history_line(day, res["checks"], recipe), day)
            prev["fail_counts"].pop(rid, None)
            prev["confirmed"] = sorted(set(prev.get("confirmed", [])) | {rid})
        elif res["outcome"] == "CHANGED":
            body = (f"---\nfrom: reverify-runner\nkind: data-change\nneeds_devin: no\n"
                    f"summary: {rid} source value CHANGED; record NOT edited\n---\n"
                    f"{json.dumps(res, indent=2, default=str)}\n")
            _note("assetlab", f"CHANGED_{rid}", body, now)
            _note("auditlab", f"CHANGED_{rid}", body, now)
        elif res["outcome"] == "FAILED":
            n = prev["fail_counts"].get(rid, 0) + 1
            prev["fail_counts"][rid] = n
            if n == FAILS_BEFORE_ESCALATION:
                _note("assetlab", f"RECIPE_FIX_{rid}",
                      f"---\nfrom: reverify-runner\nkind: recipe-fix\nneeds_devin: no\n"
                      f"summary: {rid} failed {n} runs in a row; please fix its recipe\n---\n"
                      f"{json.dumps(res, indent=2, default=str)}\n", now)
    if apply:
        for ds in DATASETS:
            _dump(os.path.join(DATA, ds + ".json"), data[ds])
    counts = {}
    for r in results.values():
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    unconfirmed = sorted(set(by_id) - set(prev.get("confirmed", []))) if apply else \
        sorted(i for i, r in results.items() if r["outcome"] != "CONFIRMED")
    report = {"cycle": cycle, "mode": "apply" if apply else "dry-run", "retry_only": retry_only,
              "started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "total_records": len(by_id), "checked": len(todo), "counts": counts,
              "missing_recipe": missing_recipe, "unconfirmed_ids": unconfirmed,
              "fail_counts": prev.get("fail_counts", {}), "confirmed": prev.get("confirmed", [])}
    if apply:
        _dump(status_path, report)
    return {"report": report, "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--out", help="write full per-record results JSON here")
    ap.add_argument("--sample", type=int, help="print N random CONFIRMED ids (AuditLab spot-check)")
    a = ap.parse_args(argv)
    out = run(apply=a.apply, retry_only=a.retry, ids=a.ids)
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
