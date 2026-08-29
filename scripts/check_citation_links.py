#!/usr/bin/env python3
"""Citation-URL link-rot checker for DeadlineRadar.

Every "Confirmed at source" badge on this site is a promise that clicking
the link shows the reader the text we cited. AuditLab's first public-SEO
audit (2026-08-03) found 6 dead citation URLs -- one (Guam) had been dead
for roughly 3 months with nothing to notice it. Rule-TEXT changes are
already monitored (DiffLab); this is the same idea for the LINKS
themselves.

Walks every data/*.json and worker/src/*.json file, collects every string
value whose key ends in "_url" and starts with "http", and checks each.

AuditLab CITE-64 (LOW, 2026-08-28): a full 327-URL/140-host sweep found
zero dead citations, but also found the OLD status-only "200 <= status <
400" predicate was unreliable on ~45 of those 140 hosts, in both
directions:
  - FALSE PASS on ~19 hosts whose server returns 200 for ANY path (an
    SPA shell, or a bot-defense captcha page served at 200) -- a citation
    that actually died there would keep "passing" forever.
  - FALSE FAIL on hosts that 403 automated requests on pages that are
    perfectly live in a real browser (bot defense, not a dead link).
Proven by construction: fetching a garbage path per host and comparing
its response to the real citation's response. This version does that
same per-host control probe and reports a three-state verdict --
LIVE / DEAD / UNVERIFIABLE-BY-HTTP -- instead of a boolean, so a host
this script cannot actually evaluate is named as exactly that rather
than silently defaulting to a wrong answer either way.

This is a REPORT-ONLY, MANUALLY-RUN advisory -- like
worker_deploy_staleness_check.py, it does not fail preship_gate.py and
never edits data. Deliberately NOT wired into preship_gate.py's own
always-run checks: this makes ~2x as many live external HTTP requests as
citation URLs (one control probe per unique host, cached), which would
add real minutes and real flakiness risk (rate limiting, transient
network errors on ~140 different government hosts) to every single
build. Run it deliberately, on a cadence, not on every ship.

Usage: python scripts/check_citation_links.py [repo_root]
"""
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

TIMEOUT_SECONDS = 12
USER_AGENT = "Mozilla/5.0 (compatible; DeadlineRadarLinkCheck/1.0)"
# Generous enough that two genuinely different real documents (e.g. two
# distinct PDFs) essentially never collide, but bounded so a single fetch
# can't stall the sweep on a multi-hundred-MB response. AuditLab's own
# sweep hit a false content-match on Ohio because ITS read cap (200,000
# bytes) truncated two different real pages to the same length -- sized
# well above that here for the same reason Utah's real PDFs (96KB/143KB)
# need to be read in full to be told apart from a control-path miss.
CONTENT_SAMPLE_BYTES = 1_000_000


def find_urls(obj, path=""):
    """Yields (path, url) for every string value at a key ending in "_url"
    that looks like an http(s) link. Deliberately generic (no hand-kept
    field-name list) so a newly added *_url field is covered automatically
    -- the same anti-drift reasoning as _mobility_covered_slugs() in
    generate.py.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{path}.{k}" if path else k
            if isinstance(v, str) and k.lower().endswith("_url") and v.startswith("http"):
                yield key_path, v
            else:
                yield from find_urls(v, key_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from find_urls(item, f"{path}[{i}]")


def _fetch(url: str) -> tuple[int | None, bytes | None, str]:
    """Returns (status, body_sample, detail). status is None on a network-
    level failure (no HTTP response at all) -- distinct from a real HTTP
    error status, which IS a status. GET only, not HEAD-first like the
    predecessor script: the same-content-at-every-path comparison this
    version needs requires a real body sample, so a HEAD response (even a
    successful one) could never be enough to classify on its own -- doing
    HEAD-then-always-GET-anyway would just be a second request for no
    benefit.
    """
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(CONTENT_SAMPLE_BYTES)
            return resp.status, body, str(resp.status)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(CONTENT_SAMPLE_BYTES)
        except Exception:
            body = None
        return e.code, body, str(e.code)
    except Exception as e:
        return None, None, type(e).__name__


def _host_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _control_url_for(url: str) -> str:
    # A fresh random path per run -- not a fixed guessable string -- so a
    # host can't special-case it even by accident.
    suffix = uuid.uuid4().hex
    return f"{_host_key(url)}/deadlineradar-link-check-control-{suffix}/"


def classify(real_status, real_body, sig_status, sig_body) -> tuple[str, str]:
    """Returns (verdict, reason). verdict is one of LIVE / DEAD / UNVERIFIABLE."""
    # Bot defense: a 403 on the real citation, or on the control probe
    # itself (can't establish any baseline for this host), is not proof of
    # anything either way -- AuditLab confirmed all 26 sweep 403s were
    # live pages behind bot defense, not dead links.
    if real_status == 403 or sig_status == 403:
        return "UNVERIFIABLE", "403 (bot defense -- status proves nothing here)"
    if sig_status is None:
        return "UNVERIFIABLE", "control probe failed at the network level; no baseline for this host"
    if real_status is None:
        return "UNVERIFIABLE", "citation URL failed at the network level (not a real HTTP status)"
    if sig_status == 200 and real_status == 200:
        if sig_body is not None and real_body is not None and sig_body == real_body:
            return "UNVERIFIABLE", "host returns identical 200 content for a garbage path (SPA shell or captcha-at-200)"
        return "LIVE", "200, and distinguishable from this host's garbage-path response"
    if 200 <= real_status < 400:
        return "LIVE", str(real_status)
    return "DEAD", str(real_status)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    files = sorted(root.glob("data/*.json")) + sorted(root.glob("worker/src/*.json"))

    all_urls: dict[str, list[str]] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key_path, url in find_urls(data):
            all_urls.setdefault(url, []).append(f"{f.relative_to(root)}:{key_path}")

    print(f"Checking {len(all_urls)} distinct citation URLs across {len(files)} data files...")

    # One control probe per unique host, cached -- not one per URL, which
    # would multiply the request count by however many citations share a
    # host (e.g. every mn.gov citation) for zero extra signal.
    host_signatures: dict[str, tuple[int | None, bytes | None]] = {}

    results: dict[str, tuple[str, str, list[str]]] = {}
    for i, (url, locations) in enumerate(sorted(all_urls.items()), 1):
        host = _host_key(url)
        if host not in host_signatures:
            sig_status, sig_body, _ = _fetch(_control_url_for(url))
            host_signatures[host] = (sig_status, sig_body)
        sig_status, sig_body = host_signatures[host]

        real_status, real_body, detail = _fetch(url)
        verdict, reason = classify(real_status, real_body, sig_status, sig_body)
        results[url] = (verdict, reason, locations)
        print(f"[{i}/{len(all_urls)}] {verdict} ({reason}) {url}")

    dead = [(u, r, l) for u, (v, r, l) in results.items() if v == "DEAD"]
    unverifiable = [(u, r, l) for u, (v, r, l) in results.items() if v == "UNVERIFIABLE"]
    live_count = sum(1 for v, _, _ in results.values() if v == "LIVE")

    print()
    print(
        f"{live_count} LIVE, {len(unverifiable)} UNVERIFIABLE-BY-HTTP, {len(dead)} DEAD "
        f"of {len(all_urls)} citation URLs across {len(host_signatures)} hosts."
    )

    if unverifiable:
        print(f"\n{len(unverifiable)} UNVERIFIABLE (not proof of anything -- HTTP cannot tell here; needs a real browser check):")
        for url, reason, locations in unverifiable:
            print(f"\n  {reason}  {url}")
            for loc in locations:
                print(f"    cited at: {loc}")

    if not dead:
        print(f"\nPASS -- 0 confirmed-DEAD citation URLs.")
        return 0

    print(f"\n{len(dead)} DEAD -- VERIFY IN A BROWSER before treating any of these as confirmed dead:")
    for url, reason, locations in dead:
        print(f"\n  {reason}  {url}")
        for loc in locations:
            print(f"    cited at: {loc}")
    return 0  # advisory only -- never fails the build


if __name__ == "__main__":
    sys.exit(main())
