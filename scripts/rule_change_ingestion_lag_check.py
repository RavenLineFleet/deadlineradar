#!/usr/bin/env python3
"""REGEN-11 (AuditLab, 2026-08-29): measures the gap between what DiffLab's
producer directory has validated and what's actually published on
/rule-changes/. `build_change_events.py` is human-run by design (no
automated caller, confirmed at t398) -- some lag is expected and correct.
What was missing is anything that MEASURES the lag: a validated event can
sit ingestable-but-unpublished indefinitely with nothing surfacing it, the
same failure shape MON-3 found one stage upstream ("17 days of real
staleness ... turned out to be a missing sync step, not a dead monitor").

Advisory only, not a hard gate -- unlike MON-3, some lag here is NORMAL
(a human runs the regen periodically, not on every DiffLab cycle), so
failing the build on any nonzero gap would make routine, harmless lag look
like a shipped defect. Degrades to a skip when the producer directory
isn't present in this checkout (it lives outside the repo, in a sibling
project's own workspace) -- same posture as
check_competitor_price_currency()'s docs/compare/ auto-skip.

GATE-55 (AuditLab, 2026-10-16): the original version compared event_id SETS
only. An id present on both sides but carrying DIFFERENT content (DiffLab
fixed a record in place, same id, corrected text) was invisible to it --
proven live by SRC-45/46/51/57, the exact "fixed upstream only" family this
check exists to catch, every one of which it reported as zero lag. Now also
diffs the fields that actually render for every id present on both sides,
alongside the existing set-difference check.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A small buffer, not zero -- the producer dir can gain a brand-new file
# mid-run without that being anything to act on; this only flags a gap that
# has had a reasonable window to be regenerated and wasn't.
LAG_WARNING_THRESHOLD = 3

# GATE-55: exactly the fields _rule_change_card_html() actually renders (jurisdiction/topic are
# cosmetic and excluded on purpose -- a renamed display label isn't the "fixed upstream only" shape
# this exists to catch). Content, not presentation.
_RENDERED_FIELDS = (
    "summary_public", "citation", "citation_url", "status", "effective_date", "confidence",
    "needs_reverification",
)


def collect_lag(repo_root: Path) -> dict:
    sys.path.insert(0, str(repo_root / "scripts"))
    import build_change_events as bce
    from datetime import date

    if not bce.DIFFLAB_EVENTS_DIR.exists():
        return {"status": "no_producer_dir"}

    today = date.today()
    difflab_events, rejected = bce.load_difflab_events(today)
    ingestable_by_id = {e["event_id"]: e for e in difflab_events}
    ingestable_ids = set(ingestable_by_id)

    data_path = repo_root / "data" / "reg_change_events.json"
    if not data_path.exists():
        return {"status": "no_data_file"}
    import json

    published = json.loads(data_path.read_text(encoding="utf-8"))
    published_by_id = {
        e["event_id"]: e for e in published.get("events", []) if e.get("source") == "difflab_reg_change_engine"
    }
    published_ids = set(published_by_id)

    unpublished = sorted(ingestable_ids - published_ids)
    # AuditLab's own framing: "published but no longer ingestable" is a
    # DIFFERENT, more serious signal (something that was live got dropped)
    # -- surfaced separately so it isn't buried inside a routine lag count.
    orphaned = sorted(published_ids - ingestable_ids)

    # GATE-55: same id, different content -- the set-difference checks above are both blind to this.
    drifted = []
    for event_id in sorted(ingestable_ids & published_ids):
        ingestable_rec, published_rec = ingestable_by_id[event_id], published_by_id[event_id]
        changed_fields = sorted(
            f for f in _RENDERED_FIELDS if ingestable_rec.get(f) != published_rec.get(f)
        )
        if changed_fields:
            drifted.append({"event_id": event_id, "changed_fields": changed_fields})

    return {
        "status": "ok",
        "ingestable_count": len(ingestable_ids),
        "published_count": len(published_ids),
        "unpublished": unpublished,
        "orphaned_published": orphaned,
        "drifted": drifted,
    }


def main() -> int:
    result = collect_lag(ROOT)
    status = result["status"]
    if status == "no_producer_dir":
        print("  (producer dir not present in this checkout -- skipping)")
        return 0
    if status == "no_data_file":
        print("  (data/reg_change_events.json not found -- skipping)")
        return 0

    unpublished = result["unpublished"]
    orphaned = result["orphaned_published"]
    drifted = result["drifted"]
    print(
        f"ingestable: {result['ingestable_count']}   published: {result['published_count']}   "
        f"unpublished: {len(unpublished)}   orphaned-published: {len(orphaned)}   "
        f"drifted (same id, changed content): {len(drifted)}"
    )
    if orphaned:
        print(
            f"  ORPHANED -- published but no longer ingestable from the current producer dir "
            f"(investigate before assuming this is just lag): {', '.join(orphaned)}"
        )
    exit_code = 0
    if len(unpublished) > LAG_WARNING_THRESHOLD:
        print(
            f"  {len(unpublished)} validated event(s) are ingestable but not yet published -- "
            f"past the {LAG_WARNING_THRESHOLD}-event buffer. Run scripts/build_change_events.py "
            f"(check for a pending-clobber warning on any existing hand-corrected record first) "
            f"before assuming /rule-changes/'s published count is current:"
        )
        for event_id in unpublished:
            print(f"    - {event_id}")
        exit_code = 1
    elif unpublished:
        print(f"  {len(unpublished)} validated event(s) ingestable but not yet published (within the normal buffer): {', '.join(unpublished)}")
    if drifted:
        # GATE-55: unlike pure lag, this is never "normal" -- the SAME id publishing different
        # rendered content than the producer's current version means the ingest is stale on a record
        # that already exists live, not merely missing a new one. Same severity treatment as exceeding
        # the lag buffer (exit 1), since "zero drift" has no equivalent of a harmless buffer window.
        print(
            f"  {len(drifted)} event(s) are ingestable AND published, but publish DIFFERENT content "
            f"than the producer's current version -- re-run scripts/build_change_events.py "
            f"(check for a pending-clobber warning first) to pick up the fix:"
        )
        for d in drifted:
            print(f"    - {d['event_id']}: {', '.join(d['changed_fields'])}")
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
