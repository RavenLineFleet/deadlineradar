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
same per-host control probe and reports a four-state verdict --
LIVE / DEAD / UNVERIFIABLE-BY-HTTP / WRONG-PAGE -- instead of a boolean,
so a host this script cannot actually evaluate is named as exactly that
rather than silently defaulting to a wrong answer either way.

AuditLab CITE-66 (MEDIUM, 2026-08-28): even the fixed status-based logic
above only proves a host RESPONDED, never that it served the CITED text.
Proven live on the exact host behind Washington's CPE figures: a
repealed/renumbered section there silently redirects to its parent
chapter's listing page instead of 404ing -- structurally "live," but not
the cited rule. WRONG-PAGE asserts the record's own `citation` string
(e.g. "WAC 4-30-134") appears in the response's <title> tag -- title, not
the whole body, because a body-substring check does NOT catch this exact
case (the wrong chapter-listing page is a table of contents that legally
contains the target section number as a listed entry). See
assert_citation_in_content()'s own comment for the full reasoning and how
it was verified against the real page before shipping.

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
import re
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
    """Yields (path, url, citation) for every string value at a key ending
    in "_url" that looks like an http(s) link. Deliberately generic (no
    hand-kept field-name list) so a newly added *_url field is covered
    automatically -- the same anti-drift reasoning as
    _mobility_covered_slugs() in generate.py.

    `citation` is the record's own sibling "citation" field (e.g. "WAC
    4-30-134") when the URL key is exactly "citation_url" -- confirmed by a
    one-off sweep (2026-08-28) that all 730 citation_url records across
    every data file have one, with zero exceptions. Only "citation_url"
    carries this pairing; other *_url fields (secondary_url,
    status_source_url, source_url) don't have a reliably-paired citation
    string, so they get None and skip content-assertion (AuditLab CITE-66).
    """
    if isinstance(obj, dict):
        citation = obj.get("citation") if isinstance(obj.get("citation"), str) else None
        for k, v in obj.items():
            key_path = f"{path}.{k}" if path else k
            if isinstance(v, str) and k.lower().endswith("_url") and v.startswith("http"):
                yield key_path, v, (citation if k == "citation_url" else None)
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


# AuditLab CITE-65 (LOW, 2026-08-28), filed against CITE-64's own fix:
# exact byte equality missed Minnesota's Radware Bot Manager captcha
# specifically because it injects a fresh nonce (a UUID) into every
# response, so the control probe's body and the real citation's body --
# same 21,621-byte captcha page in both cases -- were never byte-
# identical, and the old check fell through to a false LIVE. Two
# independent, cheaper-to-defeat-by-accident signals layered here per
# AuditLab's own two candidate fixes, combined rather than choosing one:
# normalize away injected entropy before comparing (their "preferred,
# more precise" option -- strips UUIDs and long hex/base64/digit runs,
# the exact pattern Minnesota's nonce matches), OR treat an identical
# raw byte-length at the same 200 status as UNVERIFIABLE too (their
# "cheap, errs toward can't-tell" option) -- if EITHER signal fires,
# call it UNVERIFIABLE rather than requiring both, since a false
# UNVERIFIABLE just means "check this one in a browser" while a false
# LIVE means a dead citation goes unnoticed forever, and AuditLab
# explicitly named erring toward can't-tell as the safe direction.
_ENTROPY_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    r"|\b[0-9a-fA-F]{16,}\b"  # long hex run
    r"|\b[A-Za-z0-9+/_-]{20,}={0,2}\b"  # long base64-ish run
    r"|\b\d{10,}\b"  # long digit run (epoch timestamps, session ids)
)


def _normalize(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    return _ENTROPY_RE.sub("<ENTROPY>", text)


# AuditLab CITE-66 (MEDIUM, 2026-08-28): everything above only asks "did
# this host respond in a way distinguishable from a garbage path" -- never
# "is this actually the cited section." Proven live on the exact host
# behind Washington's CPE figures (app.leg.wa.gov): a repealed/renumbered
# section silently redirects to its parent CHAPTER'S listing page instead
# of 404ing. That response is large and genuinely distinguishable from the
# garbage-path control, so the checks above call it LIVE -- the reader
# clicking our citation lands on a page that doesn't contain the rule we
# cite, and nothing catches it.
#
# I verified AuditLab's own suggested fix (assert the citation string
# appears anywhere in the body) does NOT actually catch this exact case
# before implementing it: fetched the real wrong-page scenario myself, and
# the wrong (chapter-listing) page's body contains "4-30-134" three times
# -- it's a table-of-contents style index of every section in that
# chapter, so a bare body-substring check passes on the wrong page too.
# The <title> tag is what actually discriminates them on this host --
# empirically confirmed: correct page's <title> is "WAC 4-30-134:", the
# wrong page's is "Chapter 4-30 WAC:". Using <title> as the assertion
# target instead of the whole body, with a guard against hosts whose title
# doesn't vary by page at all (compared against the control probe's own
# title -- if they match, titles carry no signal here, so this backs off
# to SKIPPED rather than risk a false WRONG-PAGE on a host this heuristic
# doesn't fit).
_DASH_RE = re.compile("[‐‑‒–—−]")  # hyphen/non-breaking-hyphen/figure-dash/en-dash/em-dash/minus
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# First digit-led run of [0-9A-Za-z.-] in the citation, e.g. "WAC
# 4-30-134" -> "4-30-134", "S.C. Code Ann. § 40-2-255(A)" -> "40-2-255"
# (stops at the open-paren, which is exactly AuditLab's "tolerate
# subsection depth" trap -- never captures a trailing subsection suffix).
_CITATION_TOKEN_RE = re.compile(r"[0-9][0-9A-Za-z.-]*[0-9A-Za-z]")


def _dash_normalize(text: str) -> str:
    return _DASH_RE.sub("-", text)


def _extract_title(body: bytes) -> str | None:
    text = body.decode("utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title or None


def _extract_citation_token(citation: str) -> str | None:
    # A citation with a semicolon lists multiple distinct legal references
    # (common in this data -- see e.g. the reg_change_events.json Guam/
    # Michigan/Oregon conflict records) -- too ambiguous which one this
    # particular citation_url is meant to confirm, so this is deliberately
    # conservative and skips rather than guessing.
    if ";" in citation:
        return None
    m = _CITATION_TOKEN_RE.search(citation)
    return m.group(0) if m else None


def _looks_like_pdf(body: bytes) -> bool:
    return body[:5] == b"%PDF-"


def assert_citation_in_content(real_body: bytes | None, sig_body: bytes | None, citation: str | None) -> tuple[str, str]:
    """Returns (verdict, reason). verdict is CONFIRMED / WRONG_PAGE / SKIPPED.
    Only ever called once the status-level checks already consider the
    response structurally LIVE -- this can only downgrade LIVE to
    WRONG-PAGE, never upgrade a DEAD/UNVERIFIABLE result.
    """
    if real_body is None:
        return "SKIPPED", "no body to check"
    if not citation:
        return "SKIPPED", "no citation string on this record (only citation_url fields carry one)"
    if _looks_like_pdf(real_body):
        # AuditLab's own explicit guidance: "skip the assertion rather than
        # fake it" for PDFs -- confirming a %PDF body contains cited text
        # needs real text extraction, which is a materially different and
        # more failure-prone piece of machinery than a title-tag check.
        return "SKIPPED", "PDF body -- content assertion needs text extraction, not attempted"
    token = _extract_citation_token(citation)
    if not token:
        return "SKIPPED", "citation string has no extractable single section token (compound citation, or no digit-led token found)"
    real_title = _extract_title(real_body)
    if not real_title:
        return "SKIPPED", "no <title> tag on this page to check the citation against"
    if sig_body is not None:
        sig_title = _extract_title(sig_body)
        if sig_title and _dash_normalize(sig_title).lower() == _dash_normalize(real_title).lower():
            return "SKIPPED", "this host's <title> doesn't vary by path (same title on the garbage-path control) -- no signal here"
    if _dash_normalize(token).lower() in _dash_normalize(real_title).lower():
        return "CONFIRMED", f"citation token {token!r} found in <title> {real_title!r}"
    return "WRONG_PAGE", f"citation token {token!r} not found in <title> {real_title!r}"


def classify(real_status, real_body, sig_status, sig_body, citation=None) -> tuple[str, str]:
    """Returns (verdict, reason). verdict is one of LIVE / DEAD / UNVERIFIABLE / WRONG-PAGE."""
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
        if sig_body is not None and real_body is not None:
            if sig_body == real_body:
                return "UNVERIFIABLE", "host returns byte-identical 200 content for a garbage path (SPA shell or captcha-at-200)"
            if len(sig_body) == len(real_body):
                return "UNVERIFIABLE", f"host returns 200 content of the exact same length ({len(real_body)} bytes) for a garbage path -- likely the same page with injected entropy (e.g. a per-request nonce)"
            if _normalize(sig_body) == _normalize(real_body):
                return "UNVERIFIABLE", "host returns 200 content for a garbage path that's identical once known entropy (UUIDs, long hex/base64/digit runs) is stripped -- likely the same page with an injected nonce"
        live_reason = "200, and distinguishable from this host's garbage-path response"
    elif 200 <= real_status < 400:
        live_reason = str(real_status)
    else:
        return "DEAD", str(real_status)

    # AuditLab CITE-66: everything above only confirms the host responded
    # in a way distinguishable from garbage -- never that it served the
    # CITED text. Layer a content assertion on top before calling anything
    # LIVE. This can only downgrade LIVE to WRONG-PAGE; it never runs for a
    # response already classified DEAD/UNVERIFIABLE above.
    content_verdict, content_reason = assert_citation_in_content(real_body, sig_body, citation)
    if content_verdict == "WRONG_PAGE":
        return "WRONG-PAGE", f"status looked live ({live_reason}), but {content_reason}"
    return "LIVE", live_reason


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    files = sorted(root.glob("data/*.json")) + sorted(root.glob("worker/src/*.json"))

    all_urls: dict[str, list[str]] = {}
    citations: dict[str, str] = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key_path, url, citation in find_urls(data):
            all_urls.setdefault(url, []).append(f"{f.relative_to(root)}:{key_path}")
            # First non-None citation wins if the same URL is ever cited by
            # more than one record -- doesn't come up today (each
            # citation_url is per-record), but a shared URL with slightly
            # different citation text across records would only affect
            # which exact token gets checked, not whether the check runs.
            if citation and url not in citations:
                citations[url] = citation

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
        verdict, reason = classify(real_status, real_body, sig_status, sig_body, citations.get(url))
        results[url] = (verdict, reason, locations)
        print(f"[{i}/{len(all_urls)}] {verdict} ({reason}) {url}")

    dead = [(u, r, l) for u, (v, r, l) in results.items() if v == "DEAD"]
    wrong_page = [(u, r, l) for u, (v, r, l) in results.items() if v == "WRONG-PAGE"]
    unverifiable = [(u, r, l) for u, (v, r, l) in results.items() if v == "UNVERIFIABLE"]
    live_count = sum(1 for v, _, _ in results.values() if v == "LIVE")

    print()
    print(
        f"{live_count} LIVE, {len(unverifiable)} UNVERIFIABLE-BY-HTTP, {len(wrong_page)} WRONG-PAGE, "
        f"{len(dead)} DEAD of {len(all_urls)} citation URLs across {len(host_signatures)} hosts."
    )

    if unverifiable:
        print(f"\n{len(unverifiable)} UNVERIFIABLE (not proof of anything -- HTTP cannot tell here; needs a real browser check):")
        for url, reason, locations in unverifiable:
            print(f"\n  {reason}  {url}")
            for loc in locations:
                print(f"    cited at: {loc}")

    # WRONG-PAGE is arguably worse than DEAD: a dead link is at least
    # visibly broken to a reader who clicks it, where a wrong page looks
    # like a normal, working citation while silently not backing the claim
    # -- surfaced ahead of DEAD in the printed report for that reason.
    if wrong_page:
        print(f"\n{len(wrong_page)} WRONG-PAGE -- responds live, but the cited section is not on this page (AuditLab CITE-66):")
        for url, reason, locations in wrong_page:
            print(f"\n  {reason}  {url}")
            for loc in locations:
                print(f"    cited at: {loc}")

    if not dead and not wrong_page:
        print(f"\nPASS -- 0 confirmed-DEAD, 0 confirmed-WRONG-PAGE citation URLs.")
        return 0

    if dead:
        print(f"\n{len(dead)} DEAD -- VERIFY IN A BROWSER before treating any of these as confirmed dead:")
        for url, reason, locations in dead:
            print(f"\n  {reason}  {url}")
            for loc in locations:
                print(f"    cited at: {loc}")
    return 0  # advisory only -- never fails the build


if __name__ == "__main__":
    sys.exit(main())
