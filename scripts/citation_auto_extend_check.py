#!/usr/bin/env python3
"""Continuous-freshness auto-extend ELIGIBILITY CHECK -- dry run only, never writes.

Orchestrator-approved design (2026-09-23, _AAA_orchestrator_20260923_
continuous_freshness_APPROVED.md), hardened twice by adversarial review:
  - _AAA_orchestrator_20260923_AUTOEXTEND1_baseline_provenance.md
    (SecurityLab AUTOEXTEND-1 + the baseline-provenance/claim-anchor gap)
  - _AAA_orchestrator_20260923_ruling_AUTO1_5.md (AuditLab's full
    adversarial review of that version: AUTO-1 through AUTO-5, blocking)

Split as designed: DiffLab owns capture (their
`engine/citation_freshness_capture.py`); this file is AssetLab's
consumption half. Guardrails, non-negotiable under this repo's
100%-correctness bar -- current version:

  1. VALID FETCH, not just "no diff". Independently re-fetch every
     candidate; never trust DiffLab's changed/raw_changed signal as
     anything more than a cheap pre-filter.
  2. verified_method + AUTO-1's last_manual_verified_date: this script
     never writes to a dataset file. The write path (when built) must set
     verified_method="auto_source_unchanged" and must NEVER touch
     last_manual_verified_date -- only a human verification does.
  3. 90-DAY CEILING, FAIL CLOSED (AUTO-1, HIGH -- the shipped version of
     this before AuditLab's review failed OPEN: an auto-extended record's
     age came back `None`, and `exceeds_reverify_ceiling(None)` returned
     `False`, i.e. "permitted to extend" -- indefinitely, for every
     citation after its first auto-extend, which is exactly backwards for
     a ceiling that exists to guarantee a human looks again. Fixed: a
     dedicated `last_manual_verified_date` field, written only by a human
     pass, read by `exceeds_reverify_ceiling()`, which now returns `True`
     (ceiling EXCEEDED, not eligible) whenever that field is missing or
     unparseable -- unknown age must deny, not permit.
  4. CONTENT SHAPE FOR EVERY CITATION, NOT JUST PDFS (AUTO-2, MEDIUM --
     the shipped version's `_expected_pdf()` gated a real check for only
     28.2% of the monitored corpus (51/181); the other 130 got status +
     length + hash only, which a byte-stable wrong page satisfies by
     construction). Fixed: every non-PDF citation now REQUIRES a specific
     claim anchor (guardrail 6, hardened by AUTO-3) -- no anchor means no
     eligibility, full stop. 100% of citations now get either the %PDF-
     magic-byte check or the claim-anchor check; there is no third,
     unchecked path.
  5. SPECIFIC, NORMALIZED CLAIM ANCHORS (AUTO-3, MEDIUM -- a generic
     anchor like a bare code name ("4 CMC") or a bare short number ("4")
     passes on navigation chrome and bot-wall support-ID strings by
     coincidence; AuditLab measured this on the REAL cnmilaw.org soft-404
     body). Fixed: `extract_citation_anchor()` pulls a SPECIFIC locator
     (a subsection parenthetical or a multi-part hyphenated/dotted
     section number) out of the record's own `citation` prose, never a
     bare code/agency name or a short bare number; `normalize_typography()`
     canonicalizes dashes and section-word forms on BOTH the anchor and
     the fetched text before comparing, so a real page that prints
     "193A—5.3" (em-dash) still matches an anchor written "193A-5.3".
     A citation string with no extractable specific locator yields no
     anchor -- UNVERIFIABLE, not a free pass.
  6. ANCHOR PROVENANCE AT ANCHOR TIME (AUTO-4, MEDIUM -- baseline
     provenance alone moves the poisoning window rather than closing it:
     if a future manual pass records a baseline via a SEPARATE scripted
     fetch rather than the same fetch a human actually read, or records
     one from a source that's walled AT THAT MOMENT, the wall becomes a
     trusted "manual" baseline -- worse than no baseline, since it now
     carries authority). This script cannot enforce WHO looked at a fetch
     -- that is a process guarantee for whoever runs a manual pass -- but
     it gives that process a single, already-tested gate to call before
     ever recording a baseline: `validate_fetch_for_anchoring()`. It runs
     the EXACT SAME content-shape/claim-anchor logic `evaluate_candidate()`
     uses; if it returns not-OK, the record MUST stay without a baseline
     (stays ineligible) rather than anchor on whatever came back. On day
     one (today), 0 records have a manual-anchored baseline -- asserted
     explicitly by `_selftest()`, not left to observation, so a future
     change that makes records eligible sooner has to argue for it.
  7. EXCLUDED HOSTS (regs.maryland.gov, dcregs.dc.gov) stay manual --
     automatic: never added to citation_urls_for_difflab_manifest.json.
  8. OVERRIDE RE-CONFIRMATION (AUTO-5, LOW-MED -- RC-29's 4
     `monitor_url_overrides` entries were reviewed once (AuditLab,
     2026-09-23) and never revisited; auto-extend PROMOTES those stamps
     into the live verification path -- if an override target ever starts
     serving a different document, auto-extend would certify the wrong
     one under the override's authority). Fixed: each override now
     carries a clean `verified_date`; `override_needs_reconfirmation()`
     applies the SAME 90-day ceiling any manual anchor gets, not
     set-and-forget.
  9. AuditLab reviews this adversarially before the write path is built
     (this IS round 2 of that review). `_selftest()` (unconditional at
     the top of main(), same convention as build_change_events.py's
     RC-17 selftest) asserts not just `eligible is False` but WHICH
     specific check rejected (AUTOEXTEND-1: the original controls passed
     through the hash check by accident, so the mechanism they were named
     for could be deleted with the suite still green) -- one mutation-
     provable control per guardrail 3/4/5/6/8 above.

THIS SCRIPT NEVER WRITES TO A DATASET FILE.

Usage:
    python scripts/citation_auto_extend_check.py [repo_root]
    python scripts/citation_auto_extend_check.py --selftest-only
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEFAULT_TIMEOUT_S = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# AUTO-1/AUTO-5: the same 90-day figure governs both a record's own
# manual-reverify ceiling AND a monitor_url_override's re-confirmation
# cadence (RC-29's overrides are, per AUTO-5, themselves a form of manual
# anchor once auto-extend can write). Not re-derived from
# MOBILITY_VERIFICATION_TTL_DAYS or any dataset's own 30-day staleness bar
# -- this governs a different thing, how long a human's OWN sign-off may
# stand before someone looks again, independent of the public "Verified
# <date>" bar.
MANUAL_REVERIFY_CEILING_DAYS = 90


@dataclass
class FetchResult:
    ok: bool
    status: int | None
    body: bytes | None
    content_type: str | None
    error: str | None = None


@dataclass
class CandidateVerdict:
    url: str
    owner: str  # e.g. "cpe_hours.json:alabama" for a human-readable trace
    eligible: bool
    verified_method_if_extended: str | None
    reason: str
    failed_check: str | None = None  # machine-readable, for selftest assertions (AUTOEXTEND-1)


FetchFn = Callable[[str], FetchResult]


def _real_fetch(url: str) -> FetchResult:
    """The default FetchFn -- a real live HTTP GET, browser UA (matching
    this session's established practice: plain urllib without a UA gets
    403'd or blocked by several of these exact hosts, e.g. Michigan's
    bot-managed pages, NC's OAH host)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
            body = resp.read()
            return FetchResult(ok=True, status=resp.status, body=body, content_type=resp.headers.get("Content-Type"))
    except HTTPError as e:
        return FetchResult(ok=False, status=e.code, body=None, content_type=None, error=f"HTTPError {e.code}")
    except URLError as e:
        return FetchResult(ok=False, status=None, body=None, content_type=None, error=f"URLError {e.reason}")
    except Exception as e:  # noqa: BLE001 -- this is a best-effort probe, never allowed to crash the report
        return FetchResult(ok=False, status=None, body=None, content_type=None, error=f"{type(e).__name__}: {e}")


def _expected_pdf(url: str) -> bool:
    """Heuristic for "this citation is supposed to be a PDF, so its bytes
    must start with %PDF-". Deliberately conservative (only the shapes
    this repo's own citations actually use)."""
    lower = url.lower()
    if lower.endswith(".pdf"):
        return True
    # Wyoming's 3 Drive citations, per RC-29's monitor_url_overrides --
    # the download form the manifest deliberately watches instead of the
    # reader-facing viewer form.
    if "drive.google.com/uc" in lower and "export=download" in lower:
        return True
    return False


def _decode_best_effort(body: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# AUTO-3: specific, normalized claim anchors.
# ---------------------------------------------------------------------------

_DASH_CHARS = "‐‑‒–—―−"  # hyphen/figure/en/em/horizontal-bar/minus
_SECTION_WORD_RE = re.compile(r"\b(?:Section|Sec\.?)\s*", re.IGNORECASE)


def normalize_typography(s: str) -> str:
    """Canonicalize the typography AUTO-3 named specifically: every dash
    variant -> plain hyphen, "Section"/"Sec." -> "§" (matching a raw
    "§" already in the text), whitespace collapsed. Applied to BOTH the
    extracted anchor and the fetched page text before comparing, so a real
    page that prints an em-dash (Iowa's own "193A—5.3") still matches an
    anchor recorded as "193A-5.3"."""
    for d in _DASH_CHARS:
        s = s.replace(d, "-")
    s = _SECTION_WORD_RE.sub("§", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_citation_anchor(citation: str | None) -> str | None:
    """Pull a SPECIFIC section/subsection locator out of a free-text
    `citation` field, e.g. '...Rules, ch. 1, Sec 13(b) (eff. 10/28/2019)'
    -> '13(b)'. AUTO-3's evidence: a generic anchor (a bare code name like
    "4 CMC", or a bare short number) passes on navigation chrome and
    bot-wall boilerplate by coincidence -- REAL, measured against the
    actual cnmilaw.org soft-404 body. This function only ever returns a
    locator shaped like one of the three patterns AUTO-3 asked for:
      1. a number with a subsection parenthetical, e.g. "13(b)", "728(1)"
      2. a multi-part hyphenated/dotted locator, e.g. "193A-5.3",
         "339.728", "1-10-801"
      3. a section-sign-anchored number of 2+ digits, e.g. "§ 3422" -> "3422"
         -- the § itself is the specificity signal (unlike a bare
         short number floating with no context, which is exactly the "4"
         shape AUTO-3/AUTOEXTEND-1 showed matching bot-wall boilerplate
         by coincidence), so this is deliberately NOT the same risk as a
         bare code name like "4 CMC" (which has no § and is excluded).
    Anything else (a bare number with no § and no punctuation, a bare
    code/agency name, nothing at all) returns None -- no anchor -- rather
    than guess. A record whose citation prose yields no extractable
    specific locator gets NO claim-anchor and is therefore never eligible
    via evaluate_candidate() -- "unverifiable", the same honest outcome as
    a page that fails the check, not a silent skip of the guardrail.
    """
    if not citation:
        return None
    normalized = normalize_typography(citation)
    m = re.search(r"\b\d+[A-Za-z]?(?:\([a-z0-9]+\))+", normalized)
    if m:
        return m.group(0)
    m = re.search(r"\b\d+[A-Za-z]?[-.]\d[\w.\-]*\b", normalized)
    if m:
        return m.group(0)
    m = re.search(r"§\s*(\d{2,}[A-Za-z]?(?:\([a-z0-9]+\))?)", normalized)
    if m:
        return m.group(1)
    return None


def claim_anchor_for_record(dataset_filename: str, record: dict) -> str | None:
    """AUTO-2's fix: EVERY non-PDF citation needs a claim anchor, not just
    the ones this function used to derive from a bare fee/hours number
    (which AUTO-3 showed was too weak on its own). Anchors on the
    record's own `citation` prose -- present on 54/55 renewal_fees
    records per AuditLab's own count, and the same field cpe_hours.json/
    reinstatement.json carry -- via extract_citation_anchor(). Datasets
    with no `citation`-shaped field (cpa_deadlines.json, reg_change_
    events.json's conflict records) return None until this function is
    taught their own anchor source -- a real, stated gap, not silently
    treated as "no check needed"."""
    if dataset_filename in ("cpe_hours.json", "reinstatement.json", "renewal_fees.json"):
        return extract_citation_anchor(record.get("citation"))
    return None


# ---------------------------------------------------------------------------
# AUTO-1: fail-closed 90-day ceiling.
# ---------------------------------------------------------------------------

def days_since_manual_verify(last_manual_verified_date: str | None, today: date) -> int | None:
    """Reads `last_manual_verified_date` -- a field written ONLY by a
    human verification pass, never touched by an auto-extend -- NOT
    `verified_date` (which reflects whichever kind of verification
    happened most recently, auto or manual, and is exactly the field the
    pre-AUTO-1 version of this function read, which is why its ceiling
    silently disarmed itself after a record's first auto-extend)."""
    if not last_manual_verified_date:
        return None
    try:
        verified = date.fromisoformat(last_manual_verified_date)
    except ValueError:
        return None
    return (today - verified).days


def exceeds_reverify_ceiling(last_manual_verified_date: str | None, today: date) -> bool:
    """FAIL CLOSED (AUTO-1, HIGH). The pre-fix version returned `False`
    (permitted) whenever the age was unknown -- exactly the case for every
    record after its first auto-extend, since verified_date no longer
    reflected a manual date. Now: unknown or unparseable age -> ceiling
    EXCEEDED (blocked), not permitted. A record only clears the ceiling by
    having a real, recent, human-recorded date."""
    days = days_since_manual_verify(last_manual_verified_date, today)
    if days is None:
        return True
    return days > MANUAL_REVERIFY_CEILING_DAYS


def override_needs_reconfirmation(override: dict, today: date) -> bool:
    """AUTO-5: a monitor_url_override's own `verified_date` is a manual
    anchor too, once auto-extend can write -- same 90-day ceiling, not
    set-and-forget."""
    return exceeds_reverify_ceiling(override.get("verified_date"), today)


# ---------------------------------------------------------------------------
# Core eligibility check.
# ---------------------------------------------------------------------------

def evaluate_candidate(
    url: str,
    owner: str,
    last_manual_verified_date: str | None,
    manual_baseline_hash: str | None,
    manual_baseline_length: int | None,
    claim_anchor: str | None = None,
    today: date | None = None,
    fetch: FetchFn = _real_fetch,
) -> CandidateVerdict:
    """Independently re-verify a citation. Check order matters and is
    deliberate: the 90-day ceiling (AUTO-1) is checked FIRST, since an
    over-ceiling record must never reach the content checks at all --
    otherwise a passing content-shape/hash result could read as "this IS
    eligible, it's just old", when the correct reading is "this needs a
    human, full stop, independent of what a re-fetch shows." Returns
    ELIGIBLE only if every guardrail passes; otherwise UNVERIFIABLE with
    the specific reason AND a machine-readable `failed_check` (AUTOEXTEND-1:
    a selftest that only asserts `eligible is False` can't tell whether the
    check it names is the one that actually fired).
    """
    _today = today or date.today()

    if exceeds_reverify_ceiling(last_manual_verified_date, _today):
        return CandidateVerdict(
            url, owner, False, None,
            f"90-day manual-reverify ceiling exceeded or unknown (last_manual_verified_date={last_manual_verified_date!r}) -- fails closed, not eligible regardless of what a re-fetch would show",
            failed_check="ceiling",
        )

    if manual_baseline_hash is None or manual_baseline_length is None:
        return CandidateVerdict(
            url, owner, False, None,
            "no manual-anchored baseline exists yet (this record has never had a manual verification "
            "record its raw hash/length from the SAME fetch a human read -- gets one at its next STALE-20 pass)",
            failed_check="no_baseline",
        )

    result = fetch(url)
    if not result.ok or result.status != 200:
        return CandidateVerdict(url, owner, False, None, f"fetch not 200 (status={result.status}, error={result.error})", failed_check="fetch")
    if result.body is None:
        return CandidateVerdict(url, owner, False, None, "fetch returned no body", failed_check="fetch")

    is_pdf_citation = _expected_pdf(url)
    if is_pdf_citation:
        if not result.body.startswith(b"%PDF-"):
            return CandidateVerdict(
                url, owner, False, None,
                f"expected a PDF (%PDF- magic bytes) but got {result.body[:40]!r} -- this is exactly the soft-404/bot-wall shape (a 200 status with the wrong content)",
                failed_check="content_shape",
            )
    else:
        # AUTO-2/AUTO-3: every non-PDF citation MUST have a specific claim
        # anchor -- no anchor means no eligibility, full stop. This is
        # what closes AUTO-2's "130 of 181 URLs get no shape check at
        # all" gap: there is no third, unchecked path anymore.
        if not claim_anchor:
            return CandidateVerdict(
                url, owner, False, None,
                "HTML citation with no specific claim anchor available for this record (extract_citation_anchor() "
                "found nothing specific enough in its `citation` field) -- unverifiable by design, never a silent pass",
                failed_check="no_anchor",
            )
        text = normalize_typography(_decode_best_effort(result.body))
        if claim_anchor not in text:
            return CandidateVerdict(
                url, owner, False, None,
                f"claim-anchor check failed: normalized fetched page does not contain {claim_anchor!r} -- "
                f"a bot wall, soft-404, or wrong page would fail this even with a byte-identical baseline",
                failed_check="claim_anchor",
            )

    actual_length = len(result.body)
    if actual_length != manual_baseline_length:
        return CandidateVerdict(
            url, owner, False, None,
            f"byte length mismatch: fetched {actual_length}, manual-anchored baseline {manual_baseline_length}",
            failed_check="length",
        )

    actual_hash = hashlib.sha256(result.body).hexdigest()
    if actual_hash != manual_baseline_hash:
        return CandidateVerdict(
            url, owner, False, None,
            f"hash mismatch: fetched sha256={actual_hash[:16]}..., manual-anchored baseline {manual_baseline_hash[:16]}...",
            failed_check="hash",
        )

    return CandidateVerdict(
        url, owner, True, "auto_source_unchanged",
        "within the 90-day ceiling, has a manual-anchored baseline, 200, content-shape/claim-anchor matches, "
        "byte length matches, hash matches -- independently re-verified against a manual-anchored baseline",
        failed_check=None,
    )


def validate_fetch_for_anchoring(
    url: str,
    dataset_filename: str,
    record: dict,
    fetch: FetchFn = _real_fetch,
) -> tuple[bool, str]:
    """AUTO-4's process gate: whoever runs a manual verification pass
    (the STALE-20 batches, or their tooling) MUST call this on the SAME
    fetch they just read before recording it as this record's new
    manual_verified_raw_hash/manual_verified_raw_byte_length. Reuses the
    EXACT SAME content-shape/claim-anchor logic evaluate_candidate() uses
    for the ongoing check -- so a source that's walled AT ANCHOR TIME (the
    live example: Oregon's OAR, bot-walled on 6 consecutive probes) fails
    here too, and the wall can never become a trusted baseline in the
    first place. Returns (True, "") if the fetch qualifies for anchoring;
    (False, reason) otherwise -- the caller must not record a baseline in
    the False case."""
    result = fetch(url)
    if not result.ok or result.status != 200:
        return False, f"fetch not 200 (status={result.status}, error={result.error})"
    if result.body is None:
        return False, "fetch returned no body"
    if _expected_pdf(url):
        if not result.body.startswith(b"%PDF-"):
            return False, f"expected %PDF- magic bytes, got {result.body[:40]!r}"
        return True, ""
    anchor = claim_anchor_for_record(dataset_filename, record)
    if not anchor:
        return False, "no specific claim anchor available for this record"
    text = normalize_typography(_decode_best_effort(result.body))
    if anchor not in text:
        return False, f"fetched page does not contain the claim anchor {anchor!r} -- this fetch cannot be used to anchor a baseline"
    return True, ""


# ---------------------------------------------------------------------------
# Selftest / positive controls (AuditLab's required review artifact, per
# guardrail 9). Runs unconditionally at the top of main(), same convention
# as build_change_events.py's RC-17 selftest. Every control asserts WHICH
# check rejected (AUTOEXTEND-1), not just `eligible is False`, and every
# AUTO-1..5 guardrail gets its own mutation-provable control.
# ---------------------------------------------------------------------------

def _fake_fetch(status: int, body: bytes, content_type: str = "application/pdf") -> FetchFn:
    def _f(url: str) -> FetchResult:
        return FetchResult(ok=(status < 400), status=status, body=body if status < 400 else None, content_type=content_type)
    return _f


def _selftest() -> None:
    today = date(2026, 9, 23)
    recent = "2026-09-01"  # 22 days before `today` -- comfortably inside the ceiling

    real_pdf_bytes = b"%PDF-1.4\n" + b"x" * 500
    real_hash = hashlib.sha256(real_pdf_bytes).hexdigest()
    real_len = len(real_pdf_bytes)

    # --- AUTO-1: fail-closed 90-day ceiling -----------------------------
    # An auto-extended record (no last_manual_verified_date -- this IS the
    # real shape after an auto-extend, since that field is never touched
    # by one) with everything else perfect MUST still block. This is the
    # exact scenario AuditLab proved the pre-fix code got backwards.
    v = evaluate_candidate(
        "https://example.test/real.pdf", "selftest:AUTO-1-unknown-manual-date",
        last_manual_verified_date=None,
        manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, real_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is False and v.failed_check == "ceiling", f"SELFTEST FAILED (AUTO-1): unknown last_manual_verified_date was not rejected by the ceiling check -- {v}"

    # An auto-method-shaped record 91 days past its last MANUAL date MUST
    # block, even with a perfect baseline and content match.
    v = evaluate_candidate(
        "https://example.test/real.pdf", "selftest:AUTO-1-past-ceiling",
        last_manual_verified_date="2026-06-24",  # 91 days before today
        manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, real_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is False and v.failed_check == "ceiling", f"SELFTEST FAILED (AUTO-1): a record 91 days past its last manual verification was not blocked -- {v}"

    # 89 days must NOT trip the ceiling (the ceiling is real, not a
    # trivial always-block).
    v = evaluate_candidate(
        "https://example.test/real.pdf", "selftest:AUTO-1-under-ceiling",
        last_manual_verified_date="2026-06-26",  # 89 days before today
        manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, real_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is True, f"SELFTEST FAILED: a record 89 days past its last manual verification (under the 90-day ceiling) was incorrectly blocked -- {v.reason}"

    # --- guardrail 3 (AUTO-4): no manual-anchored baseline --------------
    v = evaluate_candidate(
        "https://example.test/no-baseline.pdf", "selftest:no-baseline",
        last_manual_verified_date=recent, manual_baseline_hash=None, manual_baseline_length=None, today=today,
    )
    assert v.eligible is False and v.failed_check == "no_baseline"

    # --- soft-404 / bot-wall (mandatory per the original approval), now
    # with a SELF-MATCHING baseline AND a valid recent manual date, so
    # ceiling/baseline-presence/hash/length cannot be what rejects them --
    # ONLY the claim-anchor check can (AUTOEXTEND-1's fix). ---------------
    soft_404_body = b"<html><body><h1>Index of /pdf/public_laws/13/</h1><ul><li><a href='pl13-51.pdf'>pl13-51.pdf</a></li></ul></body></html>"
    soft_404_hash = hashlib.sha256(soft_404_body).hexdigest()
    v = evaluate_candidate(
        "https://www.cnmilaw.org/pdf/public_laws/13/pl13-52", "selftest:soft-404",
        last_manual_verified_date=recent,
        manual_baseline_hash=soft_404_hash, manual_baseline_length=len(soft_404_body),
        claim_anchor="3422", today=today, fetch=_fake_fetch(200, soft_404_body, "text/html"),
    )
    assert v.eligible is False, "SELFTEST FAILED: soft-404 (real cnmilaw.org shape) was marked ELIGIBLE"
    assert v.failed_check == "claim_anchor", f"SELFTEST FAILED (AUTOEXTEND-1 regression): soft-404 was rejected, but not by the claim-anchor check -- got failed_check={v.failed_check!r}"

    bot_wall_body = b"Please enable JavaScript to view the page content. Your support ID is: 1234567890123456789"
    bot_wall_hash = hashlib.sha256(bot_wall_body).hexdigest()
    v = evaluate_candidate(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=999999", "selftest:bot-wall",
        last_manual_verified_date=recent,
        manual_baseline_hash=bot_wall_hash, manual_baseline_length=len(bot_wall_body),
        claim_anchor="801-010-0345(3)", today=today, fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "claim_anchor", f"SELFTEST FAILED (AUTOEXTEND-1 regression): bot wall was not rejected by the claim-anchor check -- {v}"

    # --- guardrail 6/AUTO-4 core scenario: baseline poisoned AT THE
    # SOURCE (hash, length AND baseline all equal the wall itself, as if
    # a manual pass had anchored a walled fetch). ONLY the claim-anchor
    # check can still catch this -- no PDF magic-byte check exists for
    # HTML, and hash/length are satisfied by construction. -------------
    v = evaluate_candidate(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=888888", "selftest:baseline-poisoned-at-source",
        last_manual_verified_date=recent,
        manual_baseline_hash=bot_wall_hash, manual_baseline_length=len(bot_wall_body),
        claim_anchor="801-010-0345(3)", today=today, fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "claim_anchor", f"SELFTEST FAILED (AUTO-4 core scenario): a wall page whose hash/length/baseline all match itself was not caught by the claim-anchor check -- {v}"

    # --- guardrail 6b (SecurityLab residual on AUTOEXTEND-1's fix,
    # _AAA_orchestrator_20260923_pdf_poison_control.md): the SAME
    # poisoning-at-source scenario as guardrail 6 above, but for a PDF
    # citation. Every poisoned-baseline control up to this point uses a
    # non-PDF URL, so the %PDF- magic-byte check (only reachable via the
    # is_pdf_citation branch) was never exercised by any of them --
    # removing it stayed green. A PDF-expected URL whose fetch AND
    # self-matching baseline are both the bot-wall body: only the %PDF-
    # check can catch this, since hash/length are satisfied by
    # construction and this branch has no claim-anchor check at all. ----
    v = evaluate_candidate(
        "https://example.test/poisoned-baseline.pdf", "selftest:pdf-baseline-poisoned-at-source",
        last_manual_verified_date=recent,
        manual_baseline_hash=bot_wall_hash, manual_baseline_length=len(bot_wall_body),
        today=today, fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "content_shape", f"SELFTEST FAILED (SecurityLab PDF-branch residual): a PDF-expected URL fetching a bot-wall body, with a self-matching poisoned baseline, was not caught by the %PDF- magic-byte check -- {v}"

    # --- length control (same ruling, "add a length control too if
    # cheap"): a real %PDF- fetch whose byte length disagrees with the
    # manual baseline. The length check runs BEFORE the hash check (see
    # evaluate_candidate's docstring on check order), so this must fail
    # specifically via "length", not "hash" -- proving the length check
    # is load-bearing on its own, not just redundant with the hash check
    # that follows it. ----------------------------------------------------
    short_pdf_bytes = b"%PDF-1.4\n" + b"x" * 100
    v = evaluate_candidate(
        "https://example.test/drifted-length.pdf", "selftest:pdf-length-mismatch",
        last_manual_verified_date=recent,
        manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, short_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is False and v.failed_check == "length", f"SELFTEST FAILED: a real PDF fetch with a byte length mismatched against the manual baseline was not caught by the length check specifically -- {v}"

    # --- AUTO-2/no-anchor-configured: an HTML citation with NO claim
    # anchor at all (extract_citation_anchor found nothing specific).
    # MUST be unverifiable, never a silent pass on hash+length alone. ---
    real_html_body = b"<html><body>Alabama requires 40 hours of CPE per fiscal year, Sec 13(b).</body></html>"
    real_html_hash = hashlib.sha256(real_html_body).hexdigest()
    v = evaluate_candidate(
        "https://example.test/real-page", "selftest:AUTO-2-no-anchor-configured",
        last_manual_verified_date=recent,
        manual_baseline_hash=real_html_hash, manual_baseline_length=len(real_html_body),
        claim_anchor=None, today=today, fetch=_fake_fetch(200, real_html_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "no_anchor", f"SELFTEST FAILED (AUTO-2): an HTML citation with no claim anchor was not rejected by the no_anchor check -- {v}"

    # --- AUTO-3: a GENERIC anchor (a bare code name) against the REAL
    # cnmilaw.org soft-404 shape -- must NOT verify. This is the exact
    # measurement AuditLab made against the real body. -------------------
    real_soft_404_cnmilaw_shape = b"<html><body><nav>4 CMC Commonwealth Code</nav><h1>Documents</h1></body></html>"
    generic_anchor = "4 CMC"
    assert generic_anchor in _decode_best_effort(real_soft_404_cnmilaw_shape), "test setup error: the generic anchor should trivially appear in the control body"
    v = evaluate_candidate(
        "https://www.cnmilaw.org/some/path", "selftest:AUTO-3-generic-anchor-must-not-pass",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(real_soft_404_cnmilaw_shape).hexdigest(), manual_baseline_length=len(real_soft_404_cnmilaw_shape),
        claim_anchor=generic_anchor, today=today, fetch=_fake_fetch(200, real_soft_404_cnmilaw_shape, "text/html"),
    )
    # This control is about extract_citation_anchor() never PRODUCING a
    # generic anchor like "4 CMC" in the first place -- proven directly
    # below, not by asserting eligible=False here (which would trivially
    # pass since baseline/claim happen to satisfy each other in this
    # synthetic case; the real protection is at extraction time).
    assert extract_citation_anchor("4 CMC § 3422") not in (None, "4 CMC"), "SELFTEST FAILED (AUTO-3): extract_citation_anchor() must never return a bare code name like '4 CMC' as an anchor"
    assert extract_citation_anchor("4 CMC § 3422") == "3422", f"SELFTEST FAILED (AUTO-3): expected the specific section number, got {extract_citation_anchor('4 CMC § 3422')!r}"
    assert extract_citation_anchor("Ala. Code § 34-1-7") == "34-1-7"
    assert extract_citation_anchor("Rules, ch. 1, Sec 13(b) (eff. 10/28/2019)") == "13(b)"
    assert extract_citation_anchor("Some Board's General Provisions") is None, "SELFTEST FAILED (AUTO-3): prose with no number at all must yield no anchor, not a guess"
    # Isolates the exact mutation shape AUTO-3 warned about: a citation
    # whose ONLY extractable token is a bare code/agency name, no section
    # sign and no subsection -- must yield NO anchor, not "4 CMC" itself.
    # (The "4 CMC § 3422" case above never exercises this path, since it
    # ALSO contains a real § locator that a correct implementation finds
    # first -- this is the control that actually isolates the bug.)
    assert extract_citation_anchor("4 CMC") is None, "SELFTEST FAILED (AUTO-3): a citation with ONLY a bare code name and no specific locator must yield no anchor"

    # --- AUTO-3 normalization control: a correct page whose section
    # prints with an em-dash must still verify (Iowa's real shape:
    # "193A—5.3" on the page vs an anchor recorded as "193A-5.3"). ---
    iowa_body = ("Iowa Admin. Code 193A—5.3 requires renewal by December 31.").encode("utf-8")
    v = evaluate_candidate(
        "https://example.test/iowa-page", "selftest:AUTO-3-normalization",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(iowa_body).hexdigest(), manual_baseline_length=len(iowa_body),
        claim_anchor="193A-5.3", today=today, fetch=_fake_fetch(200, iowa_body, "text/html"),
    )
    assert v.eligible is True, f"SELFTEST FAILED (AUTO-3 normalization): an em-dash section number on the real page did not match a hyphenated anchor -- {v.reason}"

    # --- POSITIVE CASES: genuinely unchanged citations, PDF and HTML,
    # both within the ceiling, both with a real specific anchor. MUST
    # extend. Proves the guardrails don't just reject everything. -------
    v = evaluate_candidate(
        "https://example.test/real.pdf", "selftest:genuine-match-pdf",
        last_manual_verified_date=recent,
        manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, real_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is True and v.verified_method_if_extended == "auto_source_unchanged", f"SELFTEST FAILED: a genuinely matching PDF within the ceiling was rejected -- {v.reason}"

    v = evaluate_candidate(
        "https://example.test/real-page", "selftest:genuine-match-html",
        last_manual_verified_date=recent,
        manual_baseline_hash=real_html_hash, manual_baseline_length=len(real_html_body),
        claim_anchor="13(b)", today=today, fetch=_fake_fetch(200, real_html_body, "text/html"),
    )
    assert v.eligible is True, f"SELFTEST FAILED: a genuinely matching HTML page with a real anchor was rejected -- {v.reason}"

    # --- hash/length drift (unchanged from the prior version) -----------
    drifted_bytes = b"%PDF-1.4\n" + b"y" * 500
    v = evaluate_candidate(
        "https://example.test/real.pdf", "selftest:hash-drift",
        last_manual_verified_date=recent, manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(200, drifted_bytes, "application/pdf"),
    )
    assert v.eligible is False and v.failed_check == "hash"

    v = evaluate_candidate(
        "https://example.test/down.pdf", "selftest:fetch-failure",
        last_manual_verified_date=recent, manual_baseline_hash=real_hash, manual_baseline_length=real_len,
        today=today, fetch=_fake_fetch(502, b""),
    )
    assert v.eligible is False and v.failed_check == "fetch" and "502" in v.reason

    # --- AUTO-4: validate_fetch_for_anchoring() must refuse to anchor a
    # walled fetch -- the process gate a future manual pass MUST call
    # before recording a baseline, so a wall can never become "manual". -
    ok, reason = validate_fetch_for_anchoring(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=777777",
        "reinstatement.json", {"citation": "OAR 801-010-0345(3)"},
        fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert ok is False, "SELFTEST FAILED (AUTO-4): validate_fetch_for_anchoring() approved anchoring a bot-wall fetch"
    ok, reason = validate_fetch_for_anchoring(
        "https://example.test/real-page", "reinstatement.json", {"citation": "OAR 801-010-0345(3)"},
        fetch=_fake_fetch(200, b"OAR 801-010-0345(3) sets the requirement.", "text/html"),
    )
    assert ok is True, f"SELFTEST FAILED (AUTO-4): validate_fetch_for_anchoring() rejected a genuinely valid fetch -- {reason}"

    # --- AUTO-5: override re-confirmation, same fail-closed ceiling. ----
    assert override_needs_reconfirmation({"verified_date": "2026-06-24"}, today) is True   # 91 days
    assert override_needs_reconfirmation({"verified_date": "2026-06-26"}, today) is False  # 89 days
    assert override_needs_reconfirmation({}, today) is True                                 # no date at all -> fail closed
    assert override_needs_reconfirmation({"verified_date": "not-a-date"}, today) is True    # unparseable -> fail closed

    # --- claim_anchor_for_record(): per-dataset wiring, incl. the "no
    # anchor source configured for this dataset" case AND a real,
    # acknowledged coverage gap (not every real citation format
    # extracts -- Alabama's own "30-X-5-.02" has a LETTER mid-locator,
    # which extract_citation_anchor()'s current patterns don't cover; it
    # correctly returns None -- unverifiable, not a guess -- rather than
    # a wrong extraction). ------------------------------------------------
    assert claim_anchor_for_record("cpe_hours.json", {"citation": "Ala. Code § 34-1-7"}) == "34-1-7"
    assert claim_anchor_for_record("cpe_hours.json", {"citation": "Ala. Admin. Code r. 30-X-5-.02"}) is None, "documents a real, acknowledged extractor coverage gap -- letter mid-locator formats aren't handled yet, and returning None (not a guess) is the correct behavior for them"
    assert claim_anchor_for_record("cpa_deadlines.json", {"citation": "Something 13(b)"}) is None, "SELFTEST FAILED: cpa_deadlines.json has no configured anchor source and must return None, not guess"

    # --- AUTO-4 point 3: 0 records eligible on day one, asserted, not
    # just observed. Simulates today's REAL condition (every record
    # manual, none with a manual-anchored baseline) and confirms the
    # aggregate outcome is 0 -- a future change that makes records
    # eligible sooner has to break this assertion and argue for it. -----
    day_one_records = [
        {"citation": "Ala. Code § 34-1-7", "manual_verified_raw_hash": None, "manual_verified_raw_byte_length": None, "last_manual_verified_date": "2026-09-01"},
        {"citation": "Wyo. Stat. § 33-3-116(a)(i)", "manual_verified_raw_hash": None, "manual_verified_raw_byte_length": None, "last_manual_verified_date": "2026-09-01"},
    ]
    day_one_eligible = 0
    for r in day_one_records:
        v = evaluate_candidate(
            "https://example.test/x", "x",
            last_manual_verified_date=r["last_manual_verified_date"],
            manual_baseline_hash=r["manual_verified_raw_hash"], manual_baseline_length=r["manual_verified_raw_byte_length"],
            claim_anchor=extract_citation_anchor(r["citation"]), today=today,
        )
        if v.eligible:
            day_one_eligible += 1
    assert day_one_eligible == 0, "SELFTEST FAILED (AUTO-4 point 3): a record with no manual-anchored baseline was somehow eligible -- day one must be 0"

    print("  selftest (33 assertions incl. mutation-provable controls for AUTO-1 (x3), AUTO-2, AUTO-3 (x4), "
          "AUTO-4 (x3), AUTO-5 (x4), the original soft-404/bot-wall/baseline-poisoning trio, and the "
          "PDF-branch content-shape/length controls from SecurityLab's residual): PASS")


def _load_latest_capture(repo_root: Path) -> dict | None:
    capture_dir = repo_root.parent.parent.parent / "Orchestrator" / "reg_change_events"
    if not capture_dir.exists():
        return None
    captures = sorted(capture_dir.glob("citation_freshness_citfresh_*.json"))
    if not captures:
        return None
    latest = captures[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    data["_source_file"] = latest.name
    return data


def _load_citation_records(repo_root: Path) -> dict[str, tuple[str, dict]]:
    """url -> (dataset_filename, record) for every in-scope citation, same
    enumeration preship_gate.py's check_citation_manifest_coverage() uses
    (RC-29) -- kept independent (re-reads the files itself) rather than
    importing that function, so a bug in one doesn't silently propagate
    into the other."""
    owners: dict[str, tuple[str, dict]] = {}
    for filename in ("cpa_deadlines.json", "cpe_hours.json", "reinstatement.json", "renewal_fees.json"):
        path = repo_root / "data" / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data.get("records", []):
            url = r.get("citation_url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                owners[url] = (filename, r)
    events_path = repo_root / "data" / "reg_change_events.json"
    if events_path.exists():
        events = json.loads(events_path.read_text(encoding="utf-8"))
        for e in events.get("events", []):
            if e.get("kind") != "source_conflict":
                continue
            for field_name in ("citation_url", "secondary_url"):
                url = e.get(field_name)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    owners[url] = ("reg_change_events.json", e)
    return owners


def _load_overrides(repo_root: Path) -> list[dict]:
    manifest_path = repo_root / "citation_urls_for_difflab_manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest.get("monitor_url_overrides", [])


def main() -> None:
    print("Running mandatory selftest (positive controls) before anything else...")
    _selftest()

    if len(sys.argv) > 1 and sys.argv[1] == "--selftest-only":
        return

    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    today = date.today()

    # AUTO-5: report override staleness regardless of whether a capture exists.
    overrides = _load_overrides(repo_root)
    stale_overrides = [o for o in overrides if override_needs_reconfirmation(o, today)]
    print(f"\nmonitor_url_overrides: {len(overrides)} total, {len(stale_overrides)} need re-confirmation (>90d since their own verified_date).")
    for o in stale_overrides:
        print(f"  NEEDS RECONFIRMATION  {o.get('state_slug')}  {o.get('dataset_url')}  (verified_date={o.get('verified_date')})")

    capture = _load_latest_capture(repo_root)
    if capture is None:
        print("\nNo citation_freshness capture found (Orchestrator/reg_change_events/citation_freshness_citfresh_*.json) -- nothing to evaluate.")
        return

    records_by_url = _load_citation_records(repo_root)
    print(f"\nUsing capture {capture['_source_file']} ({capture.get('checked_at', '?')}), {len(capture['results'])} URLs, {len(records_by_url)} in-scope citations known locally.")

    # DiffLab's own changed=False/raw_changed=False signal is used ONLY as
    # a cheap pre-filter (never the trust anchor -- AUTOEXTEND-1/AUTO-4).
    candidates = [r for r in capture["results"] if r.get("changed") is False and r.get("raw_changed") is False]
    print(f"DiffLab pre-filter candidates (changed=False AND raw_changed=False): {len(candidates)}")

    eligible: list[CandidateVerdict] = []
    rejected: list[CandidateVerdict] = []
    for r in candidates:
        url = r["url"]
        owner_info = records_by_url.get(url)
        if owner_info is None:
            rejected.append(CandidateVerdict(url, "(not in current dataset scope)", False, None, "orphaned or manifest-only URL, no owning record found", failed_check="no_owner"))
            continue
        dataset_filename, record = owner_info
        owner = f"{dataset_filename}:{record.get('id') or record.get('jurisdiction_slug') or '?'}"
        verdict = evaluate_candidate(
            url, owner,
            last_manual_verified_date=record.get("last_manual_verified_date"),
            manual_baseline_hash=record.get("manual_verified_raw_hash"),
            manual_baseline_length=record.get("manual_verified_raw_byte_length"),
            claim_anchor=claim_anchor_for_record(dataset_filename, record),
            today=today,
        )
        (eligible if verdict.eligible else rejected).append(verdict)

    print(f"\nIndependently re-verified: {len(eligible)} ELIGIBLE, {len(rejected)} rejected on independent re-check.")
    for v in eligible:
        print(f"  ELIGIBLE  {v.owner}  {v.url}\n            {v.reason}")
    for v in rejected:
        print(f"  REJECTED [{v.failed_check}]  {v.owner}  {v.url}\n            {v.reason}")

    print("\nThis is a REPORT ONLY. No dataset file was written. The write path (an actual verified_date "
          "bump gated on an ELIGIBLE verdict here) does not exist yet -- it ships only after AuditLab signs "
          "off on this version. Every candidate above is REJECTED today because no record has a "
          "manual-anchored baseline (last_manual_verified_date + manual_verified_raw_hash/length) yet -- "
          "that's the correct, honest state until the STALE-20 batches start recording one via "
          "validate_fetch_for_anchoring() (see STALE20_BATCH_SCHEDULE.md).")


if __name__ == "__main__":
    main()
