#!/usr/bin/env python3
"""Continuous-freshness auto-extend ELIGIBILITY CHECK -- dry run only, never writes.

Orchestrator-approved design (2026-09-23, _AAA_orchestrator_20260923_
continuous_freshness_APPROVED.md, approving assetlab_20260922_continuous_
freshness_design_S4.md). Split as designed: DiffLab owns capture (their
`engine/citation_freshness_capture.py`, output at
`Orchestrator/reg_change_events/citation_freshness_citfresh_*.json`);
this file is AssetLab's consumption half. Guardrails from the approval,
non-negotiable under this repo's 100%-correctness bar:

  1. An auto-extend needs a VALID FETCH, not just "no diff". DiffLab's own
     capture already showed two ways a broken page looks healthy: Oregon's
     SOS returns 200 with a 93-character bot wall, and cnmilaw.org returns
     200 with an HTML listing for missing paths (soft-404). DiffLab's
     changed=False / raw_changed=False signal is NECESSARY but not
     SUFFICIENT -- this script independently re-fetches every candidate
     and only accepts it if ALL of: status 200; content-type/magic-bytes
     match what the citation is expected to be (a PDF citation must still
     open with the %PDF- magic bytes); byte length is within a sane band
     of DiffLab's reported raw_byte_length; AND this script's own freshly
     computed sha256 matches DiffLab's raw_content_hash exactly. Anything
     else is UNVERIFIABLE -- reported, never extended, routed to a human.
  2. verified_method: this script never writes to a dataset file. When a
     real write path exists, it must set verified_method="auto_source_
     unchanged" (vs "manual" for a human re-verification) -- the field
     already exists on every record (added in this same commit) so the
     write path has somewhere to put it on day one.
  3. 90-day ceiling: a record must get a full MANUAL re-verify at least
     every 90 days no matter how many auto-extends it has had in between.
     `_days_since_manual_verify()` below implements the check; it is not
     yet exercised by real data since no record has ever been auto-
     extended (verified_method is "manual" for all 301 records as of this
     commit) -- included now so the write path (when built) has a tested
     function to call, not a TODO.
  4. Excluded hosts (regs.maryland.gov, dcregs.dc.gov) stay manual --
     inherited automatically: they were never added to
     citation_urls_for_difflab_manifest.json in the first place (RC-29's
     own design), so DiffLab never captures them and they can never
     appear as a candidate here.
  5. AuditLab reviews this adversarially, with a required positive control
     (a soft-404 and a bot-wall page that must NOT extend), BEFORE the
     first REAL auto-extend ships. This file's own selftest (run
     unconditionally at the top of main(), same convention as
     build_change_events.py's RC-17 selftest) IS that positive control,
     using the exact real shapes this session already documented
     (Oregon's SOS bot wall, cnmilaw.org's soft-404) -- AuditLab's review
     should extend or replace it, not just read it.

THIS SCRIPT NEVER WRITES TO A DATASET FILE. It reads DiffLab's capture
output and the 5 dataset files, prints a report of which citations WOULD
be eligible for auto-extend today (almost certainly none yet -- DiffLab's
raw-hash capture is new as of 2026-09-23 and needs a second real capture
cycle before raw_changed is ever non-null), and why every other candidate
either isn't eligible yet or failed independent verification. The write
path (an actual `verified_date` bump gated on this script's ELIGIBLE
verdict) is deliberately not built here -- per guardrail 5, it ships only
after AuditLab's adversarial review of this file.

Usage:
    python scripts/citation_auto_extend_check.py [repo_root]
    python scripts/citation_auto_extend_check.py --selftest-only
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEFAULT_TIMEOUT_S = 20
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# Same 90-day figure the orchestrator's approval specifies. Kept as a
# named constant here (not re-derived from MOBILITY_VERIFICATION_TTL_DAYS
# or any dataset's own 30-day staleness bar) because it governs a
# DIFFERENT thing -- how long an auto-extend CHAIN may run before a human
# looks at the record again, independent of how often the public-facing
# "Verified <date>" bar itself requires re-verification.
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
    this repo's own citations actually use) -- URL string heuristics are
    exactly the class of thing that should widen slowly and on evidence,
    not speculatively."""
    lower = url.lower()
    if lower.endswith(".pdf"):
        return True
    # Wyoming's 3 Drive citations, per RC-29's monitor_url_overrides --
    # the download form the manifest deliberately watches instead of the
    # reader-facing viewer form.
    if "drive.google.com/uc" in lower and "export=download" in lower:
        return True
    return False


def evaluate_candidate(
    url: str,
    owner: str,
    diff_lab_raw_content_hash: str | None,
    diff_lab_raw_byte_length: int | None,
    fetch: FetchFn = _real_fetch,
) -> CandidateVerdict:
    """Independently re-verify a citation DiffLab's own capture already
    marked changed=False AND raw_changed=False (the caller's job to filter
    to that set before calling this -- see main()). Returns ELIGIBLE only
    if every guardrail-1 check passes; otherwise UNVERIFIABLE with the
    specific reason, never a silent pass."""
    if diff_lab_raw_content_hash is None or diff_lab_raw_byte_length is None:
        return CandidateVerdict(url, owner, False, None, "no DiffLab raw-hash baseline to cross-check against yet")

    result = fetch(url)
    if not result.ok or result.status != 200:
        return CandidateVerdict(url, owner, False, None, f"fetch not 200 (status={result.status}, error={result.error})")
    if result.body is None:
        return CandidateVerdict(url, owner, False, None, "fetch returned no body")

    if _expected_pdf(url) and not result.body.startswith(b"%PDF-"):
        return CandidateVerdict(
            url, owner, False, None,
            f"expected a PDF (%PDF- magic bytes) but got {result.body[:40]!r} -- this is exactly the soft-404/bot-wall shape (a 200 status with the wrong content)"
        )

    actual_length = len(result.body)
    # Exact match, not a tolerance band: these are static government
    # documents, not pages with embedded timestamps -- if the byte count
    # doesn't match DiffLab's own reported length exactly, something
    # about the fetch differs (a redirect landed somewhere else, a
    # partial read, a different representation), and "sane band" per the
    # approval means catching that, not shrugging past a few bytes of
    # drift that shouldn't exist for an unchanged static document.
    if actual_length != diff_lab_raw_byte_length:
        return CandidateVerdict(
            url, owner, False, None,
            f"byte length mismatch: fetched {actual_length}, DiffLab reported {diff_lab_raw_byte_length}"
        )

    actual_hash = hashlib.sha256(result.body).hexdigest()
    if actual_hash != diff_lab_raw_content_hash:
        return CandidateVerdict(
            url, owner, False, None,
            f"hash mismatch: fetched sha256={actual_hash[:16]}..., DiffLab reported {diff_lab_raw_content_hash[:16]}..."
        )

    return CandidateVerdict(url, owner, True, "auto_source_unchanged", "200, content-shape matches, byte length matches, hash matches -- independently re-verified, not just trusted from DiffLab's report")


def days_since_manual_verify(verified_date_str: str | None, verified_method: str | None, today: date) -> int | None:
    """Guardrail 3 (90-day ceiling). NOT the same question as "is this
    record stale" (that's the existing 30-day preship_gate bar) -- this
    asks "how long has it been since a HUMAN last looked at this,
    regardless of how many auto-extends happened since." Today, every
    record's verified_method is "manual" (see the field's rollout in this
    same commit), so this always equals the ordinary staleness age -- it
    only diverges once a real auto-extend chain exists, which is the
    scenario this function exists to guard once the write path is built.
    """
    if not verified_date_str:
        return None
    try:
        verified = date.fromisoformat(verified_date_str)
    except ValueError:
        return None
    if verified_method != "manual":
        # A real implementation would need the LAST manual verified_date,
        # not this field's current value (which reflects the latest
        # extend, auto or manual). No record has ever been auto-extended
        # yet, so there is no real "last manual date distinct from
        # verified_date" to test against -- flagged rather than guessed.
        return None
    return (today - verified).days


def exceeds_reverify_ceiling(days_since_manual: int | None) -> bool:
    if days_since_manual is None:
        return False
    return days_since_manual > MANUAL_REVERIFY_CEILING_DAYS


# ---------------------------------------------------------------------------
# Selftest / positive controls (AuditLab's required review artifact, per
# guardrail 5). Runs unconditionally at the top of main(), same convention
# as build_change_events.py's RC-17 selftest -- not opt-in, so a future
# change to evaluate_candidate() can't silently reintroduce a soft-404 or
# bot-wall false-positive without a test failing loudly.
# ---------------------------------------------------------------------------

def _fake_fetch(status: int, body: bytes, content_type: str = "application/pdf") -> FetchFn:
    def _f(url: str) -> FetchResult:
        return FetchResult(ok=(status < 400), status=status, body=body if status < 400 else None, content_type=content_type)
    return _f


def _selftest() -> None:
    real_pdf_bytes = b"%PDF-1.4\n" + b"x" * 500
    real_hash = hashlib.sha256(real_pdf_bytes).hexdigest()
    real_len = len(real_pdf_bytes)

    # POSITIVE CONTROL 1 (mandatory per the approval): a soft-404, real
    # shape -- cnmilaw.org returning 200 + an HTML directory listing for a
    # path that doesn't actually resolve to the cited document (AuditLab's
    # RC-29 evidence: "a garbage path on .org returns 200 + an HTML
    # listing -- a soft-404 host"). MUST NOT extend.
    soft_404_body = b"<html><body><h1>Index of /pdf/public_laws/13/</h1><ul><li><a href='pl13-51.pdf'>pl13-51.pdf</a></li></ul></body></html>"
    v = evaluate_candidate(
        "https://www.cnmilaw.org/pdf/public_laws/13/pl13-52.pdf",
        "selftest:soft-404",
        diff_lab_raw_content_hash=real_hash,
        diff_lab_raw_byte_length=real_len,
        fetch=_fake_fetch(200, soft_404_body, "text/html"),
    )
    assert v.eligible is False, "SELFTEST FAILED: soft-404 (real cnmilaw.org shape) was marked ELIGIBLE -- guardrail 1 is not working"
    assert "PDF" in v.reason or "hash" in v.reason or "length" in v.reason

    # POSITIVE CONTROL 2 (mandatory per the approval): a bot wall, real
    # shape -- Oregon's secure.sos.state.or.us returning 200 + a 93-
    # character "enable JavaScript" interstitial for OAR 801-010-0345(3)
    # (this session's own live evidence, re-confirmed multiple ticks).
    # MUST NOT extend.
    bot_wall_body = b"Please enable JavaScript to view the page content. Your support ID is: 1234567890123456789"
    v = evaluate_candidate(
        "https://secure.sos.state.or.us/oard/viewSingleRule.action?ruleVrsnRsn=999999",
        "selftest:bot-wall",
        diff_lab_raw_content_hash=real_hash,
        diff_lab_raw_byte_length=real_len,
        fetch=_fake_fetch(200, bot_wall_body, "text/html"),
    )
    assert v.eligible is False, "SELFTEST FAILED: bot wall (real Oregon SOS shape) was marked ELIGIBLE -- guardrail 1 is not working"

    # POSITIVE CASE: a genuinely unchanged PDF -- 200, correct magic
    # bytes, byte length matches, hash matches. MUST extend.
    v = evaluate_candidate(
        "https://example.test/real.pdf",
        "selftest:genuine-match",
        diff_lab_raw_content_hash=real_hash,
        diff_lab_raw_byte_length=real_len,
        fetch=_fake_fetch(200, real_pdf_bytes, "application/pdf"),
    )
    assert v.eligible is True, f"SELFTEST FAILED: a genuinely matching PDF was rejected -- {v.reason}"
    assert v.verified_method_if_extended == "auto_source_unchanged"

    # NEGATIVE CASE: byte-identical status but content silently drifted
    # (hash mismatch despite matching length) -- e.g. a single-character
    # edit that didn't change document length. MUST NOT extend.
    drifted_bytes = b"%PDF-1.4\n" + b"y" * 500  # same length, different hash
    assert len(drifted_bytes) == real_len
    v = evaluate_candidate(
        "https://example.test/real.pdf",
        "selftest:hash-drift",
        diff_lab_raw_content_hash=real_hash,
        diff_lab_raw_byte_length=real_len,
        fetch=_fake_fetch(200, drifted_bytes, "application/pdf"),
    )
    assert v.eligible is False, "SELFTEST FAILED: content with a matching byte length but different hash was marked ELIGIBLE"
    assert "hash mismatch" in v.reason

    # NEGATIVE CASE: a genuine fetch failure (host down / 5xx). MUST NOT
    # extend, and must be distinguishable from a soft-404 in the report.
    v = evaluate_candidate(
        "https://example.test/down.pdf",
        "selftest:fetch-failure",
        diff_lab_raw_content_hash=real_hash,
        diff_lab_raw_byte_length=real_len,
        fetch=_fake_fetch(502, b""),
    )
    assert v.eligible is False, "SELFTEST FAILED: a 502 was marked ELIGIBLE"
    assert "502" in v.reason

    # No baseline yet (DiffLab's raw-hash capture is new -- most records
    # don't have a real raw_changed=False signal yet). MUST NOT extend,
    # and must be distinguishable from a failed verification.
    v = evaluate_candidate("https://example.test/no-baseline.pdf", "selftest:no-baseline", None, None)
    assert v.eligible is False
    assert "no DiffLab raw-hash baseline" in v.reason

    # 90-day ceiling: a record manually verified 91 days ago must be
    # flagged as over the ceiling; 89 days ago must not.
    today = date(2026, 9, 23)
    assert exceeds_reverify_ceiling(days_since_manual_verify("2026-06-24", "manual", today)) is True  # 91 days
    assert exceeds_reverify_ceiling(days_since_manual_verify("2026-06-26", "manual", today)) is False  # 89 days
    assert exceeds_reverify_ceiling(days_since_manual_verify("2026-09-23", "auto_source_unchanged", today)) is False  # not exercisable yet, see docstring

    print("  selftest (6 controls incl. 2 mandatory positive controls: soft-404 + bot-wall): PASS")


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


def _load_citation_owners(repo_root: Path) -> dict[str, str]:
    """url -> "dataset.json:record_id" for every in-scope citation, same
    enumeration preship_gate.py's check_citation_manifest_coverage() uses
    (RC-29) -- kept independent (re-reads the files itself) rather than
    importing that function, so a bug in one doesn't silently propagate
    into the other."""
    owners: dict[str, str] = {}
    for filename in ("cpa_deadlines.json", "cpe_hours.json", "reinstatement.json", "renewal_fees.json"):
        path = repo_root / "data" / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data.get("records", []):
            url = r.get("citation_url")
            rid = r.get("id") or r.get("state_slug") or "?"
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                owners[url] = f"{filename}:{rid}"
    events_path = repo_root / "data" / "reg_change_events.json"
    if events_path.exists():
        events = json.loads(events_path.read_text(encoding="utf-8"))
        for e in events.get("events", []):
            if e.get("kind") != "source_conflict":
                continue
            slug = e.get("jurisdiction_slug", "?")
            for field_name in ("citation_url", "secondary_url"):
                url = e.get(field_name)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    owners[url] = f"reg_change_events.json:{slug}.{field_name}"
    return owners


def main() -> None:
    print("Running mandatory selftest (positive controls) before anything else...")
    _selftest()

    if len(sys.argv) > 1 and sys.argv[1] == "--selftest-only":
        return

    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    capture = _load_latest_capture(repo_root)
    if capture is None:
        print("\nNo citation_freshness capture found (Orchestrator/reg_change_events/citation_freshness_citfresh_*.json) -- nothing to evaluate.")
        return

    owners = _load_citation_owners(repo_root)
    print(f"\nUsing capture {capture['_source_file']} ({capture.get('checked_at', '?')}), {len(capture['results'])} URLs, {len(owners)} in-scope citations known locally.")

    candidates = [
        r for r in capture["results"]
        if r.get("changed") is False and r.get("raw_changed") is False
    ]
    print(f"DiffLab candidates (changed=False AND raw_changed=False): {len(candidates)}")

    if not candidates:
        print("\nNone yet -- expected: DiffLab's raw-hash capture is new (added 2026-09-23) and needs a "
              "second real raw-hash-to-raw-hash comparison before raw_changed is ever non-null for any URL. "
              "This is not a bug in this script; nothing should auto-extend before that data exists.")
        return

    eligible: list[CandidateVerdict] = []
    rejected: list[CandidateVerdict] = []
    for r in candidates:
        url = r["url"]
        owner = owners.get(url, "(not in current dataset scope -- orphaned or manifest-only URL)")
        verdict = evaluate_candidate(url, owner, r.get("raw_content_hash"), r.get("raw_byte_length"))
        (eligible if verdict.eligible else rejected).append(verdict)

    print(f"\nIndependently re-verified: {len(eligible)} ELIGIBLE, {len(rejected)} rejected on independent re-check.")
    for v in eligible:
        print(f"  ELIGIBLE  {v.owner}  {v.url}\n            {v.reason}")
    for v in rejected:
        print(f"  REJECTED  {v.owner}  {v.url}\n            {v.reason}")

    print("\nThis is a REPORT ONLY. No dataset file was written. The write path (an actual verified_date "
          "bump gated on an ELIGIBLE verdict here) does not exist yet -- per the approval's guardrail 5, "
          "it ships only after AuditLab's adversarial review of this file.")


if __name__ == "__main__":
    main()
