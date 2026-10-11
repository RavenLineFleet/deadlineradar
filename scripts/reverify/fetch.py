"""Polite fetch + text extraction for the monthly re-verification runner.

Deterministic, no LLM. Rules (Orchestrator 2026-10-02 auto-reverify directive):
- honest User-Agent naming the project and a contact address;
- robots.txt honoured per host (a disallow is a FAILED fetch, never a bypass);
- at most 1 request/second per host;
- retry semantics copied from scripts/source_check.py: 403/429/503 and
  connection errors get ONE retry, 404/401/451 are decisive;
- redirects are followed by hand, at most MAX_REDIRECTS hops, and EVERY hop
  gets the same robots + 1 req/s checks as the first request (RVF-1: the
  transport used to follow a 3xx to another host with none of them);
- a document routed to the PDF extractor must carry the %PDF- magic (RVF-1A).
Extraction reuses source_check's pdf/docx/html extractors so both tools read a
document the same way.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from source_check import _extract_docx, _extract_html, _extract_pdf  # noqa: E402

UA = "DeadlineRadar-Reverify/1.0 (+https://deadline-radar.com; raven@mooseandraven.com)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 1.0
RETRIABLE_HTTP_CODES = {403, 429, 503}
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface every 3xx as an HTTPError so Fetcher can re-check robots + rate
    limit on the target host before following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass
class Fetched:
    url: str
    ok: bool
    status: int | None
    reason: str          # "" when ok; short machine-readable cause otherwise
    text: str | None     # normalised extracted text
    sha256: str | None   # of the raw body, for the verification_history audit trail
    final_url: str = ""  # the URL that actually served `text`/`sha256` (== url unless redirected)


class Fetcher:
    def __init__(self, opener=None, sleep=time.sleep, clock=time.monotonic, robots_loader=None):
        self._open = opener or _NO_REDIRECT_OPENER.open
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_loader = robots_loader or self._load_robots

    # ---- politeness ----
    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = MIN_INTERVAL_S - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    def _load_robots(self, scheme: str, host: str):
        rp = urllib.robotparser.RobotFileParser()
        target = f"{scheme}://{host}/robots.txt"
        try:
            for _ in range(MAX_REDIRECTS + 1):
                req = urllib.request.Request(target, headers={"User-Agent": UA})
                self._throttle(urlparse(target).netloc)
                try:
                    with self._open(req, timeout=TIMEOUT_S) as r:
                        rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
                    return rp
                except urllib.error.HTTPError as e:
                    loc = e.headers.get("Location") if e.headers else None
                    if e.code in REDIRECT_CODES and loc:   # RFC 9309: follow at least 5 redirects
                        target = urljoin(target, loc)
                        continue
                    raise
            rp.allow_all = True          # redirect chain too long: treated like an unreachable robots.txt
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                rp.disallow_all = True   # RFC 9309: an access-denied robots.txt means stay out
            else:
                rp.allow_all = True      # 404 etc.: no robots file means no restrictions
        except Exception:
            rp.allow_all = True          # unreachable robots.txt is not a disallow; the page fetch decides
        return rp

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc not in self._robots:
            self._robots[p.netloc] = self._robots_loader(p.scheme, p.netloc)
        return self._robots[p.netloc].can_fetch(UA, url)

    # ---- fetch ----
    def get(self, url: str, method: str = "http") -> Fetched:
        if method == "browser":
            return self._get_browser(url)
        cur = url
        hops = 0
        while True:
            if not self.allowed(cur):
                return Fetched(url, False, None, "robots_disallow", None, None, cur)
            host = urlparse(cur).netloc
            req = urllib.request.Request(cur, headers={"User-Agent": UA, "Accept": "*/*"})
            status, body, ctype, moved = None, None, "", None
            for attempt in range(2):
                try:
                    self._throttle(host)
                    with self._open(req, timeout=TIMEOUT_S) as resp:
                        body = resp.read()
                        ctype = (resp.headers.get("Content-Type") or "").lower()
                        status = resp.status
                    break
                except urllib.error.HTTPError as e:
                    loc = e.headers.get("Location") if e.headers else None
                    if e.code in REDIRECT_CODES and loc:
                        moved = urljoin(cur, loc)
                        break
                    if e.code in RETRIABLE_HTTP_CODES and attempt == 0:
                        self._sleep(2)
                        continue
                    return Fetched(url, False, e.code, f"http_{e.code}", None, None, cur)
                except Exception as e:
                    if attempt == 0:
                        self._sleep(2)
                        continue
                    return Fetched(url, False, None, f"conn_{type(e).__name__}", None, None, cur)
            if moved is None:
                break
            hops += 1
            if hops > MAX_REDIRECTS:
                return Fetched(url, False, None, "too_many_redirects", None, None, cur)
            if urlparse(moved).scheme not in ("http", "https"):
                return Fetched(url, False, None, "redirect_bad_scheme", None, None, cur)
            cur = moved
        if not (200 <= (status or 0) < 300) or not body:
            return Fetched(url, False, status, "empty_or_non_2xx", None, None, cur)
        if _routes_to_pdf(body, ctype, cur, method) and not _is_pdf(body):
            return Fetched(url, False, status, "not_a_pdf", None, hashlib.sha256(body).hexdigest(), cur)
        text = extract(body, ctype, cur, method)
        if not text:
            return Fetched(url, False, status, "no_text_extracted", None, hashlib.sha256(body).hexdigest(), cur)
        return Fetched(url, True, status, "", text, hashlib.sha256(body).hexdigest(), cur)

    def _get_browser(self, url: str) -> Fetched:
        """JS-rendered pages. Same robots + rate rules, applied to EVERY main-frame
        navigation (redirect hops included, RVF-1); Playwright is imported lazily so
        http/pdf-only runs and the offline tests don't need it installed."""
        if not self.allowed(url):
            return Fetched(url, False, None, "robots_disallow", None, None, url)
        self._throttle(urlparse(url).netloc)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return Fetched(url, False, None, "playwright_missing", None, None, url)
        start_host = urlparse(url).netloc
        blocked: list[str] = []
        too_many: list[str] = []
        final = url
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                try:
                    page = b.new_page(user_agent=UA)

                    def guard(route, request):
                        u = request.url
                        if not (request.is_navigation_request() and request.frame == page.main_frame
                                and urlparse(u).scheme in ("http", "https")):
                            route.continue_()
                            return
                        # Chromium follows a 3xx inside its network stack and does NOT route
                        # the redirect target, so a plain continue_() would request the
                        # target before we could look at it. Walk the chain here instead,
                        # applying the http path's robots + rate rules to every hop, and
                        # only then let the browser load the validated final URL.
                        cur = u
                        for _ in range(MAX_REDIRECTS + 1):
                            if not self.allowed(cur):
                                blocked.append(cur)
                                route.abort()
                                return
                            if urlparse(cur).netloc != start_host:
                                self._throttle(urlparse(cur).netloc)
                            resp = route.fetch(url=cur, max_redirects=0)
                            loc = resp.headers.get("location")
                            if resp.status in REDIRECT_CODES and loc:
                                cur = urljoin(cur, loc)
                                if urlparse(cur).scheme not in ("http", "https"):
                                    blocked.append(cur)
                                    route.abort()
                                    return
                                continue
                            break
                        else:
                            too_many.append(cur)
                            route.abort()
                            return
                        if cur == u:
                            route.fulfill(response=resp)
                        else:
                            # re-space the browser's own request to the final host (1 req/s)
                            self._throttle(urlparse(cur).netloc)
                            route.fulfill(status=302, headers={"Location": cur}, body="")

                    page.route("**/*", guard)
                    resp = page.goto(url, wait_until="networkidle", timeout=TIMEOUT_S * 1000)
                    status = resp.status if resp else None
                    final = page.url or url
                    html = page.content().encode("utf-8")
                finally:
                    b.close()
        except Exception as e:
            if blocked:
                return Fetched(url, False, None, "robots_disallow", None, None, blocked[-1])
            if too_many:
                return Fetched(url, False, None, "too_many_redirects", None, None, too_many[-1])
            return Fetched(url, False, None, f"browser_{type(e).__name__}", None, None, final)
        if blocked or not self.allowed(final):
            return Fetched(url, False, None, "robots_disallow", None, None, blocked[-1] if blocked else final)
        if not (200 <= (status or 0) < 300):
            return Fetched(url, False, status, f"http_{status}", None, None, final)
        text = _extract_html(html)
        return Fetched(url, bool(text), status, "" if text else "no_text_extracted", text,
                       hashlib.sha256(html).hexdigest(), final)


def _is_pdf(body: bytes) -> bool:
    return b"%PDF-" in body[:1024]


def _routes_to_pdf(body: bytes, ctype: str, url: str, method: str) -> bool:
    return method == "pdf" or "pdf" in ctype or urlparse(url).path.lower().endswith(".pdf") or body[:5] == b"%PDF-"


def extract(body: bytes, ctype: str, url: str, method: str) -> str | None:
    path = urlparse(url).path.lower()
    if _routes_to_pdf(body, ctype, url, method):
        text = _extract_pdf(body) if _is_pdf(body) else None
    elif "officedocument.wordprocessingml" in ctype or path.endswith(".docx"):
        text = _extract_docx(body)
    else:
        text = _extract_html(body)
    return normalise(text) if text else None


def normalise(text: str) -> str:
    """Collapse whitespace and unify the punctuation variants PDFs and HTML disagree on,
    so an anchor written against one rendering still matches the other."""
    for a, b in ((" ", " "), ("’", "'"), ("‘", "'"), ("“", '"'),
                 ("”", '"'), ("–", "-"), ("—", "-"), ("­", "")):
        text = text.replace(a, b)
    return " ".join(text.split())
