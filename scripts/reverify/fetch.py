"""Polite fetch + text extraction for the monthly re-verification runner.

Deterministic, no LLM. Rules (Orchestrator 2026-10-02 auto-reverify directive):
- honest User-Agent naming the project and a contact address;
- robots.txt honoured per host (a disallow is a FAILED fetch, never a bypass);
- at most 1 request/second per host;
- retry semantics copied from scripts/source_check.py: 403/429/503 and
  connection errors get ONE retry, 404/401/451 are decisive.
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
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from source_check import _extract_docx, _extract_html, _extract_pdf  # noqa: E402

UA = "DeadlineRadar-Reverify/1.0 (+https://deadline-radar.com; raven@mooseandraven.com)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 1.0
RETRIABLE_HTTP_CODES = {403, 429, 503}


@dataclass
class Fetched:
    url: str
    ok: bool
    status: int | None
    reason: str          # "" when ok; short machine-readable cause otherwise
    text: str | None     # normalised extracted text
    sha256: str | None   # of the raw body, for the verification_history audit trail


class Fetcher:
    def __init__(self, opener=None, sleep=time.sleep, clock=time.monotonic, robots_loader=None):
        self._open = opener or urllib.request.urlopen
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
        req = urllib.request.Request(f"{scheme}://{host}/robots.txt", headers={"User-Agent": UA})
        try:
            self._throttle(host)
            with self._open(req, timeout=TIMEOUT_S) as r:
                rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
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
        if not self.allowed(url):
            return Fetched(url, False, None, "robots_disallow", None, None)
        host = urlparse(url).netloc
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        status, body, ctype = None, None, ""
        for attempt in range(2):
            try:
                self._throttle(host)
                with self._open(req, timeout=TIMEOUT_S) as resp:
                    body = resp.read()
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    status = resp.status
                break
            except urllib.error.HTTPError as e:
                if e.code in RETRIABLE_HTTP_CODES and attempt == 0:
                    self._sleep(2)
                    continue
                return Fetched(url, False, e.code, f"http_{e.code}", None, None)
            except Exception as e:
                if attempt == 0:
                    self._sleep(2)
                    continue
                return Fetched(url, False, None, f"conn_{type(e).__name__}", None, None)
        if not (200 <= (status or 0) < 300) or not body:
            return Fetched(url, False, status, "empty_or_non_2xx", None, None)
        text = extract(body, ctype, url, method)
        if not text:
            return Fetched(url, False, status, "no_text_extracted", None, hashlib.sha256(body).hexdigest())
        return Fetched(url, True, status, "", text, hashlib.sha256(body).hexdigest())

    def _get_browser(self, url: str) -> Fetched:
        """JS-rendered pages. Same robots + rate rules; Playwright is imported lazily so
        http/pdf-only runs and the offline tests don't need it installed."""
        if not self.allowed(url):
            return Fetched(url, False, None, "robots_disallow", None, None)
        self._throttle(urlparse(url).netloc)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return Fetched(url, False, None, "playwright_missing", None, None)
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                try:
                    page = b.new_page(user_agent=UA)
                    resp = page.goto(url, wait_until="networkidle", timeout=TIMEOUT_S * 1000)
                    status = resp.status if resp else None
                    html = page.content().encode("utf-8")
                finally:
                    b.close()
        except Exception as e:
            return Fetched(url, False, None, f"browser_{type(e).__name__}", None, None)
        if not (200 <= (status or 0) < 300):
            return Fetched(url, False, status, f"http_{status}", None, None)
        text = _extract_html(html)
        return Fetched(url, bool(text), status, "" if text else "no_text_extracted", text,
                       hashlib.sha256(html).hexdigest())


def extract(body: bytes, ctype: str, url: str, method: str) -> str | None:
    path = urlparse(url).path.lower()
    if method == "pdf" or "pdf" in ctype or path.endswith(".pdf") or body[:5] == b"%PDF-":
        text = _extract_pdf(body)
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
