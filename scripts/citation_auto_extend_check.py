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
  10. IDENTITY, NOT MENTION (AUTO-6, MEDIUM, round 3 --
      `_AAA_orchestrator_20260923_ruling_AUTO6.md`). The claim-anchor
      check (guardrail 5) proves the page CONTAINS the locator string; it
      does not prove the page IS the cited document -- legal documents
      cross-reference each other constantly. AuditLab measured this on a
      REAL case: NMIAC T01-10 cites "4 CMC § 3422" five times while being
      an entirely different document. Also fixed in the same round: the
      extractor's subsection-parenthetical pattern used to match only the
      trailing parenthetical of a multi-part locator (Iowa's
      "193A-5.3(2)" extracted as just "3(2)"), which made the anchor even
      thinner than intended. Fixed: `extract_citation_anchor()` now keeps
      the FULL locator, and `evaluate_candidate()`/
      `validate_fetch_for_anchoring()` both require, in addition to the
      anchor's presence, that it prove IDENTITY -- either (a) the anchor
      sits in the page's own heading/title position (an HTML <title>/
      <h1>/<h2>, or the page's own opening window), or (b) the record's
      own cited VALUE (a fee amount or an hours count) is co-located with
      the anchor match. A mid-body mention with no value nearby -- the
      exact shape of a cross-reference -- satisfies neither and fails.
      Records that can't satisfy either path stay manual-only; that is an
      acceptable, honest gap, not a bug to work around.
  11. WRITE PATH, STAGED (_AAA_orchestrator_20260923_write_path_GO_staged.md,
      AuditLab round-3 sign-off, then Stage-A spot-check PASS +
      _AAA_orchestrator_20260923_stageB_GO_AUTO8_9.md). Stage A emits
      `auto_extend_proposals/auto_extend_proposals_<date>.json` (dataset,
      record id, URL, old verified date, proposed new date, which checks
      passed, the anchor matched) for every ELIGIBLE verdict; runs by
      default, never writes to a dataset. Stage B -- reachable ONLY via
      the `--apply` CLI flag, never the default -- applies each in-scope
      proposal via `apply_eligible_extends()`: sets the public verified-
      date field + `verified_method: "auto_source_unchanged"`, appends a
      dated `verification_history` entry, and writes BOTH `data/<file>`
      and `worker/src/<file>` byte-identically. NEVER touches
      `last_manual_verified_date` or the manual baseline fields -- an
      extend confirms an existing anchor, it doesn't re-anchor. Datasets
      outside `_PUBLIC_VERIFIED_DATE_FIELD` (reg_change_events.json, a
      BUILD OUTPUT, never hand-edited) are reported out-of-scope, not
      applied. When anything was applied, also re-runs `generate.py`
      (docs/ regen) and `preship_gate.py` (safety net) and reports both
      exit codes. Every run also logs per-dataset AND per-jurisdiction
      eligible/total coverage, so a silent zero-coverage dataset or
      jurisdiction shows up instead of being buried in a long per-URL
      list.
  12. STALE-20 <-> AUTO-EXTEND FEEDBACK LOOP
      (_AAA_orchestrator_20260923_siteB_settled_plus_batch2_anchors.md).
      Auto-extend has 0 eligible records until MANUAL anchors exist for
      it to compare against -- so from STALE-20 batch 2 onward, every
      manual re-verification pass must also record its anchor baseline.
      `build_manual_verification_update()` is that tooling: called on the
      SAME fetch the verifier just read (fetched exactly ONCE, reused for
      both the anchoring check and the recorded hash/length -- never two
      separate network round-trips that could silently diverge), it
      returns the field updates to apply. `last_manual_verified_date` is
      always set (a verifier DID look, regardless of outcome). If the
      fetch qualifies, `manual_verified_raw_hash`/
      `manual_verified_raw_byte_length`/`manual_verified_anchor` (an
      audit-trail snapshot) are recorded too. If not (walled, no anchor,
      wrong document), those three are explicitly cleared to `None` --
      never left holding a stale value from an earlier pass -- and
      `manual_verify_gap_reason` records why. Still writes nothing itself;
      the STALE-20 batch process applies the returned dict to the record.
  13. AUTO-8 (auditlab_20260923_stageA_spotcheck_PASS_plus_AUTO8.md).
      RC-29's 4 `monitor_url_overrides` targets (Wyoming's 3 Drive-
      download forms, cnmilaw.org's alias) were structurally excluded from
      auto-extend forever: the manifest deliberately monitors `monitor_url`
      instead of a record's own `citation_url` for these, so the monitored
      URL never matched an owning record and was silently discarded as
      "orphaned" (no_owner). `_load_citation_records()` now resolves
      `monitor_url -> the record owning dataset_url` -- gated by AUTO-5's
      90-day override re-confirm (a STALE override is excluded from
      resolution, never trusted). Under-extend only (safe); before this
      fix these 4 targets could never even be CONSIDERED, let alone
      wrongly extended.
  14. AUTO-9 (same report as AUTO-8). The persisted proposals JSON used
      to carry only `generated_at`/`capture_source_file`/`proposal_count`/
      `proposals` -- the rejection histogram and coverage-honesty block
      went to stdout only, so `proposal_count: 0` with an empty array was
      indistinguishable, from the artifact alone, between "correct" and
      "the pipeline fetched nothing." Now also persists
      `candidate_total`, `rejection_summary` (the `failed_check` counts),
      and `coverage` (by_dataset, by_jurisdiction,
      zero_coverage_jurisdictions) in the same JSON, so a reviewer can
      verify a zero reconciles without re-running anything.
  15. AUTO-10 (_AAA_orchestrator_20260923_AUTO10_plus_apply_rules.md).
      RC-29's four override records anchor to a URL that can't carry
      their baseline: batch 2 was headed to fetch each record's own
      `citation_url` -- a Google Drive viewer shell for Wyoming's 3, an
      alias for the NMI one -- which carries no `%PDF-` bytes and no
      claim anchor, so `build_manual_verification_update()` would record
      every baseline field `None` with a gap reason that reads as "this
      source is unverifiable" when the real document is sitting at the
      override target. Verified live against the real Wyoming record:
      anchoring by `citation_url` -> every field `None`; anchoring by the
      override's `monitor_url` -> a real hash/length/anchor that matches
      AuditLab's own independently-recorded CITE-76 baseline exactly.
      Fixed: `build_manual_verification_update()` takes an `overrides`
      list (pass `_load_overrides(repo_root)`); when a
      `monitor_url_overrides` entry's `dataset_url` matches the URL
      passed in, its `monitor_url` is fetched and anchored instead, and
      `manual_verify_fetched_url` records which URL the anchor actually
      came from -- set ONLY when an override was used, so it never adds
      noise to the other ~297 normal records.
  16. APPLY-1 (SecurityLab, orchestrator-ruled HIGH, amended 04:50,
      _AAA_orchestrator_20260923_APPLY1_code_enforce.md). Rule 1 below
      used to be a COMMENT ONLY -- this repo's own 08-18 admin-digest
      incident proved that doesn't hold (a "HELD pending review" comment
      ran on every cron tick for 8 days because nothing in the code
      actually checked it). `--apply` now fails closed in code:
      `check_apply_approval()` requires BOTH (a) an approval token --
      `AUTO_EXTEND_APPLY_APPROVED` env var, or the one-shot file
      `auto_extend_proposals/.apply_approved` -- that matches
      `compute_proposals_tag()` (sha256 of THIS run's exact proposal set)
      EXACTLY, granted by Orchestrator only after AuditLab's PASS on that
      specific proposals file, AND (b) an interactive TTY. NO bypass
      either way: a non-interactive invocation refuses even with a valid
      token (a cron can export an env var as easily as pass a flag), and
      a missing/mismatched/stale token refuses regardless of TTY. A
      refused apply prints why, writes zero dataset files, and the
      process exits non-zero.

STANDING RULES FOR --apply (_AAA_orchestrator_20260923_AUTO10_plus_
apply_rules.md + APPLY-1's amendment above, until told otherwise):
  1. No scheduler, cron, loop, or watchdog may call --apply -- NOW CODE-
     ENFORCED (APPLY-1, item 16 above), not just a standing rule.
  2. The FIRST --apply run with more than 0 proposals: stop after the
     write, BEFORE commit/deploy. Send AuditLab the proposals JSON plus
     the diff to check against live sources. Commit only on its PASS.
     (Procedural -- not mechanically enforceable in this script, per
     AuditLab's own correction: it's about not committing prematurely,
     not a per-record cap.)
  3. Every applied run is exactly one commit, so any run can be reverted.

THIS SCRIPT NEVER WRITES TO A DATASET FILE UNLESS RUN WITH --apply.
Without that flag (the default), its only write is Stage A's proposals
JSON, a report artifact, never data/*.json or worker/src/*.json.

Usage:
    python scripts/citation_auto_extend_check.py [repo_root]              # Stage A only, never writes a dataset
    python scripts/citation_auto_extend_check.py [repo_root] --apply      # Stage A + Stage B apply -- BY HAND ONLY, see standing rules above
    python scripts/citation_auto_extend_check.py --selftest-only
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
    # AUTO-6 (round 3): a subsection parenthetical stacked directly onto a
    # multi-part hyphenated/dotted prefix -- e.g. Iowa's "193A-5.3(2)" --
    # used to match only the trailing "3(2)", losing the "193A-5." that
    # makes the locator specific. The optional (?:[-.]\d[\w.\-]*)* group
    # absorbs that prefix into the SAME match before the mandatory
    # parenthetical, so the anchor returned is always the FULL locator,
    # never a truncated tail of it.
    m = re.search(r"\b\d+[A-Za-z]?(?:[-.]\d[\w.\-]*)*(?:\([a-z0-9]+\))+", normalized)
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
# AUTO-6: identity, not mention. A claim anchor proves the page CONTAINS
# the locator string; it does not prove the page IS the cited document --
# legal documents cross-reference each other constantly (AuditLab's
# measured case: NMIAC T01-10 cites "4 CMC § 3422" five times while being
# a different document entirely). Two ways a fetched page can prove
# identity rather than mere mention:
#   1. the anchor sits in a heading/title position -- an HTML <title>/
#      <h1>/<h2>, or within the first _HEADING_WINDOW_CHARS characters of
#      the page (the plain-text/no-markup equivalent of "this is the
#      document's own opening heading", not a paragraph deep in its body).
#   2. the record's own cited VALUE (a fee amount, an hours count) appears
#      within a short window of the anchor match -- a coincidence a real
#      cross-reference essentially never shares, unlike the locator number
#      itself, which appears in every citation TO the document as well as
#      every citation OF it.
# A page satisfying neither -- the anchor mentioned once, mid-body, with
# no value nearby -- fails. That is the exact shape of a cross-reference.
# ---------------------------------------------------------------------------

_HEADING_WINDOW_CHARS = 400
_VALUE_PROXIMITY_CHARS = 150
_HTML_HEADING_TAG_RE = re.compile(r"<(title|h1|h2)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)


def _heading_text(text: str) -> str:
    """Best-effort (regex, no HTML parser dependency) extraction of
    <title>/<h1>/<h2> contents, plus the page's own opening window -- the
    two positions AUTO-6 treats as "this is the document's own heading",
    as opposed to anywhere else in the body."""
    tags = " ".join(m.group(2) for m in _HTML_HEADING_TAG_RE.finditer(text))
    return tags + " " + text[:_HEADING_WINDOW_CHARS]


def _anchor_proves_identity(anchor: str, text: str, cited_value: str | None) -> bool:
    """AUTO-6's core check. True only if the anchor sits in a heading/
    title position, OR the record's cited value is co-located with a
    genuine occurrence of the anchor. False for a mid-body mention with no
    value nearby -- a cross-reference, not the document itself."""
    if anchor in _heading_text(text):
        return True
    if cited_value:
        for m in re.finditer(re.escape(anchor), text):
            window = text[max(0, m.start() - _VALUE_PROXIMITY_CHARS): m.end() + _VALUE_PROXIMITY_CHARS]
            if cited_value in window:
                return True
    return False


def cited_value_for_record(dataset_filename: str, record: dict) -> str | None:
    """AUTO-6's second identity path: the record's own cited VALUE (a fee
    amount or an hours count), which -- unlike the locator number itself --
    is unlikely to also appear near a mere cross-reference to that locator.
    Only wired for datasets with a single unambiguous numeric value;
    returns None (no second identity path available, heading position
    becomes the record's ONLY path) for anything else -- a real, stated
    gap, not a guess."""
    if dataset_filename == "cpe_hours.json":
        value = record.get("total_hours")
    elif dataset_filename == "renewal_fees.json":
        value = record.get("fee_usd")
    elif dataset_filename == "reinstatement.json":
        value = record.get("reinstatement_fee_usd")
    else:
        value = None
    return None if value is None else str(value)


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
    cited_value: str | None = None,
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
        # AUTO-6: mentioning the anchor is not enough -- legal documents
        # cross-reference each other constantly (NMIAC T01-10 cites
        # "4 CMC § 3422" five times while being a different document). The
        # anchor must prove the page IS the cited document: a heading/
        # title position, or the record's own cited value co-located with
        # the match.
        if not _anchor_proves_identity(claim_anchor, text, cited_value):
            return CandidateVerdict(
                url, owner, False, None,
                f"claim-anchor {claim_anchor!r} appears on the page but not as its own heading/title, and the "
                f"record's cited value is not co-located with it -- this proves the page MENTIONS the locator, "
                f"not that it IS the cited document (AUTO-6)",
                failed_check="identity",
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
    cited_value = cited_value_for_record(dataset_filename, record)
    if not _anchor_proves_identity(anchor, text, cited_value):
        return False, (
            f"claim anchor {anchor!r} appears on the page but not as its own heading/title, and the record's "
            f"cited value is not co-located with it (AUTO-6) -- this fetch cannot be used to anchor a baseline"
        )
    return True, ""


# ---------------------------------------------------------------------------
# STALE-20 batch tooling entry point (ruling:
# _AAA_orchestrator_20260923_siteB_settled_plus_batch2_anchors.md --
# "from STALE-20 batch 2 onward, every manual re-verification must also
# record its anchor baseline"). Auto-extend has 0 eligible records until
# MANUAL anchors exist for something to compare against; this is the
# other half of that loop.
# ---------------------------------------------------------------------------

def build_manual_verification_update(
    url: str,
    dataset_filename: str,
    record: dict,
    today: date | None = None,
    fetch: FetchFn = _real_fetch,
    overrides: list[dict] | None = None,
) -> dict:
    """Called by whoever runs a STALE-20 manual re-verification pass, on
    the SAME fetch they just read (fetched exactly ONCE here and reused
    for both the anchoring check and the hash/length recording -- AUTO-4's
    whole point is that these must be the identical bytes a human/agent
    actually looked at, never two separate network round-trips that could
    silently diverge). Returns the field updates to apply to the record;
    never writes to a file itself.

    AUTO-10: if `overrides` (pass `_load_overrides(repo_root)`) contains a
    `monitor_url_overrides` entry whose `dataset_url` equals `url`, the
    OVERRIDE's `monitor_url` is fetched and anchored instead of `url`
    itself. For RC-29's four targets (Wyoming's 3 Drive forms,
    cnmilaw.org's alias) the citation_url is a viewer page that carries no
    fetchable `%PDF-` bytes and no claim anchor -- measured live,
    AuditLab's shipped-function repro: anchoring by citation_url yields
    every baseline field `None` with gap_reason "fetched page does not
    contain the claim anchor", which reads as "this source is
    unverifiable" when the real document is sitting at the override
    target. Makes the override map authoritative in both directions --
    owner lookup (AUTO-8) and anchoring (AUTO-10), not just the gate.

    Always sets `last_manual_verified_date` = today -- a verifier DID read
    this page right now, regardless of whether it can be anchored for
    auto-extend. If the fetch qualifies (`validate_fetch_for_anchoring()`
    approves), also sets `manual_verified_raw_hash`/
    `manual_verified_raw_byte_length` (the baseline auto-extend's ongoing
    check compares against) and `manual_verified_anchor` (an audit-trail
    snapshot of the specific identity anchor confirmed at verification
    time -- eligibility checks always recompute the anchor fresh from the
    record's own `citation` text; this snapshot is never itself trusted as
    a substitute). When an override was used, also sets
    `manual_verify_fetched_url` to the URL actually fetched -- so an
    override-anchored record visibly says which URL its baseline came
    from, rather than silently implying it was `citation_url`. Left out
    entirely for the (overwhelming majority) normal case, so it never adds
    noise to a record that never needed an override.

    If the fetch does NOT qualify (walled, no anchor available, wrong
    document), the three baseline fields are explicitly set to `None` --
    never left holding an OLDER value from a previous pass, so a source
    that stops being anchorable can't keep pretending it still is -- and
    `manual_verify_gap_reason` carries why, so 'recorded as manual-only'
    is visible in the data, not just implied by absence."""
    _today = today or date.today()
    actual_url = url
    for o in (overrides or []):
        if o.get("dataset_url") == url:
            actual_url = o.get("monitor_url") or url
            break

    update: dict = {"last_manual_verified_date": _today.isoformat()}
    if actual_url != url:
        update["manual_verify_fetched_url"] = actual_url

    result = fetch(actual_url)  # the ONE fetch -- reused below, never re-fetched
    cached_fetch: FetchFn = lambda _url: result

    ok, reason = validate_fetch_for_anchoring(actual_url, dataset_filename, record, fetch=cached_fetch)
    if ok and result.body is not None:
        update["manual_verified_raw_hash"] = hashlib.sha256(result.body).hexdigest()
        update["manual_verified_raw_byte_length"] = len(result.body)
        update["manual_verified_anchor"] = claim_anchor_for_record(dataset_filename, record)
        update["manual_verify_gap_reason"] = None
    else:
        update["manual_verified_raw_hash"] = None
        update["manual_verified_raw_byte_length"] = None
        update["manual_verified_anchor"] = None
        update["manual_verify_gap_reason"] = reason
    return update


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

    # --- AUTO-6 (round 3, _AAA_orchestrator_20260923_ruling_AUTO6.md):
    # full-locator extraction fix. A subsection parenthetical stacked onto
    # a multi-part hyphenated/dotted prefix used to match only the
    # trailing parenthetical -- Iowa's real "193A-5.3(2)" extracted as
    # just "3(2)", losing the "193A-5." that makes it specific. ----------
    assert extract_citation_anchor("Iowa Admin. Code r. 193A-5.3(2)") == "193A-5.3(2)", f"SELFTEST FAILED (AUTO-6): expected the FULL locator, got {extract_citation_anchor('Iowa Admin. Code r. 193A-5.3(2)')!r}"
    assert extract_citation_anchor("25 GAR § 2103(d)(2)") == "2103(d)(2)", "SELFTEST FAILED (AUTO-6 regression): a parenthetical-only locator with no hyphenated prefix must still extract correctly"

    # --- AUTO-6: identity, not mention. A claim anchor proves the page
    # CONTAINS the locator; it does not prove the page IS the cited
    # document -- legal documents cross-reference each other constantly.
    # Three controls built from AuditLab's real, measured cases. Each uses
    # a SELF-MATCHING poisoned baseline (same pattern as guardrail 6) so
    # ceiling/baseline/length/hash cannot be what rejects them -- ONLY the
    # identity check can. -------------------------------------------------

    # Case 1 (NMIAC): NMIAC T01-10 cites "4 CMC § 3422" five times in
    # running prose while being an entirely different document -- the
    # exact case AuditLab measured against the real body.
    nmiac_heading = "NMIAC Title 1, Chapter 10 - Board of Accountancy"
    nmiac_filler = ("This chapter establishes procedures for professional licensing and renewal within the Commonwealth. " * 8)
    nmiac_crossref = "Substantial equivalence (see section 6 of the Act [4 CMC § 3422]) requires that an applicant demonstrate equivalent qualifications before licensure is granted."
    nmiac_body = f"<html><head><title>{nmiac_heading}</title></head><body><h1>{nmiac_heading}</h1><p>{nmiac_filler}</p><p>{nmiac_crossref}</p></body></html>".encode("utf-8")
    nmiac_anchor = extract_citation_anchor("4 CMC § 3422 (CPE Requirements)")
    assert nmiac_anchor == "3422"
    v = evaluate_candidate(
        "https://www.cnmilaw.org/nmiac/title01/chapter10", "selftest:AUTO-6-nmiac-crossref",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(nmiac_body).hexdigest(), manual_baseline_length=len(nmiac_body),
        claim_anchor=nmiac_anchor, cited_value=cited_value_for_record("cpe_hours.json", {"total_hours": 40}),
        today=today, fetch=_fake_fetch(200, nmiac_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "identity", f"SELFTEST FAILED (AUTO-6, NMIAC cross-reference): a document that only MENTIONS the cited locator mid-body was not caught by the identity check -- {v}"

    # Case 2 (Washington, CITE-66): a real, documented failure shape -- a
    # host serves the PARENT CHAPTER at 200 for a missing/renumbered
    # section, and the specific section appears only in a table-of-
    # contents-style list, never as its own heading.
    wa_heading = "Chapter 4-30 WAC—Public Accountants"
    wa_filler = ("This chapter contains the rules adopted by the Washington State Board of Accountancy governing licensure, renewal, and continuing education for certified public accountants. " * 6)
    wa_toc = "Sections in this chapter include 4-30-130, 4-30-131, 4-30-132, 4-30-133, 4-30-134(3), and 4-30-135, each governing a distinct aspect of licensure."
    wa_body = f"<html><head><title>{wa_heading}</title></head><body><h1>{wa_heading}</h1><p>{wa_filler}</p><p>{wa_toc}</p></body></html>".encode("utf-8")
    wa_anchor = extract_citation_anchor("Wash. Admin. Code 4-30-134(3)")
    assert wa_anchor == "4-30-134(3)"
    v = evaluate_candidate(
        "https://apps.leg.wa.gov/wac/default.aspx?cite=4-30-134", "selftest:AUTO-6-washington-parent-chapter",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(wa_body).hexdigest(), manual_baseline_length=len(wa_body),
        claim_anchor=wa_anchor, cited_value=cited_value_for_record("renewal_fees.json", {"fee_usd": 100}),
        today=today, fetch=_fake_fetch(200, wa_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "identity", f"SELFTEST FAILED (AUTO-6, Washington parent-chapter): a parent-chapter page listing the section only in a TOC was not caught by the identity check -- {v}"

    # Case 3 (cnmilaw sibling path, RC-20): the host serves an adjacent
    # section (§ 3742) at the URL for a different one (§ 3743), mentioning
    # the requested locator only as a repeal cross-reference.
    cn_heading = "§ 3742. Continuing Education Requirements."
    cn_filler = ("This section governs the continuing education requirements applicable to licensees prior to the 2019 amendments to the Commonwealth Code. " * 6)
    cn_crossref = "This section has been repealed and replaced by § 3743, effective January 1, 2020."
    cn_body = f"<html><head><title>{cn_heading}</title></head><body><h1>{cn_heading}</h1><p>{cn_filler}</p><p>{cn_crossref}</p></body></html>".encode("utf-8")
    cn_anchor = extract_citation_anchor("4 CMC § 3743")
    assert cn_anchor == "3743"
    v = evaluate_candidate(
        "https://www.cnmilaw.org/pdf/public_laws/13/pl13-3743", "selftest:AUTO-6-cnmilaw-sibling-path",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(cn_body).hexdigest(), manual_baseline_length=len(cn_body),
        claim_anchor=cn_anchor, cited_value=cited_value_for_record("reinstatement.json", {"reinstatement_fee_usd": 75}),
        today=today, fetch=_fake_fetch(200, cn_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "identity", f"SELFTEST FAILED (AUTO-6, cnmilaw sibling path): a sibling section serving a repeal cross-reference to the requested locator was not caught by the identity check -- {v}"

    # --- AUTO-6 POSITIVE controls: the identity check must not just
    # reject everything -- proves both paths genuinely work, not just the
    # negative side. --------------------------------------------------
    # Path 1 (heading/title): the anchor legitimately IS the page's own
    # section heading.
    pos_heading = "§ 3422. Substantial Equivalence."
    pos_body_text = "This section establishes the standard for substantial equivalence applicable to out-of-state licensees seeking reciprocal recognition within the Commonwealth."
    pos_heading_body = f"<html><head><title>{pos_heading}</title></head><body><h1>{pos_heading}</h1><p>{pos_body_text}</p></body></html>".encode("utf-8")
    v = evaluate_candidate(
        "https://www.cnmilaw.org/nmiac/title04/chapter30/section3422", "selftest:AUTO-6-positive-heading",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(pos_heading_body).hexdigest(), manual_baseline_length=len(pos_heading_body),
        claim_anchor="3422", cited_value=None,
        today=today, fetch=_fake_fetch(200, pos_heading_body, "text/html"),
    )
    assert v.eligible is True, f"SELFTEST FAILED (AUTO-6 positive, heading path): a page whose own <title>/<h1> IS the cited section was incorrectly rejected -- {v.reason}"

    # Path 2 (value-nearby): no title/h1 markup at all (a plain-text-shaped
    # page), anchor pushed well past the heading window, but the record's
    # own cited value (40 hours) sits right next to it.
    pos_filler = ("This is a plain-text regulatory page with no title or heading markup at all, describing general licensure procedures for certified public accountants in the state. " * 4)
    pos_tail = "The continuing education requirement under Section 13(b) is 40 hours annually, to be completed prior to each renewal cycle."
    pos_value_body = (pos_filler + pos_tail).encode("utf-8")
    v = evaluate_candidate(
        "https://example.test/plain-value-page", "selftest:AUTO-6-positive-value-nearby",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(pos_value_body).hexdigest(), manual_baseline_length=len(pos_value_body),
        claim_anchor="13(b)", cited_value=cited_value_for_record("cpe_hours.json", {"total_hours": 40}),
        today=today, fetch=_fake_fetch(200, pos_value_body, "text/html"),
    )
    assert v.eligible is True, f"SELFTEST FAILED (AUTO-6 positive, value-nearby path): a page with no heading markup but the record's cited value co-located with the anchor was incorrectly rejected -- {v.reason}"
    # Same page, but WITHOUT the cited value wired in -- proves this
    # specific control genuinely depends on the value-nearby path, not on
    # the heading-window fallback (the anchor is well past
    # _HEADING_WINDOW_CHARS in this body).
    v = evaluate_candidate(
        "https://example.test/plain-value-page", "selftest:AUTO-6-value-nearby-isolation",
        last_manual_verified_date=recent,
        manual_baseline_hash=hashlib.sha256(pos_value_body).hexdigest(), manual_baseline_length=len(pos_value_body),
        claim_anchor="13(b)", cited_value=None,
        today=today, fetch=_fake_fetch(200, pos_value_body, "text/html"),
    )
    assert v.eligible is False and v.failed_check == "identity", f"SELFTEST FAILED (AUTO-6 isolation control): without a cited value, an anchor past the heading window must still fail identity -- {v}"

    # --- SecurityLab's site-B control (AuditLab's correction: there are
    # TWO _expected_pdf(url) call sites -- evaluate_candidate's (site A,
    # already covered above) and validate_fetch_for_anchoring's own (site
    # B). The original selftest's only control exercising this function
    # used a non-PDF URL, so site B's %PDF- check could be removed
    # silently.) A PDF-expected anchoring fetch that returns a bot-wall
    # body must be refused, not accepted as a baseline. -------------------
    ok, reason = validate_fetch_for_anchoring(
        "https://example.test/poisoned-anchor.pdf", "cpe_hours.json", {"citation": "Ala. Code § 34-1-7"},
        fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert ok is False, f"SELFTEST FAILED (SecurityLab site-B control): validate_fetch_for_anchoring() approved anchoring a PDF-expected URL that returned a bot-wall body instead of %PDF- bytes -- {reason}"

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

    # --- AUTO-6 on validate_fetch_for_anchoring() specifically (not just
    # evaluate_candidate()): the SAME identity check must gate the
    # anchoring-time function too, or a cross-reference-laden page could
    # be recorded as a trusted MANUAL baseline in the first place --
    # worse than a bad auto-extend, since it would then carry manual
    # authority forever. Reuses the real NMIAC cross-reference body. -----
    ok, reason = validate_fetch_for_anchoring(
        "https://www.cnmilaw.org/nmiac/title01/chapter10", "cpe_hours.json",
        {"citation": "4 CMC § 3422 (CPE Requirements)"},
        fetch=_fake_fetch(200, nmiac_body, "text/html"),
    )
    assert ok is False, f"SELFTEST FAILED (AUTO-6 on validate_fetch_for_anchoring): a cross-reference-only mention of the cited locator was approved for anchoring a baseline -- {reason}"

    # --- build_manual_verification_update() -- the STALE-20 batch tooling
    # entry point (_AAA_orchestrator_20260923_siteB_settled_plus_batch2_
    # anchors.md). Counts fetch calls to prove SINGLE-FETCH reuse: the
    # bytes used for the anchoring check and the recorded hash/length MUST
    # be identical, never two separate network round-trips. -------------
    def _counting_fetch(status: int, body: bytes, content_type: str = "application/pdf"):
        calls = {"n": 0}
        def _f(url: str) -> FetchResult:
            calls["n"] += 1
            return FetchResult(ok=(status < 400), status=status, body=body if status < 400 else None, content_type=content_type)
        return _f, calls

    # Anchorable case: a real PDF fetch. Baseline fields recorded, gap
    # reason None, and exactly ONE fetch call.
    counting, calls = _counting_fetch(200, real_pdf_bytes, "application/pdf")
    update = build_manual_verification_update(
        "https://example.test/real.pdf", "cpe_hours.json", {"citation": "Ala. Code § 34-1-7"},
        today=today, fetch=counting,
    )
    assert calls["n"] == 1, f"SELFTEST FAILED (build_manual_verification_update): expected exactly 1 fetch call, got {calls['n']} -- the anchoring check must reuse the SAME fetch, not re-fetch"
    assert update["last_manual_verified_date"] == today.isoformat()
    assert update["manual_verified_raw_hash"] == hashlib.sha256(real_pdf_bytes).hexdigest()
    assert update["manual_verified_raw_byte_length"] == len(real_pdf_bytes)
    assert update["manual_verified_anchor"] == "34-1-7"
    assert update["manual_verify_gap_reason"] is None, f"SELFTEST FAILED (build_manual_verification_update): an anchorable fetch left a gap reason -- {update['manual_verify_gap_reason']!r}"

    # Non-anchorable case (bot wall): last_manual_verified_date is STILL
    # set (a verifier DID look), but baseline fields are explicitly None,
    # not left stale, and a gap reason is recorded. Still exactly ONE
    # fetch call.
    counting2, calls2 = _counting_fetch(200, bot_wall_body, "text/html")
    update2 = build_manual_verification_update(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=555555",
        "reinstatement.json", {"citation": "OAR 801-010-0345(3)"},
        today=today, fetch=counting2,
    )
    assert calls2["n"] == 1, f"SELFTEST FAILED (build_manual_verification_update): expected exactly 1 fetch call for the non-anchorable case too, got {calls2['n']}"
    assert update2["last_manual_verified_date"] == today.isoformat(), "SELFTEST FAILED (build_manual_verification_update): a verifier who read a walled page must still get credit for last_manual_verified_date"
    assert update2["manual_verified_raw_hash"] is None
    assert update2["manual_verified_raw_byte_length"] is None
    assert update2["manual_verified_anchor"] is None
    assert update2["manual_verify_gap_reason"], "SELFTEST FAILED (build_manual_verification_update): a non-anchorable fetch must record WHY, not leave a silent gap"

    # Stale-field control: a record with an OLD baseline from a prior pass
    # must have it explicitly cleared (None), not left stale, when THIS
    # pass finds the source no longer anchorable.
    update3 = build_manual_verification_update(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=444444",
        "reinstatement.json",
        {"citation": "OAR 801-010-0345(3)", "manual_verified_raw_hash": "stale-hash-from-a-prior-pass", "manual_verified_raw_byte_length": 12345},
        today=today, fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert update3["manual_verified_raw_hash"] is None, "SELFTEST FAILED (build_manual_verification_update): a now-unanchorable source must CLEAR a stale hash from a prior pass, not leave it"
    assert update3["manual_verified_raw_byte_length"] is None

    # --- AUTO-10 (auditlab_20260923_anchor_tooling_reviewed_plus_AUTO10.md):
    # build_manual_verification_update() must anchor against a
    # monitor_url_overrides entry's monitor_url, not the record's own
    # citation_url, when one exists for it -- RC-29's four targets
    # (Wyoming's 3 Drive forms, cnmilaw.org's alias) have a citation_url
    # that's a viewer shell carrying no fetchable %PDF- bytes and no claim
    # anchor; the real document is at the override's monitor_url. This
    # control is shaped exactly like the real Wyoming case (verified
    # separately against the LIVE record -- same hash/length/anchor
    # AuditLab's own independent CITE-76 baseline recorded). ---------------
    def _url_aware_fetch(url_to_body: dict[str, tuple[int, bytes, str]]):
        calls: dict = {"n": 0, "urls": []}
        def _f(u: str) -> FetchResult:
            calls["n"] += 1
            calls["urls"].append(u)
            status, body, ct = url_to_body.get(u, (404, b"", "text/html"))
            return FetchResult(ok=(status < 400), status=status, body=body if status < 400 else None, content_type=ct)
        return _f, calls

    wyo_viewer_url = "https://drive.google.com/file/d/WYOTEST/view?usp=sharing"
    wyo_download_url = "https://drive.google.com/uc?export=download&id=WYOTEST"
    wyo_viewer_body = b"<html><body>Google Drive preview shell -- no document content, just chrome</body></html>"
    wyo_pdf_bytes = b"%PDF-1.4\n" + b"Wyo. Bd. of Certified Public Accountants Rules content " + b"x" * 400
    wyo_record = {"citation": "Wyo. Bd. of Certified Public Accountants Rules, ch. 1, Sec 13(b) (eff. 10/28/2019)"}
    wyo_overrides = [{"state_slug": "wyoming", "dataset_url": wyo_viewer_url, "monitor_url": wyo_download_url, "verified_date": today.isoformat()}]

    fetch10, calls10 = _url_aware_fetch({
        wyo_viewer_url: (200, wyo_viewer_body, "text/html"),
        wyo_download_url: (200, wyo_pdf_bytes, "application/pdf"),
    })
    update10 = build_manual_verification_update(wyo_viewer_url, "renewal_fees.json", wyo_record, today=today, fetch=fetch10, overrides=wyo_overrides)
    assert calls10["n"] == 1, f"SELFTEST FAILED (AUTO-10): expected exactly 1 fetch call, got {calls10['n']}"
    assert calls10["urls"] == [wyo_download_url], f"SELFTEST FAILED (AUTO-10): expected the override's monitor_url to be fetched, not the citation_url viewer page -- got {calls10['urls']}"
    assert update10["manual_verify_fetched_url"] == wyo_download_url, f"SELFTEST FAILED (AUTO-10): manual_verify_fetched_url must record which URL the anchor actually came from -- got {update10.get('manual_verify_fetched_url')!r}"
    assert update10["manual_verified_raw_hash"] == hashlib.sha256(wyo_pdf_bytes).hexdigest()
    assert update10["manual_verified_raw_byte_length"] == len(wyo_pdf_bytes)
    assert update10["manual_verified_anchor"] == "13(b)"
    assert update10["manual_verify_gap_reason"] is None, f"SELFTEST FAILED (AUTO-10): an override-anchored record must not carry a gap reason -- {update10['manual_verify_gap_reason']!r}"

    # Regression baseline: the SAME record/url WITHOUT overrides passed
    # must reproduce AuditLab's exact pre-fix measurement (every baseline
    # field None, a gap reason naming the citation_url's own shortfall) --
    # proves this control isn't accidentally passing for an unrelated
    # reason, and that overrides=None (the old call shape) is still safe,
    # not a crash.
    fetch10b, _ = _url_aware_fetch({wyo_viewer_url: (200, wyo_viewer_body, "text/html")})
    update10b = build_manual_verification_update(wyo_viewer_url, "renewal_fees.json", wyo_record, today=today, fetch=fetch10b, overrides=None)
    assert update10b["manual_verified_raw_hash"] is None and update10b["manual_verify_gap_reason"], "SELFTEST FAILED (AUTO-10 regression baseline): without overrides, the viewer-shell citation_url must still fail to anchor (reproducing the pre-fix bug) -- if this now passes, the test setup itself is wrong"
    assert "manual_verify_fetched_url" not in update10b, "SELFTEST FAILED (AUTO-10): the normal (no-override) case must not carry manual_verify_fetched_url at all -- it should never add noise to a record that never needed an override"

    # --- AUTO-5: override re-confirmation, same fail-closed ceiling. ----
    assert override_needs_reconfirmation({"verified_date": "2026-06-24"}, today) is True   # 91 days
    assert override_needs_reconfirmation({"verified_date": "2026-06-26"}, today) is False  # 89 days
    assert override_needs_reconfirmation({}, today) is True                                 # no date at all -> fail closed
    assert override_needs_reconfirmation({"verified_date": "not-a-date"}, today) is True    # unparseable -> fail closed

    # --- AUTO-8: monitor_url_overrides resolved into the owner index, so
    # an override target (a monitored URL that deliberately differs from
    # its record's own citation_url -- RC-29's Wyoming Drive-download
    # forms, cnmilaw.org's alias) can be found by auto-extend instead of
    # being silently discarded as "orphaned" (no_owner). Gated by AUTO-5:
    # a STALE override must NOT resolve. Built against a synthetic repo
    # layout in a temp dir, not the real data files, so this is a pure
    # regression control independent of production content. -------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "data").mkdir()
        fresh_record = {"id": "wy-cpe", "state_slug": "wyoming", "citation_url": "https://drive.google.com/file/d/ABC123/view?usp=sharing", "citation": "Wyo. Rules ch. 5 § 3"}
        (tmp_root / "data" / "cpe_hours.json").write_text(json.dumps({"records": [fresh_record]}), encoding="utf-8")
        for empty_ds in ("cpa_deadlines.json", "reinstatement.json", "renewal_fees.json"):
            (tmp_root / "data" / empty_ds).write_text(json.dumps({"records": []}), encoding="utf-8")
        manifest = {
            "monitor_url_overrides": [
                {
                    "state_slug": "wyoming",
                    "dataset_url": "https://drive.google.com/file/d/ABC123/view?usp=sharing",
                    "monitor_url": "https://drive.google.com/uc?export=download&id=ABC123",
                    "verified_date": today.isoformat(),
                },
                {
                    "state_slug": "wyoming-stale",
                    "dataset_url": "https://drive.google.com/file/d/ABC123/view?usp=sharing",
                    "monitor_url": "https://drive.google.com/uc?export=download&id=STALE999",
                    "verified_date": "2026-01-01",
                },
            ]
        }
        (tmp_root / "citation_urls_for_difflab_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        owners = _load_citation_records(tmp_root, today=today)
        assert "https://drive.google.com/uc?export=download&id=ABC123" in owners, "SELFTEST FAILED (AUTO-8): a fresh override's monitor_url did not resolve to its dataset_url's owning record"
        resolved_dataset, resolved_record = owners["https://drive.google.com/uc?export=download&id=ABC123"]
        assert resolved_dataset == "cpe_hours.json" and resolved_record.get("id") == "wy-cpe", f"SELFTEST FAILED (AUTO-8): resolved to the wrong record -- {resolved_dataset}, {resolved_record.get('id')}"
        assert "https://drive.google.com/uc?export=download&id=STALE999" not in owners, "SELFTEST FAILED (AUTO-8/AUTO-5 gate): a STALE override (>90d since its own verified_date) resolved anyway -- must be excluded, not trusted"

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

    # --- Stage B: apply_eligible_extends() -- the highest-stakes function
    # in this file, since it's the one that actually writes production
    # datasets. Built against a synthetic repo layout in a temp dir, never
    # against real data. Covers: correct field updates, last_manual_
    # verified_date/manual baseline fields UNTOUCHED, verification_history
    # appended (not overwritten), both copies byte-identical, an
    # out-of-scope dataset is reported not applied, and a no-proposals run
    # touches no file at all. ---------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        (tmp_root / "data").mkdir()
        (tmp_root / "worker" / "src").mkdir(parents=True)
        sb_record = {
            "id": "sb-test-cpe", "state_slug": "sb-test-state", "citation": "Ala. Code § 34-1-7",
            "citation_url": "https://example.test/real.pdf", "verified_date": "2026-09-01",
            "verified_method": "manual", "last_manual_verified_date": "2026-09-01",
            "manual_verified_raw_hash": "abc123", "manual_verified_raw_byte_length": 509,
            "verification_history": "2026-09-01 (manual pass): confirmed unchanged.",
        }
        sb_dataset = {"records": [sb_record]}
        sb_serialized = json.dumps(sb_dataset, indent=2, ensure_ascii=False) + "\n"
        (tmp_root / "data" / "cpe_hours.json").write_text(sb_serialized, encoding="utf-8")
        (tmp_root / "worker" / "src" / "cpe_hours.json").write_text(sb_serialized, encoding="utf-8")

        sb_proposal = {
            "dataset_filename": "cpe_hours.json", "record_id": "sb-test-cpe", "state_slug": "sb-test-state",
            "url": "https://example.test/real.pdf", "old_verified_date": "2026-09-01",
            "proposed_verified_date": today.isoformat(), "proposed_verified_method": "auto_source_unchanged",
            "checks_passed": ["ceiling", "baseline_presence", "fetch_200", "content_shape_pdf_magic_bytes", "length_match", "hash_match"],
            "anchor_matched": None,
        }
        out_of_scope_proposal = {"dataset_filename": "reg_change_events.json", "record_id": "some-event", "state_slug": "x", "url": "https://example.test/y"}

        sb_applied, sb_out_of_scope = apply_eligible_extends(tmp_root, [sb_proposal, out_of_scope_proposal], today)

        assert len(sb_applied) == 1 and sb_applied[0]["record_id"] == "sb-test-cpe", f"SELFTEST FAILED (Stage B): expected exactly 1 in-scope proposal applied, got {sb_applied}"
        assert len(sb_out_of_scope) == 1 and sb_out_of_scope[0]["dataset_filename"] == "reg_change_events.json", f"SELFTEST FAILED (Stage B): reg_change_events.json proposal was not reported out-of-scope -- {sb_out_of_scope}"

        sb_data_after = json.loads((tmp_root / "data" / "cpe_hours.json").read_text(encoding="utf-8"))
        sb_mirror_after = (tmp_root / "worker" / "src" / "cpe_hours.json").read_text(encoding="utf-8")
        sb_r = sb_data_after["records"][0]
        assert sb_r["verified_date"] == today.isoformat(), f"SELFTEST FAILED (Stage B): verified_date was not bumped -- {sb_r['verified_date']!r}"
        assert sb_r["verified_method"] == "auto_source_unchanged", f"SELFTEST FAILED (Stage B): verified_method not set correctly -- {sb_r['verified_method']!r}"
        assert sb_r["last_manual_verified_date"] == "2026-09-01", "SELFTEST FAILED (Stage B): last_manual_verified_date must NEVER be touched by an auto-extend -- AUTO-1's whole ceiling depends on this staying a human-only field"
        assert sb_r["manual_verified_raw_hash"] == "abc123", "SELFTEST FAILED (Stage B): the manual baseline hash must not be touched by an apply -- it wasn't re-anchored, only confirmed"
        assert "2026-09-01 (manual pass): confirmed unchanged." in sb_r["verification_history"], "SELFTEST FAILED (Stage B): the prior verification_history entry was overwritten, not appended to"
        assert f"{today.isoformat()} (auto-extend, auto_source_unchanged)" in sb_r["verification_history"], "SELFTEST FAILED (Stage B): no new verification_history entry documenting the extend was appended"
        assert json.dumps(sb_data_after, indent=2, ensure_ascii=False) + "\n" == sb_mirror_after, "SELFTEST FAILED (Stage B): data/ and worker/src/ copies are not byte-identical after apply"

        # No-op control: an empty proposals list must not touch a SINGLE
        # file -- proven by pointing at a repo_root whose data/ files
        # don't even exist; if apply_eligible_extends() ever unconditionally
        # tried to read/write, this would raise FileNotFoundError.
        empty_applied, empty_out_of_scope = apply_eligible_extends(Path(tmp) / "does-not-exist", [], today)
        assert empty_applied == [] and empty_out_of_scope == [], "SELFTEST FAILED (Stage B): an empty proposals list must return empty results"

    # --- APPLY-1 (SecurityLab, orchestrator-ruled HIGH, amended 04:50):
    # check_apply_approval() -- BOTH a matching per-run token AND an
    # interactive TTY are required, no bypass either way. The 4 controls
    # the amended ruling asks for explicitly: no token -> refused;
    # wrong/stale token -> refused; valid token + non-TTY -> refused;
    # valid token + TTY -> approved. env var and approval FILE both
    # tested as token sources. os.environ is saved/restored around every
    # case so this control can't leak state into anything else. ----------
    _apply1_env_backup = os.environ.get(AUTO_EXTEND_APPLY_APPROVED_ENV)
    try:
        fake_proposals = [{"dataset_filename": "cpe_hours.json", "record_id": "x"}]
        real_tag = compute_proposals_tag(fake_proposals)
        stale_tag = compute_proposals_tag([{"dataset_filename": "cpe_hours.json", "record_id": "y"}])
        assert real_tag != stale_tag, "test setup error: two different proposal sets must hash differently"

        with tempfile.TemporaryDirectory() as apply_tmp:
            apply_repo_root = Path(apply_tmp)
            (apply_repo_root / "auto_extend_proposals").mkdir()

            # 1. No token anywhere (env unset, no approval file) -> refused.
            os.environ.pop(AUTO_EXTEND_APPLY_APPROVED_ENV, None)
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=True)
            assert ok is False and reason, "SELFTEST FAILED (APPLY-1): no approval token anywhere must be refused"

            # 2. Wrong/stale token (a real token, but for a DIFFERENT
            # proposal set) -> refused. This is "a stale approval can't
            # authorize new proposals," proven directly, not assumed.
            os.environ[AUTO_EXTEND_APPLY_APPROVED_ENV] = stale_tag
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=True)
            assert ok is False, f"SELFTEST FAILED (APPLY-1): a stale token (for a different proposal set) must be refused, not accepted -- {reason}"

            # 3. Valid token (env) + NON-interactive -> refused. No bypass,
            # per the amendment -- this is the control it explicitly asked for.
            os.environ[AUTO_EXTEND_APPLY_APPROVED_ENV] = real_tag
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=False)
            assert ok is False, f"SELFTEST FAILED (APPLY-1): a valid token with a non-interactive (non-TTY) invocation must still be refused -- {reason}"

            # 4. Valid token (env) + interactive TTY -> approved.
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=True)
            assert ok is True, f"SELFTEST FAILED (APPLY-1): a valid token with an interactive TTY must be approved -- {reason}"

            # 5. Valid token via the approval FILE (not env) + TTY -> approved.
            # This is the mechanism Orchestrator actually uses per the
            # amendment ("take the token from an approval file").
            os.environ.pop(AUTO_EXTEND_APPLY_APPROVED_ENV, None)
            (apply_repo_root / AUTO_EXTEND_APPLY_APPROVED_FILE).write_text(real_tag, encoding="utf-8")
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=True)
            assert ok is True, f"SELFTEST FAILED (APPLY-1): a valid approval FILE token with a TTY must be approved -- {reason}"

            # 6. A stale FILE token (for a different proposal set) -> refused.
            (apply_repo_root / AUTO_EXTEND_APPLY_APPROVED_FILE).write_text(stale_tag, encoding="utf-8")
            ok, reason = check_apply_approval(apply_repo_root, real_tag, stdin_isatty=True)
            assert ok is False, "SELFTEST FAILED (APPLY-1): a stale approval FILE token must be refused too, not just a stale env token"
    finally:
        if _apply1_env_backup is None:
            os.environ.pop(AUTO_EXTEND_APPLY_APPROVED_ENV, None)
        else:
            os.environ[AUTO_EXTEND_APPLY_APPROVED_ENV] = _apply1_env_backup

    print("  selftest (89 assertions incl. mutation-provable controls for AUTO-1 (x3), AUTO-2, AUTO-3 (x4), "
          "AUTO-4 (x3), AUTO-5 (x4), AUTO-6 (x11: full-locator extraction, 3 real-case cross-reference "
          "negatives, 2 positive identity paths, 1 isolation control, 1 on validate_fetch_for_anchoring "
          "specifically), AUTO-8 (x3: override resolution, correct-record, stale-gate), AUTO-10 (x9: "
          "override-aware anchoring matching the live Wyoming case, single-fetch, regression baseline, "
          "no-noise-on-normal-records), Stage B's apply_eligible_extends() (x9: field updates, "
          "last_manual_verified_date/baseline untouched, history appended not overwritten, byte-identical "
          "copies, out-of-scope reporting, no-op safety), APPLY-1 (x6: no token, stale env token, valid "
          "token + non-TTY, valid token + TTY, valid file token, stale file token), the original "
          "soft-404/bot-wall/baseline-poisoning trio, the PDF-branch content-shape/length controls, "
          "SecurityLab's site-B anchoring-path control, and build_manual_verification_update()'s "
          "single-fetch/stale-field controls (x14)): PASS")


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


def _load_citation_records(repo_root: Path, today: date | None = None) -> dict[str, tuple[str, dict]]:
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

    # AUTO-8: resolve RC-29's monitor_url_overrides into the SAME owner
    # index. The manifest deliberately watches `monitor_url` instead of a
    # record's own `citation_url` for these four targets (Wyoming's 3
    # Drive-download forms, cnmilaw.org's alias) -- without this
    # resolution step, the monitored URL never matches an owning record
    # and is silently discarded as "orphaned" (no_owner), structurally
    # excluding those records from auto-extend forever. Gated by AUTO-5's
    # 90-day override re-confirm: a STALE override is excluded here, not
    # resolved -- fail safe, never trust an unconfirmed mapping to route
    # eligibility to a record.
    _today = today or date.today()
    for o in _load_overrides(repo_root):
        if override_needs_reconfirmation(o, _today):
            continue
        dataset_url = o.get("dataset_url")
        monitor_url = o.get("monitor_url")
        if not isinstance(dataset_url, str) or not isinstance(monitor_url, str):
            continue
        owner_info = owners.get(dataset_url)
        if owner_info is not None:
            owners[monitor_url] = owner_info
    return owners


def _load_overrides(repo_root: Path) -> list[dict]:
    manifest_path = repo_root / "citation_urls_for_difflab_manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest.get("monitor_url_overrides", [])


def _public_verified_date_for_record(record: dict) -> str | None:
    """The human-facing 'as of' date this record shows today -- field name
    varies by dataset (`verified_date` vs `last_verified`). Report-only
    (Stage A's proposal 'old date' column); never used by eligibility
    logic itself, which reads `last_manual_verified_date` exclusively
    (AUTO-1)."""
    return record.get("verified_date") or record.get("last_verified")


def _checks_passed_for_eligible(url: str, claim_anchor: str | None) -> list[str]:
    """Stage A's proposal report: the ordered list of guardrails an
    ELIGIBLE verdict passed, matching evaluate_candidate()'s actual check
    order for this citation's shape (PDF vs HTML)."""
    checks = ["ceiling", "baseline_presence", "fetch_200"]
    if _expected_pdf(url):
        checks.append("content_shape_pdf_magic_bytes")
    else:
        checks.append("claim_anchor_present")
        checks.append("identity_heading_or_value")
    checks += ["length_match", "hash_match"]
    return checks


# ---------------------------------------------------------------------------
# Stage B: apply (_AAA_orchestrator_20260923_stageB_GO_AUTO8_9.md --
# "Stage B may proceed under the terms of the staged GO: one commit per
# run, verified_method: auto_source_unchanged, last_manual_verified_date
# untouched, preship + identical copies green"). Only reachable via the
# --apply CLI flag; the default run stays Stage-A-only (report, no
# writes), matching this whole feature's every prior default.
# ---------------------------------------------------------------------------

# Scope-limited to the 4 hand-maintained datasets. reg_change_events.json
# is a BUILD OUTPUT (regenerated by build_change_events.py from
# mobility_rules.json), never hand-edited -- an eligible verdict against
# it is reported as out-of-scope, not applied.
_PUBLIC_VERIFIED_DATE_FIELD = {
    "cpa_deadlines.json": "last_verified",
    "cpe_hours.json": "verified_date",
    "reinstatement.json": "last_verified",
    "renewal_fees.json": "verified_date",
}


def apply_eligible_extends(repo_root: Path, proposals: list[dict], today: date) -> tuple[list[dict], list[dict]]:
    """Applies each ELIGIBLE proposal to its owning dataset record. For
    each in-scope proposal: sets the public verified-date field (
    `verified_date` or `last_verified`, per dataset) + `verified_method:
    "auto_source_unchanged"`, and appends a dated `verification_history`
    entry documenting the extend -- same free-text-log convention every
    existing entry in that field already uses. NEVER touches
    `last_manual_verified_date` or the `manual_verified_raw_*` baseline
    fields -- this didn't re-anchor anything, it confirmed the existing
    anchor still matches.

    Writes the FULL dataset back to BOTH `data/<file>` and
    `worker/src/<file>`, using the exact `json.dumps(indent=2,
    ensure_ascii=False) + '\\n'` round-trip independently verified to
    reproduce every existing dataset file byte-for-byte when unchanged --
    so a diff shows ONLY the records actually touched, never a reformat
    of the whole file. Writes nothing at all if `proposals` is empty (a
    no-op run leaves the repo untouched).

    Returns (applied, out_of_scope) -- both lists of proposal dicts
    (applied ones carry the old date value too), for the run's
    report/commit message."""
    by_dataset: dict[str, list[dict]] = {}
    out_of_scope: list[dict] = []
    for p in proposals:
        ds = p["dataset_filename"]
        if ds not in _PUBLIC_VERIFIED_DATE_FIELD:
            out_of_scope.append(p)
            continue
        by_dataset.setdefault(ds, []).append(p)

    applied: list[dict] = []
    for dataset_filename, ds_proposals in by_dataset.items():
        data_path = repo_root / "data" / dataset_filename
        mirror_path = repo_root / "worker" / "src" / dataset_filename
        data = json.loads(data_path.read_text(encoding="utf-8"))
        by_id = {r.get("id"): r for r in data.get("records", [])}
        field = _PUBLIC_VERIFIED_DATE_FIELD[dataset_filename]

        for p in ds_proposals:
            record = by_id.get(p["record_id"])
            if record is None:
                # Shouldn't happen -- this proposal came from THIS dataset
                # moments ago in the same run. Fail loud rather than
                # silently skip a record we can no longer find.
                raise RuntimeError(
                    f"Stage B: proposal record_id {p['record_id']!r} not found in {dataset_filename} -- "
                    f"the dataset changed between Stage A and Stage B within this same run"
                )
            old_value = record.get(field)
            record[field] = today.isoformat()
            record["verified_method"] = "auto_source_unchanged"
            entry = (
                f"{today.isoformat()} (auto-extend, auto_source_unchanged): independently re-fetched "
                f"citation_url, byte-identical (hash + length match) to the manual-anchored baseline "
                f"recorded {record.get('last_manual_verified_date', '?')}. No new human read; "
                f"last_manual_verified_date unchanged."
            )
            existing_history = record.get("verification_history") or ""
            record["verification_history"] = (existing_history + "\n\n" + entry) if existing_history else entry
            applied.append({**p, "old_public_verified_date_field": field, "old_public_verified_date_value": old_value})

        serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        data_path.write_text(serialized, encoding="utf-8")
        mirror_path.write_text(serialized, encoding="utf-8")

    return applied, out_of_scope


# ---------------------------------------------------------------------------
# APPLY-1 (SecurityLab, orchestrator-ruled HIGH,
# _AAA_orchestrator_20260923_APPLY1_code_enforce.md): --apply must fail
# closed in CODE, not just in a standing-rules comment. This repo's own
# 08-18 admin-digest incident proved a comment-only "HELD pending review"
# doesn't hold -- it ran on every cron tick for 8 days anyway, because
# nothing in the code actually checked it (worker/src/env.ts's own
# docstring). Same shape of fix as requireSendApproval() on the Worker
# side, adapted for a local script (SecurityLab's own refinement: an
# env-var allowlist alone doesn't transfer -- a cron entry can export a
# var exactly as easily as it can pass --apply; a LOCAL script's only
# real signal that a human is present is an interactive TTY).
# ---------------------------------------------------------------------------

AUTO_EXTEND_APPLY_APPROVED_ENV = "AUTO_EXTEND_APPLY_APPROVED"
AUTO_EXTEND_APPLY_APPROVED_FILE = "auto_extend_proposals/.apply_approved"


def compute_proposals_tag(proposals: list[dict]) -> str:
    """A tamper-evident tag for THIS run's exact proposal set -- sha256 of
    its canonical (sorted-keys) JSON serialization. A stale approval
    token, granted for an earlier/different proposal set, will not match
    a changed one even if the record COUNT happens to be identical --
    "A stale approval can't authorize new proposals" (the ruling's exact
    requirement)."""
    canonical = json.dumps(proposals, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_apply_approval(repo_root: Path, proposals_tag: str, stdin_isatty: bool) -> tuple[bool, str]:
    """Belt and braces, BOTH required: (1) an approval token -- from
    AUTO_EXTEND_APPLY_APPROVED (env) or a one-shot approval file -- must
    equal proposals_tag EXACTLY, granted by Orchestrator only after
    AuditLab's PASS on this specific proposals file; (2) stdin must be a
    TTY, since the standing rule is that --apply is run BY HAND ONLY,
    never from a scheduler/cron/loop/watchdog -- a leaked or copied token
    alone must not be enough to authorize a write from an unattended
    process. Returns (True, "") only if both hold; otherwise (False,
    reason) and the caller MUST NOT write anything."""
    env_token = os.environ.get(AUTO_EXTEND_APPLY_APPROVED_ENV)
    token_matched = bool(env_token) and env_token == proposals_tag
    token_source = "env" if token_matched else None

    if not token_matched:
        approval_path = repo_root / AUTO_EXTEND_APPLY_APPROVED_FILE
        if approval_path.exists():
            file_token = approval_path.read_text(encoding="utf-8").strip()
            if file_token == proposals_tag:
                token_matched = True
                token_source = "file"

    if not token_matched:
        return False, (
            f"no approval token matches this run's proposals (tag={proposals_tag[:16]}...) -- set "
            f"{AUTO_EXTEND_APPLY_APPROVED_ENV} or write {AUTO_EXTEND_APPLY_APPROVED_FILE} with the "
            f"EXACT tag Orchestrator grants after AuditLab's PASS on this specific proposals file. "
            f"Refused, no dataset file touched."
        )
    if not stdin_isatty:
        return False, (
            f"approval token matched (source={token_source}) but stdin is not a TTY -- --apply is run "
            f"BY HAND ONLY, never from a scheduler/cron/loop/watchdog, even with a valid token. "
            f"Refused, no dataset file touched."
        )
    return True, ""


def main() -> int:
    print("Running mandatory selftest (positive controls) before anything else...")
    _selftest()

    args = sys.argv[1:]
    if "--selftest-only" in args:
        return 0
    apply_mode = "--apply" in args
    positional = [a for a in args if not a.startswith("--")]
    repo_root = Path(positional[0]) if positional else Path(__file__).resolve().parent.parent
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
        return 0

    records_by_url = _load_citation_records(repo_root, today=today)
    print(f"\nUsing capture {capture['_source_file']} ({capture.get('checked_at', '?')}), {len(capture['results'])} URLs, {len(records_by_url)} in-scope citations known locally.")

    # DiffLab's own changed=False/raw_changed=False signal is used ONLY as
    # a cheap pre-filter (never the trust anchor -- AUTOEXTEND-1/AUTO-4).
    candidates = [r for r in capture["results"] if r.get("changed") is False and r.get("raw_changed") is False]
    print(f"DiffLab pre-filter candidates (changed=False AND raw_changed=False): {len(candidates)}")

    eligible: list[CandidateVerdict] = []
    rejected: list[CandidateVerdict] = []
    proposals: list[dict] = []
    # Coverage honesty (item 3 of the write-path staging ruling): per-
    # dataset AND per-jurisdiction eligible/total counts, so a silent
    # zero-coverage dataset or state shows up instead of being buried in a
    # long per-URL list.
    dataset_coverage: dict[str, dict[str, int]] = {}
    state_coverage: dict[str, dict[str, int]] = {}
    failed_check_counts: dict[str, int] = {}

    for r in candidates:
        url = r["url"]
        owner_info = records_by_url.get(url)
        if owner_info is None:
            v = CandidateVerdict(url, "(not in current dataset scope)", False, None, "orphaned or manifest-only URL, no owning record found", failed_check="no_owner")
            rejected.append(v)
            failed_check_counts["no_owner"] = failed_check_counts.get("no_owner", 0) + 1
            continue
        dataset_filename, record = owner_info
        state_slug = record.get("state_slug") or record.get("jurisdiction_slug") or "(no state_slug)"
        owner = f"{dataset_filename}:{record.get('id') or record.get('jurisdiction_slug') or '?'}"
        claim_anchor = claim_anchor_for_record(dataset_filename, record)
        verdict = evaluate_candidate(
            url, owner,
            last_manual_verified_date=record.get("last_manual_verified_date"),
            manual_baseline_hash=record.get("manual_verified_raw_hash"),
            manual_baseline_length=record.get("manual_verified_raw_byte_length"),
            claim_anchor=claim_anchor,
            cited_value=cited_value_for_record(dataset_filename, record),
            today=today,
        )

        dcov = dataset_coverage.setdefault(dataset_filename, {"total": 0, "eligible": 0})
        dcov["total"] += 1
        scov = state_coverage.setdefault(state_slug, {"total": 0, "eligible": 0})
        scov["total"] += 1

        if verdict.eligible:
            eligible.append(verdict)
            dcov["eligible"] += 1
            scov["eligible"] += 1
            # Stage A: proposal file only, changes NOTHING. No open(...,'w'),
            # no dataset write anywhere in this branch -- just a report row.
            proposals.append({
                "dataset_filename": dataset_filename,
                "record_id": record.get("id") or record.get("jurisdiction_slug") or "?",
                "state_slug": state_slug,
                "url": url,
                "old_verified_date": _public_verified_date_for_record(record),
                "proposed_verified_date": today.isoformat(),
                "proposed_verified_method": "auto_source_unchanged",
                "checks_passed": _checks_passed_for_eligible(url, claim_anchor),
                "anchor_matched": claim_anchor,
            })
        else:
            rejected.append(verdict)
            failed_check_counts[verdict.failed_check or "?"] = failed_check_counts.get(verdict.failed_check or "?", 0) + 1

    print(f"\nIndependently re-verified: {len(eligible)} ELIGIBLE, {len(rejected)} rejected on independent re-check.")
    for v in eligible:
        print(f"  ELIGIBLE  {v.owner}  {v.url}\n            {v.reason}")
    for v in rejected:
        print(f"  REJECTED [{v.failed_check}]  {v.owner}  {v.url}\n            {v.reason}")

    print("\nCoverage honesty (per dataset):")
    for dataset_filename in sorted(dataset_coverage):
        c = dataset_coverage[dataset_filename]
        flag = "  <-- ZERO COVERAGE" if c["eligible"] == 0 and c["total"] > 0 else ""
        print(f"  {dataset_filename}: {c['eligible']}/{c['total']} eligible{flag}")

    zero_coverage_states = sorted(s for s, c in state_coverage.items() if c["eligible"] == 0 and c["total"] > 0)
    print(f"\nCoverage honesty (per jurisdiction): {len(state_coverage)} jurisdictions with in-scope candidates this run, "
          f"{len(zero_coverage_states)} at ZERO eligible coverage.")
    if zero_coverage_states:
        print("  ZERO-COVERAGE JURISDICTIONS: " + ", ".join(zero_coverage_states))

    rejection_summary = dict(sorted(failed_check_counts.items()))
    print("\nRejection reasons (failed_check counts): " + ", ".join(f"{k}={v}" for k, v in rejection_summary.items()))

    # Stage B (AUTO-8/9 ruling): apply, ONLY behind --apply. Default run
    # stays Stage-A-only -- report, no writes -- matching this whole
    # feature's every prior default. APPLY-1: even with --apply and a
    # non-empty proposal set, nothing is written without a matching,
    # per-run approval token AND an interactive TTY (check_apply_approval).
    applied: list[dict] = []
    out_of_scope: list[dict] = []
    proposals_tag = compute_proposals_tag(proposals)
    apply_refused_reason: str | None = None
    if apply_mode and proposals:
        approved, reason = check_apply_approval(repo_root, proposals_tag, sys.stdin.isatty())
        if not approved:
            apply_refused_reason = reason
            print(f"\nStage B: REFUSED (APPLY-1). {reason}")
        else:
            applied, out_of_scope = apply_eligible_extends(repo_root, proposals, today)
            print(f"\nStage B: applied {len(applied)} extend(s) to dataset files (verified_method=auto_source_unchanged, "
                  f"last_manual_verified_date untouched).")
            for a in applied:
                print(f"  APPLIED  {a['dataset_filename']}:{a['record_id']}  {a['old_public_verified_date_field']} "
                      f"{a['old_public_verified_date_value']} -> {today.isoformat()}")
            if out_of_scope:
                print(f"  {len(out_of_scope)} eligible proposal(s) OUT OF STAGE-B SCOPE (not a hand-maintained "
                      f"dataset -- reg_change_events.json is a build output, never hand-edited): " +
                      ", ".join(f"{o['dataset_filename']}:{o['record_id']}" for o in out_of_scope))

            if applied:
                print("\nRegenerating docs/ from the updated dataset(s) (generate.py)...")
                gen = subprocess.run([sys.executable, str(repo_root / "generate.py")], cwd=repo_root, capture_output=True, text=True)
                print(f"generate.py exit={gen.returncode}")
                if gen.returncode != 0:
                    print(gen.stdout[-4000:])
                    print(gen.stderr[-4000:])

                print("\nRunning preship_gate.py to confirm the applied changes are still green...")
                gate = subprocess.run([sys.executable, str(repo_root / "scripts" / "preship_gate.py")], cwd=repo_root, capture_output=True, text=True)
                print(f"preship_gate.py exit={gate.returncode}")
                if gate.returncode != 0:
                    print(gate.stdout[-4000:])
                    print(gate.stderr[-4000:])
    elif apply_mode:
        print("\nStage B: --apply passed, but 0 proposals this run -- nothing to apply, no dataset file touched.")

    # AUTO-9: persist rejection_summary, the candidate total, and the
    # coverage-honesty block (incl. zero-coverage jurisdictions) in the
    # SAME JSON as the proposals -- not just stdout. A reviewer must be
    # able to tell a correct zero from a broken one without re-running
    # anything (AuditLab's exact framing).
    proposals_dir = repo_root / "auto_extend_proposals"
    proposals_dir.mkdir(exist_ok=True)
    proposals_path = proposals_dir / f"auto_extend_proposals_{today.isoformat()}.json"
    proposals_path.write_text(json.dumps({
        "generated_at": today.isoformat(),
        "capture_source_file": capture["_source_file"],
        "candidate_total": len(candidates),
        "proposal_count": len(proposals),
        "proposals": proposals,
        "proposals_tag": proposals_tag,
        "rejection_summary": rejection_summary,
        "coverage": {
            "by_dataset": dataset_coverage,
            "by_jurisdiction": state_coverage,
            "zero_coverage_jurisdictions": zero_coverage_states,
        },
        "stage_b_applied_count": len(applied),
        "stage_b_out_of_scope_count": len(out_of_scope),
        "stage_b_refused_reason": apply_refused_reason,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {proposals_path} (proposals + rejection_summary + coverage + proposals_tag, AUTO-9/"
          f"APPLY-1). This is the only report artifact this run writes; dataset writes (if any) happened "
          f"only via Stage B above, exactly {len(applied)} record(s), only when --apply was passed AND "
          f"approved.")

    if not apply_mode:
        print("\nThis is a REPORT ONLY (Stage A, no --apply passed). Every candidate above is REJECTED today "
              "because no record has a manual-anchored baseline (last_manual_verified_date + "
              "manual_verified_raw_hash/length) yet -- that's the correct, honest state until the STALE-20 "
              "batches start recording one via validate_fetch_for_anchoring() (see STALE20_BATCH_SCHEDULE.md).")

    return 1 if apply_refused_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
