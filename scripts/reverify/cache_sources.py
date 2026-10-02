"""One-time helper for recipe authoring: fetch every URL cited by any record (politely, via
fetch.Fetcher) and save the extracted text, so recipes can be written and tested offline.

    python scripts/reverify/cache_sources.py <cache_dir>

Hosts are fetched in parallel (one thread per host). Within a host, requests stay sequential
at <=1 req/s, so per-host politeness is unchanged. Writes <cache_dir>/index.json:
{url: {ok, status, reason, file, sha256}}; text files are <sha1(url)>.txt.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch import Fetcher  # noqa: E402

DATA = os.path.abspath(os.path.join(HERE, "..", "..", "data"))
URL_FIELDS = ("source_url", "citation_url", "secondary_source_url")


def record_urls():
    urls = set()
    for ds in ("cpa_deadlines", "cpe_hours", "reinstatement", "renewal_fees"):
        with open(os.path.join(DATA, ds + ".json"), encoding="utf-8-sig") as f:
            for r in json.load(f)["records"]:
                for k in URL_FIELDS:
                    v = r.get(k)
                    for u in (v if isinstance(v, list) else [v]):
                        if isinstance(u, str) and u.startswith("http"):
                            urls.add(u.split("#")[0])
    return sorted(urls)


def main(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    idx_path = os.path.join(cache_dir, "index.json")
    index = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {}
    by_host = defaultdict(list)
    for u in record_urls():
        if not index.get(u, {}).get("ok"):
            by_host[urlparse(u).netloc].append(u)

    def do_host(urls):
        f = Fetcher()   # one fetcher per host thread: its own rate clock + robots cache
        out = {}
        for u in urls:
            r = f.get(u, "pdf" if u.lower().endswith(".pdf") else "http")
            name = hashlib.sha1(u.encode()).hexdigest() + ".txt"
            if r.ok:
                with open(os.path.join(cache_dir, name), "w", encoding="utf-8") as fh:
                    fh.write(r.text)
            out[u] = {"ok": r.ok, "status": r.status, "reason": r.reason,
                      "file": name if r.ok else None, "sha256": r.sha256}
        return out

    with ThreadPoolExecutor(max_workers=16) as ex:
        for part in ex.map(do_host, by_host.values()):
            index.update(part)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    ok = sum(1 for v in index.values() if v["ok"])
    reasons = defaultdict(int)
    for v in index.values():
        if not v["ok"]:
            reasons[v["reason"]] += 1
    print(f"{ok}/{len(index)} ok; failures by reason: {dict(reasons)}")


if __name__ == "__main__":
    main(sys.argv[1])
