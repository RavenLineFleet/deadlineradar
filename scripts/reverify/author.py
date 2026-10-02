"""Hand-authoring helper for recipes the bootstrapper could not finish (authoring aid only).

    python scripts/reverify/author.py show <cache_dir> <id> [<id> ...]
        stored values + every candidate snippet in the cached sources
    python scripts/reverify/author.py build <cache_dir> <hand_spec.json>
        hand spec + bootstrap proposals -> data/reverify_recipes.json (self-tested against the cache)

hand_spec.json:
  { "<id>": {"manual": "<reason>"} |
            {"checks": [{"field": "fee_usd" | null, "url": "<url or doc index>", "anchor": "<literal>",
                         "kind": "fee" | "hours" | "text", "expect_text": "<for kind=text>",
                         "expect": <per-check value, for sum>, "alt_urls": [...] , "method": "browser"?}],
             "combine": "sum"?, "field": "<for sum>", "derivation": "<note>",
             "keep_auto": true }   # keep the bootstrapper's passing checks too (default true)
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bootstrap as bs  # noqa: E402
import runner  # noqa: E402


def docs_for(rec, index, cache_dir):
    out = []
    for k in bs.URL_FIELDS:
        u = rec.get(k)
        for uu in (u if isinstance(u, list) else [u]):
            if not isinstance(uu, str) or not uu.startswith("http"):
                continue
            key = uu.split("#")[0]
            meta = index.get(key, {})
            text = open(os.path.join(cache_dir, meta["file"]), encoding="utf-8").read() if meta.get("ok") else None
            out.append((key, text, meta.get("reason", "not_cached")))
    return out


def all_records():
    return {r["id"]: (ds, r) for ds in runner.DATASETS
            for r in runner._load(os.path.join(runner.DATA, ds + ".json"))["records"]}


def show(cache_dir, ids):
    index = json.load(open(os.path.join(cache_dir, "index.json"), encoding="utf-8"))
    recs = all_records()
    for rid in ids:
        ds, rec = recs[rid]
        fields = {f: rec.get(f) for f, _ in bs.FIELDS[ds]}
        extra = {k: rec.get(k) for k in ("fee_basis", "confidence", "data_gap_note", "renewal_pattern") if rec.get(k)}
        print(f"\n### {rid} [{ds}] stored={fields} {json.dumps(extra)[:300]}")
        if ds == "cpa_deadlines":
            print("   cycle:", str(rec.get("cycle_description"))[:260])
        for di, (u, text, why) in enumerate(docs_for(rec, index, cache_dir)):
            print(f"  doc{di} {u[:110]} {'(' + why + ')' if not text else f'{len(text)} chars'}")
            if not text:
                continue
            for f, kind in bs.FIELDS[ds]:
                v = rec.get(f)
                if v is None:
                    continue
                hits = list(re.finditer(bs.value_regex(kind, v), text, re.I))[:4]
                for m in hits:
                    print(f"     {f}={v}: …{text[max(0, m.start() - 110):m.end() + 50]}…")
                if not hits:   # value absent: show amounts/hours next to the field's keywords instead
                    kw = bs.KEYWORDS[f][0]
                    near = [m for m in re.finditer(re.escape(kw), text, re.I)][:3]
                    for m in near:
                        print(f"     {f}={v} NOT FOUND; near '{kw}': …{text[max(0, m.start() - 60):m.end() + 140]}…")
            if ds == "cpa_deadlines":
                for m in list(bs.DATE_RE.finditer(text))[:5]:
                    print(f"     date: …{text[max(0, m.start() - 90):m.end() + 40]}…")


def build(cache_dir, spec_path):
    index = json.load(open(os.path.join(cache_dir, "index.json"), encoding="utf-8"))
    recs = all_records()
    props = json.load(open(os.path.join(cache_dir, "proposals.json"), encoding="utf-8"))
    spec = json.load(open(spec_path, encoding="utf-8"))
    recipes, errors = {}, {}
    for rid, p in props["proposals"].items():
        recipes[rid] = _strip(p)
    for rid, s in spec.items():
        ds, rec = recs[rid]
        if s.get("manual"):
            recipes[rid] = {"dataset": ds, "manual": s["manual"], "combine": "each", "field": None,
                            "derivation": s.get("derivation"), "provenance": "hand 2026-10-02", "checks": []}
            continue
        docs = docs_for(rec, index, cache_dir)
        checks = []
        if s.get("keep_auto", True):
            checks += props["needs_hand"].get(rid, {}).get("partial_checks", [])
        for c in s["checks"]:
            url = docs[c["url"]][0] if isinstance(c["url"], int) else c["url"]
            text = next((t for u, t, _ in docs if u == url), None)
            if text is None and url in index and index[url].get("ok"):
                text = open(os.path.join(cache_dir, index[url]["file"]), encoding="utf-8").read()
            kind = c.get("kind", "fee")
            chk = {"url": url, "alt_urls": c.get("alt_urls", []),
                   "method": c.get("method") or ("pdf" if url.lower().endswith(".pdf") else "http"),
                   "anchor": c["anchor"], "window": c.get("window", 60 if kind != "text" else len(c.get("expect_text", "")) + 40),
                   "pattern": None if kind == "text" else c.get("pattern", bs.PATTERN[kind])}
            if c.get("field"):
                chk["field"] = c["field"]
            if kind == "text":
                chk["expect_text"] = c["expect_text"]
            if "expect" in c:
                chk["expect"] = c["expect"]
            for k in ("divide", "multiply", "duplicated_source"):
                if k in c:
                    chk[k] = c[k]
            if text is None:
                errors[rid] = f"no cached text for {url} (method={chk['method']}); check unverified offline"
            else:
                st, got = runner.judge_check(chk, text)
                want = c.get("expect", rec.get(c["field"]) if c.get("field") else None)
                if st != "MATCH" or (want is not None and kind != "text" and runner._scaled(got, chk) != runner._num(want)):
                    errors[rid] = f"selftest {st} got={got!r} want={want!r} anchor={c['anchor']!r}"
            checks.append(chk)
        recipes[rid] = {"dataset": ds, "manual": None, "combine": s.get("combine", "each"),
                        "field": s.get("field"), "derivation": s.get("derivation"),
                        "provenance": "hand 2026-10-02", "checks": [_strip_check(c) for c in checks]}
    # Uniqueness pass (AuditLab 10-02): every anchor must occur exactly once in its own source. A repeated
    # anchor is extended LEFTWARDS from its current (first) match until unique -- same row, now pinned by
    # content -- then every check is re-self-tested, auto-proposed ones included.
    widened = []
    errors = {k: v for k, v in errors.items() if v.startswith("no cached text")}   # re-collected below
    for rid, rec_ in recipes.items():
        ds_, stored = recs[rid]
        for c in rec_.get("checks", []):
            meta = index.get(c["url"], {})
            if not meta.get("ok"):
                continue
            text = open(os.path.join(cache_dir, meta["file"]), encoding="utf-8").read()
            low, a = text.lower(), runner.normalise(c["anchor"]).lower()
            i = low.find(a)
            if i >= 0 and low.count(a) > 1 and not c.get("duplicated_source"):
                for n in range(10, 400, 10):
                    cand = text[max(0, i - n):i + len(a)]
                    cand = cand[cand.find(" ") + 1:] if " " in cand[:12] else cand
                    if low.count(cand.lower()) == 1:
                        widened.append(f"{rid}: {c['anchor'][:40]!r} -> ...{cand[:60]!r}")
                        c["anchor"] = cand
                        break
                else:
                    # no unique widening: the passage itself is repeated verbatim. Flag it ONLY if every
                    # occurrence yields the same result (runner re-checks this on every run).
                    if runner.judge_check(dict(c, duplicated_source=True), text)[0] == "MATCH":
                        c["duplicated_source"] = True
                        widened.append(f"{rid}: {c['anchor'][:40]!r} repeated verbatim, all occurrences agree -> duplicated_source")
            st, got = runner.judge_check(c, text)
            want = c.get("expect", stored.get(c["field"]) if c.get("field") else None)
            if st != "MATCH" or (want is not None and c.get("pattern") and runner._scaled(got, c) != runner._num(want)):
                errors[rid] = f"selftest {st} got={got!r} want={want!r} anchor={c['anchor'][:60]!r}"
    for w in widened:
        print("  widened", w)
    missing = sorted(set(recs) - set(recipes))
    out = {k: recipes[k] for k in sorted(recipes)}
    with open(runner.RECIPES, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"recipes: {len(out)} ({sum(1 for r in out.values() if r['manual'])} manual); missing: {len(missing)}")
    for rid, e in sorted(errors.items()):
        print(f"  ERR {rid}: {e}")
    if missing:
        print("  missing:", " ".join(missing))


def _strip_check(c):
    return {k: v for k, v in c.items() if not k.startswith("_")}


def _strip(p):
    p = dict(p)
    p["checks"] = [_strip_check(c) for c in p["checks"]]
    return p


if __name__ == "__main__":
    cmd, cache = sys.argv[1], sys.argv[2]
    show(cache, sys.argv[3:]) if cmd == "show" else build(cache, sys.argv[3])
