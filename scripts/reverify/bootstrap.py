"""One-time recipe proposer (authoring aid, NOT part of the monthly run).

    python scripts/reverify/bootstrap.py <cache_dir> <out_proposals.json>

For every record without a recipe, for each verifiable field it:
  1. finds every occurrence of the stored value in the cached text of the record's URLs
     (official .gov / board sources first, Cornell LII last, per Orchestrator 10-02);
  2. scores each by field keywords in the 120 chars before it;
  3. takes the best one, builds a short anchor ending just before the value, and keeps it only
     if the anchor is unique in that document AND runner.judge_check() returns MATCH on it.
Records whose numbers are all null use quoted board text ('...') from their own prose fields,
when that quote appears verbatim at the source (expect_text mode).
Every proposal records its provenance + score; anything with score 0 or no hit is listed under
"needs_hand" for manual authoring. Nothing here writes to data/.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner  # noqa: E402
from fetch import normalise  # noqa: E402

FIELDS = {
    "renewal_fees": [("fee_usd", "fee")],
    "cpe_hours": [("total_hours", "hours"), ("ethics_hours", "hours"), ("annual_minimum_hours", "hours")],
    "reinstatement": [("reinstatement_fee_usd", "fee"), ("penalty_cpe_hours", "hours"), ("penalty_ethics_hours", "hours")],
    "cpa_deadlines": [],
}
KEYWORDS = {
    "fee_usd": ["renew", "biennial", "annual", "license fee", "certificate", "permit"],
    "total_hours": ["total", "hours of cpe", "hours of continuing", "no fewer than", "no less than", "complete", "period", "biennial", "triennial", "renewal"],
    "ethics_hours": ["ethic"],
    "annual_minimum_hours": ["each year", "annual", "minimum", "per year", "calendar year"],
    "reinstatement_fee_usd": ["reinstat", "reactivat", "lapsed", "late", "delinquent", "restor"],
    "penalty_cpe_hours": ["reinstat", "reactivat", "lapsed", "restor", "inactive"],
    "penalty_ethics_hours": ["ethic"],
}
NEGATIVE = {  # wording that marks a different quantity than the one stored
    "ethics_hours": ["maximum", "no more than", "carry", "self-study"],
    "total_hours": ["maximum", "no more than", "carry", "self-study", "ethic"],
    "fee_usd": ["reinstat", "late", "penalty", "initial", "exam"],
}
PATTERN = {"fee": r"\$\s*([\d,]+(?:\.\d\d)?)", "hours": rf"\b({runner.WORD_NUM_RE}|\d{{1,3}})\b"}

_U = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
      "sixteen seventeen eighteen nineteen").split()
_T = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def to_words(n: int) -> str:
    """Regex for the spelled-out form of n: 120 -> 'one\\s+hundred(?:\\s+and)?\\s+twenty'."""
    if n < 20:
        return _U[n]
    if n < 100:
        return _T[n // 10] + ("" if n % 10 == 0 else r"[\s-]+" + _U[n % 10])
    rest = n % 100
    return _U[n // 100] + r"\s+hundred" + ("" if rest == 0 else r"(?:\s+and)?\s+" + to_words(rest))
URL_FIELDS = ("citation_url", "source_url", "secondary_source_url")


def value_regex(kind, v):
    n = int(v) if float(v).is_integer() else v
    s = f"{n:,}" if isinstance(n, int) else str(n)
    alts = {re.escape(s), re.escape(str(n))}
    if kind == "hours" and isinstance(n, int) and 0 < n < 1000:
        alts.add(to_words(n))
    if kind == "fee":
        return r"\$\s*(?:" + "|".join(alts) + r")(?:\.00)?(?![\d,])"
    # "80 hours", "(80) hours", "80 CPE hours", "80 credit hours", "80 hours of continuing", "80 credits"
    return (r"(?<![\d.$])\(?(?:" + "|".join(alts) + r")\)?(?!\d)"
            r"(?=\s*(?:[A-Za-z-]+\s+){0,3}?(?:hours?|credits?)\b)")


def url_rank(u):
    h = urlparse(u).netloc
    return (2 if "law.cornell.edu" in h else 0 if (h.endswith(".gov") or ".state." in h or h.endswith(".us")) else 1)


def make_anchor(text, start):
    """Shortest tail (>=24 chars, word-aligned) of the text before `start` that is unique in text."""
    for n in (24, 36, 50, 70, 100):
        a = text[max(0, start - n):start]
        a = a[a.find(" ") + 1:] if " " in a[:-1] and start - n > 0 else a
        a = a.rstrip()
        if len(a) >= 12 and text.lower().count(a.lower()) == 1:
            return a
    return None


def propose_numeric(rec, field, kind, docs):
    v = rec.get(field)
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    best = None
    for url, text in docs:
        for m in re.finditer(value_regex(kind, v), text, flags=re.I):
            ctx = text[max(0, m.start() - 120):m.end() + 80].lower()   # keywords often follow ("4 hours of ethics")
            score = sum(1 for k in KEYWORDS[field] if k in ctx)
            if any(k in ctx for k in NEGATIVE.get(field, ())):
                score -= 2
            if best is None or (score, -url_rank(url)) > (best[0], -url_rank(best[1])):
                best = (score, url, text, m)
    if not best:
        return {"field": field, "status": "value_not_found", "value": v}
    score, url, text, m = best
    anchor = make_anchor(text, m.start())
    if not anchor:
        return {"field": field, "status": "no_unique_anchor", "value": v, "url": url}
    gap = m.end() - m.start()
    check = {"url": url, "alt_urls": [], "method": "pdf" if url.lower().endswith(".pdf") else "http",
             "anchor": anchor, "window": gap + 15, "pattern": PATTERN[kind], "field": field,
             "_score": score, "_context": text[max(0, m.start() - 80):m.end() + 40]}
    st, got = runner.judge_check(check, text)
    if st != "MATCH" or runner._num(got) != runner._num(v):
        return {"field": field, "status": f"selftest_{st}", "value": v, "got": got, "url": url}
    return {"field": field, "status": "ok", "check": check}


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}})\b")
DEADLINE_WORDS = ["expire", "renew", "due", "deadline", "last day", "on or before", "lapse"]


def propose_deadline(rec, docs):
    """cpa_deadlines: the first 'Month DD' in cycle_description is the renewal date; find it at the
    source near expire/renew wording and check that exact date text follows a unique anchor."""
    m0 = DATE_RE.search(str(rec.get("cycle_description") or ""))
    if not m0:
        return None, "no_date_in_cycle_description"
    want = f"{m0.group(1)} {int(m0.group(2))}"
    best = None
    rx = re.compile(rf"\b{m0.group(1)}\s+0?{int(m0.group(2))}(?!\d)", re.I)
    for url, text in docs:
        for m in rx.finditer(text):
            ctx = text[max(0, m.start() - 150):m.end() + 80].lower()
            score = sum(1 for k in DEADLINE_WORDS if k in ctx)
            if best is None or (score, -url_rank(url)) > (best[0], -url_rank(best[1])):
                best = (score, url, text, m)
    if not best or best[0] == 0:
        return None, f"date_not_found_near_deadline_words ({want})"
    score, url, text, m = best
    anchor = make_anchor(text, m.start())
    if not anchor:
        return None, "no_unique_anchor"
    check = {"url": url, "alt_urls": [], "method": "pdf" if url.lower().endswith(".pdf") else "http",
             "anchor": anchor, "window": (m.end() - m.start()) + 5, "pattern": None,
             "expect_text": text[m.start():m.end()], "_score": score,
             "_context": text[max(0, m.start() - 80):m.end() + 60]}
    if runner.judge_check(check, text)[0] != "MATCH":
        return None, "selftest_failed"
    return check, None


QUOTE_RE = re.compile(r"""['"‘“]([^'"’”]{30,240})['"’”]""")


def propose_quote(rec, docs):
    prose = " ".join(str(rec.get(k) or "") for k in ("cycle_description", "citation", "notes", "fee_notes",
                                                        "reinstatement_fee_notes", "penalty_cpe_notes", "lapse_trigger"))
    for q in QUOTE_RE.findall(prose):
        qn = normalise(q).rstrip(".")
        for url, text in sorted(docs, key=lambda d: url_rank(d[0])):
            i = text.lower().find(qn.lower())
            if i >= 0 and text.lower().count(qn[:28].lower()) == 1:
                check = {"url": url, "alt_urls": [], "method": "pdf" if url.lower().endswith(".pdf") else "http",
                         "anchor": qn[:28], "window": len(qn) + 5, "pattern": None, "expect_text": qn}
                if runner.judge_check(check, text)[0] == "MATCH":
                    return check
    return None


def main(cache_dir, out_path):
    index = json.load(open(os.path.join(cache_dir, "index.json"), encoding="utf-8"))
    existing = runner._load(runner.RECIPES) if os.path.exists(runner.RECIPES) else {}
    proposals, needs_hand, stats = {}, {}, {"ok": 0, "hand": 0}
    for ds in runner.DATASETS:
        for rec in runner._load(os.path.join(runner.DATA, ds + ".json"))["records"]:
            rid = rec["id"]
            if rid in existing:
                continue
            docs = []
            for k in URL_FIELDS:
                u = rec.get(k)
                for uu in (u if isinstance(u, list) else [u]):
                    if isinstance(uu, str) and index.get(uu.split("#")[0], {}).get("ok"):
                        p = os.path.join(cache_dir, index[uu.split("#")[0]]["file"])
                        docs.append((uu.split("#")[0], open(p, encoding="utf-8").read()))
            if not docs:
                needs_hand[rid] = {"dataset": ds, "why": "no source fetched",
                                   "fetch": {k: index.get(str(rec.get(k)).split("#")[0], {}).get("reason")
                                             for k in URL_FIELDS if rec.get(k)}}
                stats["hand"] += 1
                continue
            checks, problems = [], []
            for field, kind in FIELDS[ds]:
                r = propose_numeric(rec, field, kind, docs)
                if r is None:
                    continue
                if r["status"] == "ok" and r["check"]["_score"] > 0:
                    checks.append(r["check"])
                else:
                    problems.append(r if r["status"] != "ok" else {"field": field, "status": "score_0",
                                                                    "context": r["check"]["_context"]})
            if ds == "cpa_deadlines":
                dchk, why = propose_deadline(rec, docs)
                if dchk:
                    checks.append(dchk)
            if not checks and not problems:
                q = propose_quote(rec, docs)
                if q:
                    checks.append(q)
                else:
                    problems.append({"status": "no_numeric_fields_and_no_verbatim_quote"})
            if checks and not problems:
                proposals[rid] = {"dataset": ds, "manual": None, "combine": "each", "field": None,
                                  "derivation": None, "provenance": "bootstrap 2026-10-02", "checks": checks}
                stats["ok"] += 1
            else:
                needs_hand[rid] = {"dataset": ds, "partial_checks": checks, "problems": problems}
                stats["hand"] += 1
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"proposals": proposals, "needs_hand": needs_hand}, f, indent=1, ensure_ascii=False)
    print(stats)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
