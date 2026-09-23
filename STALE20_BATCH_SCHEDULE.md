# STALE-20 staggered re-verification — batch schedule

Written 2026-09-23 (AssetLab), per orchestrator's continuous-freshness approval
(`_AAA_orchestrator_20260923_continuous_freshness_APPROVED.md`): "AssetLab, write the batch
schedule down now; it isn't written anywhere yet." This is that write-down.

## Why this exists

`cpe_hours.json`/`reinstatement.json`/`renewal_fees.json` (157 records total) clustered into two
cliff dates during the 2026-09-09/09-12 remediation cascade — `preship_gate.py`'s own staleness
checks (`STALENESS_THRESHOLD_DAYS = 30`, all three `scripts/*_staleness_check.py`) HARD-BLOCK
shipping once a record's `verified_date`/`last_verified` turns 31 days old. Re-verifying oldest-
first in ~31-record batches (spread across sessions ahead of each cliff) avoids re-creating a
157-record wall on a single date, per AuditLab's original "157-record wall" finding.

## Current wall, as of 2026-09-23 post-batch-2-corrected (independently recomputed from live data, not carried forward)

| Cohort date | Records | cpe_hours | reinstatement | renewal_fees | Gate-blocking cliff (verified_date + 30d) |
|---|---|---|---|---|---|
| 2026-09-09 (4 remain of 6) | 4 | 0 | 0 | 4 | **2026-10-09** |
| 2026-09-12 (109 remain of 115) | 109 | 42 | 45 | 22 | 2026-10-12 |
| 2026-09-13 | 1 | 0 | 0 | 1 | 2026-10-13 |
| 2026-09-19 | 3 | 2 | 1 | 0 | 2026-10-19 |
| 2026-09-22 (batch 1, DONE) | 32 | 1 | 5 | 26 | 2026-10-22 |
| 2026-09-23 (batch 2, 8 genuinely confirmed) | 8 | 6 | 0 | 2 | 2026-10-23 |

Batch 1 (32 records: 1 cpe_hours + 5 reinstatement + 26 renewal_fees, all originally 2026-09-09)
already shipped 2026-09-22, `CONFIRMED_UNCHANGED` 32/32 — see HANDOFF's 2026-09-22 ~12:00 MDT
entry.

**Batch 2 (32 records attempted, commit `229d85b80`, corrected 2026-09-23 per DATE-8 --
`_AAA_orchestrator_20260923_HIGH_batch2_dates_overclaimed.md`): only 8 genuinely re-confirmed
(ak-cpe, ct-cpe, il-cpe, la-cpe, md-cpe, mi-cpe, virginia-renewal-fee, washington-renewal-fee) --
their `verified_date`/`last_manual_verified_date` bump and anchor baseline are legitimate.** The
first ship wrongly bumped dates on 24 more (12 PDFs never text-read, 2 fetches that 403'd, several
anchor/identity misses, one HTML page where the cited figure wasn't actually on it) -- those 24 are
reverted to their pre-batch-2 dates (20 back to 09-12, 4 back to 09-09) and their history notes
reworded to say plainly they were fetched but NOT re-verified. **These 24 still need a real
re-verification pass (batch-1 method: PDF text extraction or an agent reading it) before their own
cliff**, folded into batch 3 alongside its own new records. Auto-extend's proposals recomputed:
only the 8 genuinely confirmed can ever be eligible.

**Batches 3-5 UNPAUSED 2026-09-23** -- AuditLab independently verified the revert 32/32 against their
own per-record target list (0 mismatches), confirmed live, and confirmed the tooling fix is
mutation-proven and stricter than asked (withholds on any unconfirmed fetch, not just a failed one).
See `auditlab_20260923_batch2_revert_VERIFIED_32of32_plus_STALE21.md`.

**STALE-21 (LOW-MED, same report, open)**: `build_manual_verification_update()` never returns
`verified_date` in any case -- its DATE-8 protection is entirely on `last_manual_verified_date`
(correctly, since that's AUTO-1's ceiling field). The public `verified_date` is still set by whoever
runs the batch by hand, with only `manual_verify_gap_reason` as a signal -- exactly what went wrong
in the original batch-2 ship (the gap reasons were all correct; the dates moved anyway). Suggested
fix: a `preship_gate.py` check that a record with a non-`None manual_verify_gap_reason` may not carry
a `verified_date` newer than its `last_manual_verified_date` -- mechanically checkable from the data
alone. Not yet implemented; fail-safe (process gap, not a live defect) so not blocking batch 3.

## Batches 2–5

Front-loaded, not evenly spread by calendar week — the whole 09-09 (6) and 09-12 (115) cohorts
must clear before the 2026-10-09 cliff, since that's the earliest hard block. The 09-13 (1) and
09-19 (3) stragglers are small enough to fold into the last batch rather than earn their own.

| Batch | Target ship date | Records | Composition | Method |
|---|---|---|---|---|
| **2 (DONE + corrected, 2026-09-23, `229d85b80` + DATE-8 fix)** | by 2026-09-27 | 32 attempted, 8 confirmed | all 6 remaining 09-09 renewal_fees + first 26 of the 09-12 cohort (all landed in cpe_hours.json by file-iteration order); only 8 genuinely re-confirmed the cited value, 24 need a real re-pass | real citation_url fetch + field-level comparison (cited fee/hours figure ACTUALLY confirmed, not just fetched) + anchor recording, gated by DATE-8 |
| **3** | by 2026-10-01 | 31 new + the 24 needing re-verification from batch 2 | re-derive the oldest-N of the 09-12/09-09 cohort at execution time; prioritize the 4 remaining 09-09 records (nearest cliff) and the 20 reverted 09-12 cpe_hours records over fresh ones | batch-1 method for anything landing on a PDF (real text extraction or an agent read) -- a magic-bytes-only fetch can never confirm a PDF per DATE-8 |
| **4** | by 2026-10-05 | ~31 | re-derive at execution time | " |
| **5** | by 2026-10-08 (day before the first cliff) | remainder + the 1 09-13 + all 3 09-19 | re-derive at execution time | " |

## Batch 2 onward: also record the anchor baseline (new, 2026-09-23)

Per `_AAA_orchestrator_20260923_siteB_settled_plus_batch2_anchors.md`: auto-extend has 0 eligible
records until MANUAL anchors exist for it to compare against, so from batch 2 onward every manual
re-verification pass must ALSO record its anchor baseline, not just bump `verified_date`/
`last_verified` the way batch 1 did.

**DATE-8 correction (HIGH, `_AAA_orchestrator_20260923_HIGH_batch2_dates_overclaimed.md`, amended):
batch 2's first version of this instruction was wrong** -- it said "always set
`last_manual_verified_date`, regardless of outcome." A fetch succeeding is not the same as the
claim being re-confirmed: 24 of batch 2's 32 records got a live "Verified" date bump (and their
30-day cliff clock reset) with nothing behind it -- 12 PDFs whose text was never read, 2 fetches
that 403'd, several anchor/identity misses. Corrected 2026-09-23, commit `baf264597`.

For each record, on the SAME fetch used for the field-level comparison, call
`build_manual_verification_update(url, dataset_filename, record, today=..., fetch=..., overrides=_load_overrides(repo_root), pdf_value_manually_confirmed=...)`
in `scripts/citation_auto_extend_check.py` -- fetch exactly ONCE and reuse those bytes for both the
comparison and this call, never a second network round-trip. **A verified date moves ONLY when this
pass actually re-confirmed the record's own `cited_value_for_record()` figure** (a non-PDF page
literally contains it, or a human/agent read the PDF text directly and passes
`pdf_value_manually_confirmed=True` -- this function cannot search PDF bytes as text, and a
coincidental raw-byte match is not evidence, per DATE-8). Apply the returned dict's fields to the
record EXACTLY as returned, field by field -- do not invent a bump the function didn't return:
- If confirmed: `last_manual_verified_date` IS in the returned dict -- set it, and ALSO bump
  `verified_date`/`last_verified` to the same date (the caller's own responsibility, this function
  doesn't touch that field name). Also apply `manual_verified_raw_hash`, `manual_verified_raw_
  byte_length`, `manual_verified_anchor`, and `manual_verify_fetched_url` if present.
- If NOT confirmed: `last_manual_verified_date` is ABSENT from the returned dict -- do not touch
  the record's existing `verified_date`/`last_verified`/`last_manual_verified_date` at all, they
  stay exactly as they were before this pass. Still apply `manual_verified_raw_hash`/`byte_length`/
  `anchor` (all `None`, clearing any stale value from an earlier pass) and `manual_verify_gap_reason`
  (why) -- this is an honest, expected outcome for a record needing a real content read (a PDF, or a
  walled page), not a batch failure, and it must be re-attempted (batch-1 method: PDF text
  extraction or an agent actually reading it) before its own 30-day cliff, not left as "verified."
  Oregon's OAR and Colorado's board-email source (both already flagged below) are expected to land
  here structurally (no fetchable text/no citation_url at all).

**AUTO-10, batch 2 MUST pass `overrides`:** RC-29's four override records (`wyoming-renewal-fee`,
`wyoming-reinstatement`, `wy-cpe`, `northern-mariana-islands-all`) have a `citation_url` that is a
viewer shell carrying no fetchable `%PDF-` bytes and no claim anchor -- passing `overrides` (from
`_load_overrides(repo_root)`) makes `build_manual_verification_update()` anchor against the override's
real `monitor_url` instead, which is what actually carries the document. Without `overrides`, these 4
records would record every baseline field as `None` with a misleading "unverifiable" gap reason, even
though the source is fine. Batch 2 includes all 6 of the 09-09 cohort's records; confirm whether any of
these 4 fall in that cohort before running (recompute at execution time per the note below) and pass
`overrides` either way -- it's a no-op for records with no matching override.

32 + 31 + 31 + 31 = 125. ✓ 26 + 31 + 31 + 27 = 115 (all of the 09-12 cohort). ✓

**"Oldest 26/31 of the 09-12 cohort" is deliberately not pinned to specific record IDs here** --
each batch should re-derive "the N oldest not-yet-covered records across the three datasets" at
its own execution time (same as batch 1's own approach: "computed current state myself"), since
an earlier batch, or an unrelated same-day fix, can legitimately move a record's date between now
and when a later batch actually runs. Re-deriving avoids batch 3 accidentally re-touching (or
skipping) a record batch 2 already handled under a different, now-stale list.

**Two flagged loose ends already on record from batch 1, still applicable to whichever future
batch reaches them**: Alabama's reinstatement statute has an amendment (Act 2026-16) effective
2026-10-01 -- worth a recheck after that date, not before. Colorado's renewal fee is sourced to a
dated board email by design (DORA publishes no public schedule) and can't be re-verified the
normal citation_url-fetch way -- flag rather than silently confirm if/when it comes up in a batch.

## Standing rule while this schedule is open

Per the approval: **do not pause these manual batches once auto-extend (continuous-freshness) is
being built or even once it ships its first real extend** -- the ruling is explicit that manual
batches 2-5 keep going regardless, since auto-extend only reaches records DiffLab's capture
already covers with a clean fetch, and the guardrails (valid-fetch-only, 90-day manual-reverify
ceiling, AuditLab positive-control review before first real extend) mean it will not be covering
records this file exists to protect on day one.

Update this file's "Current wall" table and mark each batch DONE (with commit hash + real result
counts, same as batch 1's own report) as it ships -- do not let it go stale itself.

## Standing rules for auto-extend's --apply (2026-09-23, until told otherwise)

Once STALE-20 batches start recording manual anchors, auto-extend's `--apply` flag becomes able to
produce real proposals. Per `_AAA_orchestrator_20260923_AUTO10_plus_apply_rules.md` +
`_AAA_orchestrator_20260923_APPLY1_code_enforce.md` (amended 04:50):
1. No scheduler, cron, loop, or watchdog may call `--apply`. **Code-enforced (APPLY-1)**, not just a
   standing rule: `--apply` refuses (writes nothing, exits non-zero) unless BOTH a per-run approval
   token matching that exact proposals set (via `AUTO_EXTEND_APPLY_APPROVED` env var or the one-shot
   file `auto_extend_proposals/.apply_approved`, containing `compute_proposals_tag()`'s sha256) AND
   an interactive TTY are present. No bypass either way -- a non-interactive invocation refuses even
   with a valid token.
2. The FIRST `--apply` run with more than 0 proposals: stop after the write, BEFORE commit/deploy.
   Send AuditLab the proposals JSON plus the diff to check against live sources. Commit only on its
   PASS. (Procedural, not mechanically enforceable -- it's about not committing prematurely.)
3. Every applied run is exactly one commit, so any run can be reverted.
4. **The approval token is granted by Orchestrator, after AuditLab's PASS on that specific proposals
   file.** Whoever runs a real `--apply` (proposals > 0) must wait for that token before running with
   the flag -- running without it is a guaranteed refusal by design, not an error to work around.
