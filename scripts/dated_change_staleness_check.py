#!/usr/bin/env python3
"""STALE-18 (AuditLab, 2026-09-19): the four per-dataset staleness guards
(cpa_deadlines/cpe_hours/reinstatement/renewal_fees) only ever ask "when did
someone last look?" against a fixed threshold -- none of them ask "is a
dated rule change already on our own calendar for this jurisdiction?" A
record can re-certify as fresh straight through the date its own subject
matter changes, with nothing to catch it: CITE-70 (this same session) is a
live example -- fl-cpe's verified_date (2026-09-12) predated the Florida
CPE-deadline event's own confirmation (2026-09-17), so a re-verification
pass done in that window would have re-certified July 31 as current with no
signal that a December 31 change was already confirmed and on the books.

The join AuditLab proposed is straightforward -- for each validated,
CONFIRMED (not merely proposed) rule-change event, does any of that
jurisdiction's dataset records show a verification older than the event
itself? Uses each event's own `verified_date` as the anchor (not
`detected_at`) -- the date AssetLab/DiffLab last confirmed the event's
facts, which is what actually matters for "could this record plausibly
already reflect the change," and a field both the producer and published
copies of an event carry, so the same comparison works for both sources
below.

STALE-19 (AuditLab, 2026-09-19, filed minutes after STALE-18 shipped):
the first version of this script only read the PUBLISHED
`data/reg_change_events.json` -- which means it went silent for exactly
the window a validated event sits unpublished, and publish lag is the
condition that produced CITE-70 in the first place (the Florida event
was confirmed 2026-09-17 but not published until REG-1, 2026-09-19,
*after* CITE-70's hand-fix had already landed). Proven by AuditLab running
this script against the pre-CITE-70 tree and getting a clean PASS on the
exact case it exists to catch. Fixed the same way
rule_change_ingestion_lag_check.py already handles this: also read
`Orchestrator/reg_change_events/` via `build_change_events.load_difflab_events`
when that sibling directory is present, merge in any event not yet in the
published file, and label those hits distinctly ("confirmed upstream, not
yet published") since they're also a REG-1-class signal in their own
right. Degrades to published-file-only when the producer dir is absent
(same skip-on-absent posture as the lag check), so this still works in a
docs-only checkout.

ADVISORY ONLY, not a hard gate, on purpose -- unlike the four per-dataset
checks this complements, the jurisdiction-level join here is not
guaranteed topically relevant. Checked directly against live data before
shipping this: Louisiana's Act 653 event (licensure pathways + the
substantial-equivalency-to-comparable mobility rewrite) technically
predates la-individual/la-firm's own last_verified by two days, but Act
653 doesn't touch either record's actual subject matter (renewal dates) --
flagging that as a defect would be a false positive a human has to notice
is noise, exactly the failure mode this checker exists to avoid
elsewhere. A human reading each hit and judging relevance is the right
posture until real-world false-positive/negative rates justify promoting
this the way BADGE-1 promoted the per-dataset checks from advisory to
hard gate. AuditLab measured this at first ship: 10/10 hits that day were
false positives for their records' actual subject matter (confirmed by
reading each one against the underlying rule text), and the one true
positive that motivated the check (fl-cpe) was absent because it had
already been fixed -- worth knowing when reading this advisory's output:
at that noise level it will be skimmed past, which is how a real hit gets
missed later. No topical-relevance filter added in response -- a cheap
heuristic (citation-string overlap, shared statute chapter) risks turning
noisy-but-safe into quiet-but-wrong (a real hit filtered out because the
event's citation phrasing didn't match the record's), which is the worse
failure mode for a check like this.

Usage:
    python scripts/dated_change_staleness_check.py [repo_root]
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

# Confirmed-change statuses only -- a still-PROPOSED event has no settled
# text to compare a dataset record against yet (see undated_proposed vs
# undated_enacted in generate.py's own STATUS-1 fix, same distinction).
CONFIRMED_STATUSES = ("ENACTED", "ADOPTED_RULE")

# How far out an event's effective_date needs to be (or how recently
# passed) to be worth a human's attention right now -- not a correctness
# bound, just noise control against events so far out (Idaho's 2028-01-01
# birthdate-renewal transition, 469+ days out as of this check) that
# nobody could act on the gap today anyway.
LOOKAHEAD_DAYS = 90

DATASET_DATE_FIELDS = {
    "cpa_deadlines.json": "last_verified",
    "cpe_hours.json": "verified_date",
    "reinstatement.json": "last_verified",
    "renewal_fees.json": "verified_date",
}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_candidate_source_events(repo_root: Path) -> list[tuple[dict, bool]]:
    """Every rule-change event worth checking, from BOTH the published file
    and (when present) the DiffLab producer directory -- STALE-19's fix.
    Returns (event, is_published) pairs; a producer-dir event already present
    in the published file (by event_id) is deduplicated to its published
    copy, since that's the one a build actually ships."""
    events_path = repo_root / "data" / "reg_change_events.json"
    published = (
        json.loads(events_path.read_text(encoding="utf-8")).get("events", [])
        if events_path.exists()
        else []
    )
    published_ids = {e.get("event_id") for e in published}
    pairs = [(e, True) for e in published]

    sys.path.insert(0, str(repo_root / "scripts"))
    try:
        import build_change_events as bce
    except ImportError:
        return pairs  # scripts/ itself missing something -- published-only is still a valid run
    if not bce.DIFFLAB_EVENTS_DIR.exists():
        return pairs  # same skip-on-absent posture as rule_change_ingestion_lag_check.py

    producer_events, _rejected = bce.load_difflab_events(date.today())
    for e in producer_events:
        if e.get("event_id") not in published_ids:
            pairs.append((e, False))
    return pairs


def collect_gaps(repo_root: Path, today: date | None = None) -> list[dict]:
    """Returns one entry per (event, stale dataset record) pair where the
    record's own verification predates the event's."""
    today = today or date.today()

    candidates = []
    for e, is_published in _load_candidate_source_events(repo_root):
        if e.get("kind") != "rule_change" or e.get("status") not in CONFIRMED_STATUSES:
            continue
        event_verified = _parse_date(e.get("verified_date"))
        if not event_verified:
            continue
        eff = _parse_date(e.get("effective_date"))
        if eff:
            days_out = (eff - today).days
            # Already-landed events stay in scope for a while too (the
            # "guard cannot arm before an effective date" framing cuts
            # both ways -- a record that hasn't been touched since a
            # change TOOK effect is the same gap, just past tense), but
            # not indefinitely.
            if days_out > LOOKAHEAD_DAYS or days_out < -LOOKAHEAD_DAYS:
                continue
        candidates.append((e, event_verified, is_published))

    gaps = []
    for filename, date_field in DATASET_DATE_FIELDS.items():
        data_path = repo_root / "data" / filename
        if not data_path.exists():
            continue
        records = json.loads(data_path.read_text(encoding="utf-8")).get("records", [])
        by_slug: dict[str, list[dict]] = {}
        for r in records:
            by_slug.setdefault(r.get("state_slug"), []).append(r)
        for e, event_verified, is_published in candidates:
            for r in by_slug.get(e.get("jurisdiction_slug"), []):
                record_verified = _parse_date(r.get(date_field))
                if record_verified and record_verified < event_verified:
                    gaps.append(
                        {
                            "event_id": e["event_id"],
                            "jurisdiction": e.get("jurisdiction"),
                            "event_verified": event_verified,
                            "effective_date": e.get("effective_date"),
                            "dataset": filename,
                            "record_id": r.get("id"),
                            "record_verified": record_verified,
                            "gap_days": (event_verified - record_verified).days,
                            "is_published": is_published,
                        }
                    )
    return gaps


def main() -> int:
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    today = date.today()
    gaps = collect_gaps(repo_root, today)

    print(f"Dated-change staleness check -- {today.isoformat()} (lookahead {LOOKAHEAD_DAYS}d, confirmed statuses only)")
    if not gaps:
        print("PASS -- no dataset record predates a confirmed, near-term rule change for its own jurisdiction.")
        return 0

    print(f"\n{len(gaps)} record(s) last verified BEFORE a confirmed rule change was verified for the same jurisdiction:")
    for g in sorted(gaps, key=lambda x: -x["gap_days"]):
        eff = f", effective {g['effective_date']}" if g["effective_date"] else ""
        # STALE-19: a producer-only hit means the event hasn't even been
        # published yet -- a REG-1-class signal in its own right, distinct
        # from "published but the dataset record predates it."
        source_tag = "" if g["is_published"] else " [CONFIRMED UPSTREAM, NOT YET PUBLISHED]"
        print(
            f"  [{g['dataset']}/{g['record_id']}] {g['jurisdiction']} -- record verified {g['record_verified']}, "
            f"but event {g['event_id']} was confirmed {g['event_verified']}{eff} ({g['gap_days']}d gap){source_tag}"
        )
    unpublished_hits = sum(1 for g in gaps if not g["is_published"])
    if unpublished_hits:
        print(
            f"\n{unpublished_hits} of the above are confirmed upstream in the DiffLab producer directory but not "
            f"yet in data/reg_change_events.json -- run scripts/build_change_events.py (check for a "
            f"pending-clobber warning on any hand-corrected record first) to publish them."
        )
    print(
        "\nNot necessarily a defect -- the event may not touch this record's actual subject matter "
        "(check summary_public before assuming it does). Where it does, the record likely needs a "
        "forward-dated caveat (see CITE-70's fix for the pattern) or a re-verification pass."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
