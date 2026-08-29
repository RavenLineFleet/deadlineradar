#!/usr/bin/env python3
"""Extract PUBLISHABLE rule-change events from the mobility ruleset.

## Why this is a separate file and a separate build step

The changes feed must be able to publish the change FACTS + citations while the
mobility DETERMINATION ENGINE stays held from production (orchestrator's
directive, 2026-08-01). Deriving events into their own dataset -- rather than
having the feed read the engine's ruleset directly -- is what keeps those two
separable. The feed depends on this file; it never imports mobility.

## Why status is derived from STRUCTURED FIELDS ONLY, never from prose

`EDITORIAL_CHARTER_law_coverage.md` mandates one explicit status label per item
(ENACTED / ADOPTED RULE / PROPOSED / DIED) and warns, with Florida's
died-two-sessions mobility bills as the example, that blurring them actively
misleads a CPA.

I measured whether the label could be inferred from each record's prose
(`flux_note` / `notes` / `citation`). It cannot: **27 of 55 records match
enacted-signals AND proposed-signals AND rule-signals simultaneously**, because
a single flux_note routinely discusses a signed act, the rulemaking that
follows it, and the board rule it amends. A regex over that prose would emit a
confident label with no basis -- precisely the failure the charter exists to
prevent.

So this script derives ONLY what the structured fields support:

  * `rule_changes_on` in the FUTURE  -> a dated, signed change not yet in force.
  * `last_changed_on`                -> the most recent CONSUMMATED change.
    Powers "recently changed". The charter forbids asserting it took effect
    without re-verification after the date, so these carry
    `needs_reverification` and the feed must render "effective [date]; we
    re-verify on/after that date", never "is now in effect".
  * `rule_in_flux` with NO date      -> NOT a rule change. These are the
    source-disagreement records (board page contradicts the statute). The
    directive is explicit that these must not be conflated with rule changes,
    so they get their own kind and never appear in the changes list.

## The 2026-08-02 schema split, and why a past `rule_changes_on` is WITHHELD

ScoutLab found `rule_changes_on` was carrying two meanings at once: 34 of 55
records held a PAST date while the field is consumed as forward-looking. A past
date in a forward field makes change-alerts fire on history. The orchestrator
split it: `rule_changes_on` is now strictly forward-looking (null if nothing
pending) and `last_changed_on` is new for the most recent consummated change.

Until the corrected dataset lands, a PAST date sitting in `rule_changes_on` is
explicitly untrustworthy -- it may mean "already happened" or it may be a
stale forward value. This script therefore WITHHOLDS those records to the work
queue rather than publishing them as "recently changed", and only treats a past
date as a real recent change when it arrives in `last_changed_on`. Future dates
are unambiguous under both schemas and still publish.

Anything else is not emitted. A smaller, correct feed beats a complete one
with a guessed label.

## Hard rejections (charter: "No item without a source. Ever.")

A candidate is DROPPED, loudly, if it lacks a citation, lacks a primary-source
URL, has a non-http(s) URL, or names a jurisdiction outside our 55. The script
prints every rejection so a gap is visible rather than silent.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES = ROOT / "worker" / "src" / "mobility_rules.json"
OUT = ROOT / "data" / "reg_change_events.json"
QUEUE = ROOT / "data" / "reg_change_withheld.json"
DEADLINES = ROOT / "data" / "cpa_deadlines.json"

# DiffLab's live regulatory-change monitor writes one JSON file per confirmed
# event here (outside this repo -- a sibling project's own workspace). This
# script only READS from it; DiffLab owns writing. An env override exists
# because the absolute default path is this machine's layout, not a repo
# assumption -- on any other machine (or if DiffLab hasn't produced anything
# yet) the directory is simply absent, which degrades to zero events, not an
# error. That degrade-to-empty is deliberate: see the module docstring on the
# honest-empty-state requirement.
DIFFLAB_EVENTS_DIR = pathlib.Path(
    os.environ.get("DR_DIFFLAB_EVENTS_DIR", ROOT.parent.parent.parent / "Orchestrator" / "reg_change_events")
)

KIND_CHANGE = "rule_change"
KIND_CONFLICT = "source_conflict"

# REGEN-6 (AuditLab, filed 2026-08-26, still open 2026-08-29): the charter's
# 5 labels, matching generate.py's _rule_change_status_label() dict exactly
# (that function is the sole consumer of this field on the public page --
# these two lists must never drift apart). A missing OR off-charter status
# (a typo, a case variant like "died_withdrawn", or a producer-internal label
# like "candidate_unverified_needs_interpretation") used to silently become
# "ENACTED" -- the site's strongest legal claim -- with no whitelist check at
# all. Now anything that isn't exactly one of these 5 is rejected rather than
# guessed, same treatment as every other required field below.
VALID_RULE_CHANGE_STATUSES = {
    "ENACTED",
    "ENACTED_DATE_PENDING",
    "ADOPTED_RULE",
    "PROPOSED",
    "DIED_WITHDRAWN",
}


# Known jurisdiction-slug aliases between ScoutLab's vocabulary and ours.
#
# Deliberately an explicit, tiny, DOCUMENTED map rather than fuzzy matching:
# a slug mismatch must stay loud. This one was caught by the script's own
# rejection gate on 2026-08-01 -- ScoutLab emits "district-of-columbia" while
# every deadline record, URL and page on this site uses "dc". Without the
# alias, DC would silently vanish from the changes feed; with fuzzy matching,
# the NEXT mismatch would silently resolve to the wrong jurisdiction.
#
# Reported upstream so the source vocabulary converges. If this map grows
# beyond a couple of entries, fix it at the source instead.
SLUG_ALIASES = {"district-of-columbia": "dc"}


def _public_summary(r: dict, is_conflict: bool) -> str:
    """A short, public-safe one-liner, synthesized from STRUCTURED fields only.

    `flux_note`/`notes` are internal research prose -- they routinely contain
    review annotations like "UPDATE (verifier): ... REFUTED ... CiteID 552496"
    that must never reach a public page (or this repo's public data/ files,
    which ship alongside the site). preship_gate.py caught exactly this leak
    on the first render of this page; this function is the fix, not a
    stripped/redacted version of the prose -- stripping specific patterns out
    of free text is exactly the kind of check that silently misses the next
    variant. Never derive this from flux_note/notes again.

    REMOVED, not just guarded (AuditLab REGEN-4, filed 2026-08-26, re-
    verified 2026-08-29): this used to have a `basis` branch that read
    `equivalence_test` -- the record's CURRENT classification -- and
    asserted the rule "is changing to" that same basis. Self-contradictory
    for any state whose target basis IS its current one (confirmed live-
    wrong for Indiana and Massachusetts), and never a verified current-vs-
    target distinction for the others either -- there is no field anywhere
    in this data model for what a mobility rule is actually changing TO,
    only what it currently IS, so "changing to <current basis>" could never
    be right except by accident. It was dead code at the time (every record
    that could reach it was withheld, missing `status`), and re-verification
    found the count of records one `status` field away from republishing it
    had grown 6.5x (9 -> 44) with zero gate watching for that -- a docstring
    warning is not a control, it just delays the incident to whoever adds a
    status field without having read this comment. Deleted the guess
    entirely rather than gate it: the generic sentence below is honest with
    zero fields to author, and stays honest regardless of what any current
    or future flux record's status/equivalence_test look like. If a specific
    "changing to X" claim is ever wanted, it needs its own explicitly-
    authored target field (e.g. rule_changes_to) that only gets set once
    someone has actually researched the target -- never derived from a
    CURRENT-state field again.
    """
    state = r.get("state") or r.get("state_slug") or "This jurisdiction"
    if is_conflict:
        # Devin, live, 2026-08-28: "We need to be positive in what we tell
        # people and a IDK isn't something i want to publish." This sentence
        # was the ORIGINAL source of "withhold a determination" for every
        # conflict record -- fixed directly in data/reg_change_events.json
        # and generate.py that day, but this function is what regenerates
        # that file's summary_public field from scratch. Left unfixed here,
        # the very next time this script runs (a new conflict detected, or
        # an existing one re-synced) would have silently reintroduced the
        # retired phrasing, undoing that fix without anyone touching the
        # data file directly. Matches the exact reframe applied everywhere
        # else: leads with what was actually found, not with "we don't
        # know."
        return (
            f"{state}'s own primary legal sources don't agree with each other on this rule. "
            f"Rather than guess, we found the conflict and are showing you exactly where it is "
            f"below — read both citations yourself, or confirm directly with the {state} board."
        )
    return f"{state}'s CPA mobility rule is changing. See the citation below for the exact requirements."


def _http(url: object) -> str | None:
    """Only http(s) survives -- these render into href attributes, where HTML
    escaping does nothing against a javascript: URI."""
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    return None


def build(today: date) -> tuple[list[dict], list[str]]:
    rules = json.loads(RULES.read_text(encoding="utf-8"))["records"]
    valid_slugs = {
        r["state_slug"] for r in json.loads(DEADLINES.read_text(encoding="utf-8"))["records"]
    }

    events: list[dict] = []
    rejected: list[str] = []
    withheld_ambiguous: list[dict] = []

    for r in rules:
        slug = SLUG_ALIASES.get(r.get("state_slug"), r.get("state_slug"))
        if slug not in valid_slugs:
            rejected.append(f"{slug}: jurisdiction not in our 55")
            continue
        if r.get("rule_in_flux") is not True:
            continue  # nothing changing and no disagreement -> not an event

        citation = r.get("citation")
        citation_url = _http(r.get("citation_url"))
        if not citation or not citation_url:
            rejected.append(f"{slug}: no citation or no primary-source URL -- charter forbids publishing")
            continue

        def _iso(value: object, field: str) -> date | None:
            if not value:
                return None
            try:
                return date.fromisoformat(str(value))
            except (TypeError, ValueError):
                rejected.append(f"{slug}: unparseable {field} {value!r}")
                return None

        changes_on = r.get("rule_changes_on")
        last_changed = r.get("last_changed_on")
        forward = _iso(changes_on, "rule_changes_on")
        recent = _iso(last_changed, "last_changed_on")

        # Schema-split transition guard. A PAST value in the forward-looking
        # field is ambiguous until ScoutLab's corrected dataset lands, so it is
        # queued, never published. See the module docstring.
        if forward is not None and forward <= today and recent is None:
            withheld_ambiguous.append({
                "jurisdiction_slug": slug,
                "jurisdiction": r.get("state") or slug,
                "status": "UNDETERMINED",
                "reason": (f"rule_changes_on={changes_on} is in the past and last_changed_on is "
                           "absent -- ambiguous under the 2026-08-02 schema split; may be a "
                           "consummated change or a stale forward value"),
                "has_citation_url": bool(citation_url),
                "confidence": r.get("confidence"),
                "next_action": "awaiting the corrected forward-only rule_changes_on / last_changed_on split for this record",
            })
            continue

        parsed = forward if (forward is not None and forward > today) else recent

        # ID suffix mirrors whichever date actually produced `parsed` (or,
        # for a genuine no-date source conflict, the literal fallback) --
        # NOT simply "changes_on or fallback", which mislabelled a real,
        # dated, last_changed_on-derived event as "source-conflict" purely
        # because rule_changes_on had gone null (2026-08-02 schema split: a
        # consummated change nulls the forward-looking field on purpose).
        id_date = parsed.isoformat() if parsed is not None else "source-conflict"
        base = {
            "event_id": f"{slug}-mobility-{id_date}",
            "jurisdiction_slug": slug,
            "jurisdiction": r.get("state") or slug,
            "topic": "practice privilege (mobility)",
            "citation": citation,
            "citation_url": citation_url,
            "secondary_url": _http(r.get("source_url")),
            "verified_date": r.get("verified_date"),
            "confidence": r.get("confidence"),
            # Synthesized, public-safe -- NEVER the raw flux_note/notes
            # prose. See _public_summary()'s docstring for why.
            "summary_public": _public_summary(r, is_conflict=parsed is None),
        }

        if parsed is None:
            base.update({
                "kind": KIND_CONFLICT,
                "effective_date": None,
                # Deliberately NOT one of the charter's law-status labels: this
                # is not a law changing, it is our two sources disagreeing.
                "status": "SOURCE_CONFLICT",
                "needs_reverification": False,
            })
        elif not r.get("status"):
            # AuditLab REGEN-1/REGEN-3 (2026-08-26): every record in this
            # dataset lacks a `status` key (mobility_rules.json predates the
            # DiffLab schema and never carries one), so the old `r.get(
            # "status") or "ENACTED"` fallback fired unconditionally --
            # published Massachusetts and Indiana as ENACTED law with a
            # 2027-01-01 effective date while their own flux_note says
            # neither has actually been enacted (MA: bill sitting in House
            # committee; IN: mobility statute unchanged since 2007). A
            # missing field is not evidence of enactment -- charter forbids
            # asserting a status the data doesn't support, same rule that
            # already governs the past-dated/no-last_changed_on withhold
            # above. Withhold instead of guessing.
            withheld_ambiguous.append({
                "jurisdiction_slug": slug,
                "jurisdiction": r.get("state") or slug,
                "status": "UNDETERMINED",
                "reason": (f"rule_in_flux with a determinable date ({id_date}) but no explicit "
                           "`status` field in mobility_rules.json -- withheld rather than "
                           "defaulting to ENACTED with no basis"),
                "has_citation_url": bool(citation_url),
                "confidence": r.get("confidence"),
                "next_action": "needs an explicit status field (ENACTED / ADOPTED RULE / PROPOSED / "
                               "DIED) added to this record in worker/src/mobility_rules.json, "
                               "sourced the same way DiffLab's regwatch events are",
            })
            continue
        else:
            base.update({
                "kind": KIND_CHANGE,
                "effective_date": parsed.isoformat(),
                "status": r.get("status"),
                "status_evidence": r.get("status_evidence"),
                "status_source_url": _http(r.get("status_source_url")),
                "upcoming": parsed > today,
                # The charter forbids asserting a change took effect without
                # re-verifying after the date passed.
                "needs_reverification": parsed <= today,
            })
        events.append(base)

    events.sort(key=lambda e: (e.get("effective_date") or "9999-99-99", e["jurisdiction_slug"]))
    return events, rejected, withheld_ambiguous


def load_difflab_events(today: date) -> tuple[list[dict], list[str]]:
    """Ingest DiffLab's live-monitoring events (see schema agreement,
    2026-08-02: `Orchestrator/inbox/difflab_..._reg_change_events_schema_agreed.md`).

    Each file is one already-classified event, already in this script's output
    shape. `event_id`s are namespaced `-regwatch-` (vs. this script's own
    `-mobility-`), so there is no collision merging the two sources. Still
    re-validated against the SAME hard-rejection gate as the mobility path
    (charter: "no item without a source, ever") -- a producer bug elsewhere
    must not become a silent citation-less item on the public page.
    """
    valid_slugs = {
        r["state_slug"] for r in json.loads(DEADLINES.read_text(encoding="utf-8"))["records"]
    }
    events: list[dict] = []
    rejected: list[str] = []
    if not DIFFLAB_EVENTS_DIR.is_dir():
        return events, rejected

    for path in sorted(DIFFLAB_EVENTS_DIR.glob("event_*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rejected.append(f"{path.name}: unreadable/invalid JSON ({exc})")
            continue

        slug = SLUG_ALIASES.get(raw.get("jurisdiction_slug"), raw.get("jurisdiction_slug"))
        if slug not in valid_slugs:
            rejected.append(f"{path.name}: jurisdiction {raw.get('jurisdiction_slug')!r} not in our 55")
            continue

        citation_url = _http(raw.get("citation_url"))
        if not citation_url:
            rejected.append(f"{path.name}: no http(s) citation_url -- charter forbids publishing")
            continue

        # `detail` is DiffLab's own internal/audit field and is NEVER
        # rendered publicly -- only `summary_public` (their schema's
        # explicit "reader-safe one-liner") is. A producer bug that omits
        # summary_public must not silently fall back to unreviewed text on
        # a public page, so the event is rejected instead.
        summary_public = raw.get("summary_public")
        if not summary_public:
            rejected.append(f"{path.name}: no summary_public -- refusing to publish unreviewed detail text")
            continue

        effective_date = raw.get("effective_date")
        parsed = None
        if effective_date:
            try:
                parsed = date.fromisoformat(str(effective_date))
            except (TypeError, ValueError):
                rejected.append(f"{path.name}: unparseable effective_date {effective_date!r}")
                continue

        # REGEN-6/REGEN-10 (AuditLab): a missing status used to silently
        # become "ENACTED" -- the strongest claim the site makes -- and any
        # OFF-CHARTER value (a case variant like "died_withdrawn", or a
        # producer-internal label like
        # "candidate_unverified_needs_interpretation") passed through
        # verbatim with no check at all, where generate.py's renderer would
        # then ALSO fail open to "Enacted" on anything it didn't recognize.
        # Reject here, loudly, same as every other required field above --
        # a human must supply a real charter status, never a guess.
        status = raw.get("status")
        if status not in VALID_RULE_CHANGE_STATUSES:
            rejected.append(
                f"{path.name}: status {status!r} is missing or not one of the charter's 5 labels "
                f"({sorted(VALID_RULE_CHANGE_STATUSES)}) -- refusing to guess a legal-status claim"
            )
            continue

        events.append({
            "event_id": raw.get("event_id") or f"{slug}-regwatch-{path.stem}",
            "jurisdiction_slug": slug,
            "jurisdiction": raw.get("jurisdiction") or slug,
            "topic": raw.get("topic") or "CPA regulatory/statutory change",
            "citation": raw.get("citation"),
            "citation_url": citation_url,
            "secondary_url": _http(raw.get("secondary_url")),
            "verified_date": raw.get("verified_date"),
            # This pipeline is single-source by construction (one monitored
            # page diffed against its own prior capture) -- never upgraded to
            # dual_source here even if the input claims otherwise.
            "confidence": "single_source",
            "summary_public": summary_public,
            "kind": KIND_CHANGE,
            "effective_date": parsed.isoformat() if parsed else None,
            "status": status,
            "status_evidence": raw.get("status_evidence"),
            "status_source_url": _http(raw.get("status_source_url")) or citation_url,
            "upcoming": bool(parsed and parsed > today),
            "needs_reverification": bool(parsed and parsed <= today),
            "source": "difflab_reg_change_engine",
        })
    return events, rejected


def withheld_queue(today: date) -> list[dict]:
    """Records we are NOT publishing, as a WORK QUEUE rather than a silent drop.

    Orchestrator refinement (2026-08-02): "a withheld item is a work queue, not
    a decision." Anything held back for want of a defensible charter status
    lands here with the reason, so it gets resolved instead of forgotten.
    """
    rules = json.loads(RULES.read_text(encoding="utf-8"))["records"]
    events, _, ambiguous = build(today)
    published = {e["jurisdiction_slug"] for e in events}
    out = []
    for r in rules:
        slug = SLUG_ALIASES.get(r.get("state_slug"), r.get("state_slug"))
        if slug in published:
            continue
        if r.get("rule_in_flux") is True:
            reason = "flagged in flux but no citation/primary-source URL"
        else:
            reason = ("no rule_changes_on and not flagged in flux -- no evidence of a change; "
                      "a charter status cannot be derived from structure alone")
        out.append({
            "jurisdiction_slug": slug,
            "jurisdiction": r.get("state") or slug,
            "status": "UNDETERMINED",
            "reason": reason,
            "has_citation_url": bool(_http(r.get("citation_url"))),
            "confidence": r.get("confidence"),
            "next_action": "needs an explicit status classification + justifying evidence before it can publish",
        })
    seen = {a["jurisdiction_slug"] for a in ambiguous}
    out = [o for o in out if o["jurisdiction_slug"] not in seen] + ambiguous
    return sorted(out, key=lambda x: x["jurisdiction_slug"])


# AuditLab REGEN-9 (2026-08-27, self-corrected same day): these 5 of the 11
# _meta keys can carry hand-authored prose worth protecting -- including
# `generated_from`, which is templated but NOT pure noise: its only dynamic
# part is one embedded count, so it only changes when that count actually
# does (5 distinct values across 25 days of history, not every run). This
# is exactly the field the original incident destroyed -- at 75e5bc747 it
# ended with a 2026-08-21 hand-written note on a 3-vs-4 monitoring-count
# discrepancy; 4950497b7 silently replaced it with the bare template. The
# other 6 keys (as_of, the five counts) are genuine noise -- they change on
# literally every run and must NOT be diffed, or the guard would cry wolf
# on every single invocation.
_META_HAND_EDITABLE_KEYS = ("purpose", "separable", "status_derivation", "reverification_rule", "generated_from")


def _report_pending_clobber(new_events: list[dict], new_meta: dict) -> None:
    """AuditLab REGEN-2 addendum (2026-08-27): this generator is write-only
    -- it never reads its own prior output, so any hand-correction made
    directly in data/reg_change_events.json (rather than upstream in
    mobility_rules.json or a DiffLab event file) survives only until the
    next run. That's exactly how 4950497b7 silently reverted a 10-day-old
    fix to a reported Massachusetts/Indiana defect (e09525b84) -- nobody
    saw it happen until the live site was already wrong.

    Making the generator itself read-modify-write would fix this, but
    AuditLab's point is sharper: the check that would have caught it is
    much cheaper than that redesign. Diff the freshly-built event set
    against whatever's already on disk, BEFORE overwriting it, and print
    anything that would be silently removed, added, or field-changed. A
    clean diff means no hand-edit is sitting in the blast radius; a dirty
    one is the loud warning that was missing on 2026-08-26.

    Also diffs the hand-editable _meta prose keys (REGEN-9) -- the first
    version of this guard only compared events and reported "clean" while
    about to destroy a hand-edited _meta field, which is worse than no
    check at all."""
    if not OUT.is_file():
        return
    try:
        old_doc = json.loads(OUT.read_text(encoding="utf-8"))
        old_events = {e["event_id"]: e for e in old_doc.get("events", [])}
        old_meta = old_doc.get("_meta", {})
    except (OSError, json.JSONDecodeError, KeyError):
        print("  (pending-clobber check skipped -- existing data/reg_change_events.json unreadable)")
        return
    new_by_id = {e["event_id"]: e for e in new_events}
    removed = sorted(set(old_events) - set(new_by_id))
    added = sorted(set(new_by_id) - set(old_events))
    changed = []
    for event_id in sorted(set(old_events) & set(new_by_id)):
        old_e, new_e = old_events[event_id], new_by_id[event_id]
        drifted_fields = sorted(k for k in (set(old_e) | set(new_e)) if old_e.get(k) != new_e.get(k))
        if drifted_fields:
            changed.append((event_id, drifted_fields))
    meta_changed = sorted(
        k for k in _META_HAND_EDITABLE_KEYS if old_meta.get(k) != new_meta.get(k)
    )
    if not removed and not added and not changed and not meta_changed:
        print("  pending-clobber check: clean -- this run matches what's already on disk, no hand-edit at risk.")
        return
    print("  pending-clobber check: this run WOULD CHANGE the committed file --")
    if removed:
        print(f"    would REMOVE {len(removed)} event(s): {', '.join(removed)}")
    if added:
        print(f"    would ADD {len(added)} event(s): {', '.join(added)}")
    for event_id, fields in changed:
        print(f"    would change {event_id}: {', '.join(fields)}")
    for key in meta_changed:
        print(f"    would change _meta.{key}")
    print("  If any of the above is a hand-correction made directly in data/reg_change_events.json "
          "(not upstream in mobility_rules.json or a DiffLab event file), it will be lost -- move the "
          "fix upstream before re-running, or confirm the change is intentional.")


def main() -> int:
    today = date.today()
    mobility_events, rejected, _ = build(today)
    difflab_events, difflab_rejected = load_difflab_events(today)
    rejected = rejected + difflab_rejected

    # No id collisions by construction (-mobility- vs -regwatch- namespacing,
    # per DiffLab's schema agreement) -- still de-duped defensively so a
    # producer bug on either side can't double-publish the same event_id.
    events = mobility_events + difflab_events
    seen_ids: set[str] = set()
    deduped = []
    for e in events:
        if e["event_id"] in seen_ids:
            rejected.append(f"{e['event_id']}: duplicate event_id, second occurrence dropped")
            continue
        seen_ids.add(e["event_id"])
        deduped.append(e)
    events = deduped
    events.sort(key=lambda e: (e.get("effective_date") or "9999-99-99", e["jurisdiction_slug"]))

    changes = [e for e in events if e["kind"] == KIND_CHANGE]
    upcoming = [e for e in changes if e.get("upcoming")]
    recent = [e for e in changes if not e.get("upcoming")]
    conflicts = [e for e in events if e["kind"] == KIND_CONFLICT]
    difflab_live = [e for e in changes if e.get("source") == "difflab_reg_change_engine"]

    meta = {
        "purpose": "Publishable rule-change events for the public changes feed.",
        "generated_from": "worker/src/mobility_rules.json (batch research) + "
                          "automated day-to-day source monitoring "
                          f"({len(difflab_live)} currently promoted).",
        "separable": "Deliberately independent of the mobility determination engine (AuditLab "
                     "MON-2, 2026-08-06: the engine shipped to production 2026-07-30 -- "
                     "/firm/mobility/check, /firm/mobility/check-batch, /firm/mobility/coverage, "
                     "and the /firm-mobility/ Practice Privilege Check UI are all live; this note "
                     "previously said HELD, which stopped being true and would have misled a "
                     "future maintainer about what's deployed). This feed publishes change facts "
                     "+ citations only and stays architecturally independent of that engine "
                     "either way -- a change fact does not require or imply a mobility "
                     "determination for the same state.",
        "status_derivation": "Structured fields ONLY (rule_changes_on presence + direction, or "
                             "the monitoring pipeline's own classified status). Status is NOT inferred from "
                             "prose: 27 of 55 mobility records match enacted/proposed/rule "
                             "signals simultaneously, so a regex label would be a guess. Records "
                             "whose status cannot be established from structure are not emitted.",
        "reverification_rule": "needs_reverification=true means the effective date has passed "
                               "and we have NOT re-checked. The page must say 'effective [date]; "
                               "we re-verify on/after that date' -- never 'is now in effect'.",
        "as_of": today.isoformat(),
        # Plain numeric fields so the page renders honest counts without
        # string-parsing `generated_from` or hardcoding a figure that
        # drifts from the data the next time this script runs.
        "changes_published": len(changes),
        "changes_upcoming": len(upcoming),
        "changes_recent": len(recent),
        "source_conflicts_published": len(conflicts),
        "live_monitoring_count": len(difflab_live),
    }

    _report_pending_clobber(events, meta)

    OUT.write_text(json.dumps({
        "_meta": meta,
        "events": events,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    held = withheld_queue(today)
    QUEUE.write_text(json.dumps({
        "_meta": {
            "purpose": "Jurisdictions NOT published to the changes feed, and why. A WORK QUEUE.",
            "policy": "Orchestrator 2026-08-02: withhold rather than guess a charter status; 40 "
                      "well-labelled items beat 55 with some mislabelled. Tracked here so held-back "
                      "items get resolved rather than silently forgotten.",
            "as_of": today.isoformat(),
        },
        "withheld": held,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"wrote {QUEUE.relative_to(ROOT)}  ({len(held)} withheld, tracked not dropped)")
    print(f"  rule changes   : {len(changes)}  ({len(upcoming)} upcoming, {len(recent)} past-dated, "
          f"{len(difflab_live)} from DiffLab live monitoring)")
    print(f"  source conflicts: {len(conflicts)}  (rendered separately -- NOT rule changes)")
    print(f"  past-dated needing re-verification before we may claim they took effect: "
          f"{sum(1 for e in changes if e['needs_reverification'])}")
    if rejected:
        print(f"\n  REJECTED {len(rejected)} (visible, not silent):")
        for r in rejected:
            print(f"    - {r}")
    if not events:
        print("\n  no events -- nothing to publish", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
