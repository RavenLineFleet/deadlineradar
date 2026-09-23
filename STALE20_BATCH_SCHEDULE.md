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

## Current wall, as of 2026-09-23 (independently recomputed from live data, not carried forward)

| Cohort date | Records | cpe_hours | reinstatement | renewal_fees | Gate-blocking cliff (verified_date + 30d) |
|---|---|---|---|---|---|
| 2026-09-09 | 6 | 0 | 0 | 6 | **2026-10-09** |
| 2026-09-12 | 115 | 48 | 45 | 22 | 2026-10-12 |
| 2026-09-13 | 1 | 0 | 0 | 1 | 2026-10-13 |
| 2026-09-19 | 3 | 2 | 1 | 0 | 2026-10-19 |
| 2026-09-22 (batch 1, DONE) | 32 | 1 | 5 | 26 | 2026-10-22 |

Batch 1 (32 records: 1 cpe_hours + 5 reinstatement + 26 renewal_fees, all originally 2026-09-09)
already shipped 2026-09-22, `CONFIRMED_UNCHANGED` 32/32 — see HANDOFF's 2026-09-22 ~12:00 MDT
entry. **125 records remain**, across the 09-09/09-12/09-13/09-19 cohorts above.

## Batches 2–5

Front-loaded, not evenly spread by calendar week — the whole 09-09 (6) and 09-12 (115) cohorts
must clear before the 2026-10-09 cliff, since that's the earliest hard block. The 09-13 (1) and
09-19 (3) stragglers are small enough to fold into the last batch rather than earn their own.

| Batch | Target ship date | Records | Composition | Method |
|---|---|---|---|---|
| **2** | by 2026-09-27 | 32 | all 6 remaining 09-09 + oldest 26 of the 09-12 cohort | real citation_url fetch + field-level comparison, same methodology as batch 1 |
| **3** | by 2026-10-01 | 31 | next 31 of the 09-12 cohort (26 consumed so far, 58 remain after this) | " |
| **4** | by 2026-10-05 | 31 | next 31 of the 09-12 cohort (57 consumed so far, 27 remain after this) | " |
| **5** | by 2026-10-08 (day before the first cliff) | 31 | remaining 27 of 09-12 + the 1 09-13 record + all 3 09-19 records | " |

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
