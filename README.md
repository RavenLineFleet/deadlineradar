# DeadlineRadar — CPA license renewal deadline tracker (live production site)

**Status: this repo IS the live public site.** It is the GitHub Pages source for
**deadline-radar.com** (repo must stay **public** — GitHub Pages on the free tier only serves
public repos; flipping this repo private silently takes the entire live site down, as happened
2026-09-03). `docs/` is the deployed static site (one page per state), paired with a Cloudflare
Worker API (`worker/`, Stripe billing, D1-backed subscriber/firm accounts, SendGrid email) that
is a **separate deploy** from this repo (`worker/.last_deploy_commit` tracks its own marker —
pushing this repo does not redeploy the Worker). Current scope, state coverage, and feature
status change often enough that this file does not try to track them precisely — the operator's
own HANDOFF.md and passive_income_register.md (outside this repo) carry the live, current
picture. The sections below describe the **content pipeline** (`generate.py`) itself, which is
stable and still accurate.
Scope: CPA license renewal only. A companion "contractor licensing" vertical was scouted and
deliberately dropped before any build: a 10-state sample found most states have no single
state-level general-contractor license to point a page at at all (licensing is fragmented across
per-trade boards or pushed down to municipal registration), which breaks the one-page-per-state
model this pipeline depends on. CPA licensing cleared that same check cleanly (a majority of
states use a fixed calendar renewal date), so "renarrow to CPA-only" is a deliberate scope
decision made from real research, not a shortcut.

This started as a proof that **the ingest → normalize → generate pipeline produces correct,
non-stale, per-state pages from verified source data** — that proof succeeded, and the site
built on it is now live (see Status above). The sections below still describe that original
content-pipeline proof accurately; for the business/product layer built on top of it (accounts,
billing, distribution), see HANDOFF.md.

## Pipeline

```
data/cpa_deadlines.json   (ingest: hand-verified facts, one record per state/license-type/cohort)
        |
        v
   generate.py            (normalize: compute next_deadline_computed forward from as_of_date;
        |                   render: stdlib string templating, no framework, no network)
        v
   docs/                  (output: one folder per state + index + sitemap.xml + robots.txt)
```

- **Ingest.** `data/cpa_deadlines.json` holds one flat array of *records*. A record is the
  smallest unit of a renewal fact: `{state, state_slug, license_type, renewal_pattern,
  cycle_description, next_deadline_computed, source_url, last_verified, wave}`, plus a few
  pattern-specific extras (`cohort_groups` for Ohio's 3-group system, `computation` for the
  birth-month states, `data_gap_note` where the verified spike data doesn't give us enough
  to compute a date). A state can have more than one record (Florida has 3: two individual
  cohorts + firm; Georgia has 2: individual + firm). `generate.py` groups records by
  `state_slug` to build one page per state.

- **Normalize / compute.** `next_deadline_computed` in the JSON is only trustworthy as of
  `as_of_date` (2026-07-03) — it is *not* re-derived by `generate.py` from the raw renewal
  rule; it was computed once by hand (and double-checked with a throwaway Python script
  during the build) and stored as a plain ISO date. **This is a known limitation, not an
  oversight:** re-run this pipeline after `as_of_date` has passed and the wave-1/2 dates go
  stale silently, because the JSON's stored dates don't move with the calendar. Wave-3
  (birth-month) states don't have this problem — their tables are computed live, every run,
  from `date.today()`-equivalent math seeded by `as_of_date`, so they're correct for any
  `as_of_date` you set. See "Known limitation" below for the fix before this ships past
  prototype stage.

  `generate.py` does carry one live safety check: before writing anything, it refuses to
  build if any record's `next_deadline_computed` is on-or-before `as_of_date` (a stale/past
  date would silently ship a wrong deadline — see the `stale = [...]` guard in `main()`).
  That guard only catches staleness *relative to the JSON's own `as_of_date`*, not staleness
  from someone running the script six months later on the same file — see the limitation
  below.

- **Wave-3 birth-month computation.** California and Texas don't have one fixed date — the
  deadline depends on the reader's own birth month (and, for California, their birth year's
  odd/even parity). Rather than fake a single date, `generate.py` builds a full lookup table
  (12 months × the relevant parity split) computed from the actual calendar, so every cell is
  a real, non-stale date. New York is different again: its rule depends on the reader's
  *first-registration date*, which isn't a fact this dataset has at all, so its page states
  that plainly and sends the reader to the official NYSED lookup instead of asserting
  anything invented.

- **Generate.** `generate.py` is Python stdlib only (`json`, `html`, `pathlib`, `datetime`) —
  no templating framework, no `pip install`, no network access of any kind. It writes:
  - `docs/[state-slug]/index.html` — one page per state, every field driven by the JSON record(s)
  - `docs/index.html` — directory of all state pages
  - `docs/sitemap.xml` — valid XML sitemap, one `<url>` per generated page + the index, each
    with a `<lastmod>`
  - `docs/robots.txt` — allow-all, points at the sitemap

  All URLs in `sitemap.xml`/`robots.txt` use the real `https://deadline-radar.com` base
  (`SITE_BASE_URL` in `generate.py`) — publishing was a deliberate, gated step, and it has
  happened; see the top of this file.

## Running it

```
cd b3_saas/deadlineradar
python generate.py
```

Output goes to `docs/`. Re-running is idempotent — it overwrites the same files: one
`docs/[state-slug]/index.html` per state in `data/cpa_deadlines.json`, plus `docs/index.html`,
`docs/sitemap.xml`, `docs/robots.txt` — this is the live production build, so the actual state
count changes as coverage grows; check `data/cpa_deadlines.json` (or HANDOFF.md) rather than
trusting a number hardcoded here.

## Data coverage patterns (original 10-state spike sample — patterns still accurate; see `data/cpa_deadlines.json` for the current full state count)

| Wave | States | Pattern |
|---|---|---|
| 1 | FL, IL, PA, GA, NC, MI | Fixed calendar date (some with cohort splits — FL individual odd/even, GA firm separate from individual) |
| 2 | OH | Fixed date (Dec 31 + Jan 31 grace) but only ~1/3 of licensees due per year — 3-group rotating cohort, explained with a table instead of one asserted date |
| 3 | CA, TX, NY | No single fixed date — CA/TX depend on the licensee's own birth month (rendered as a full lookup table); NY additionally depends on first-registration date, which this dataset doesn't have, so its page is explicitly "look it up yourself" |

One known data gap, carried honestly rather than papered over: **Illinois firm-license
renewal** (`il-firm` record) has a confirmed recurring date (November 30) and cycle length
(3 years) but no confirmed anchor year in the verified spike data, unlike the Illinois
individual track which has one ("current cycle ends 2027-09-30"). `next_deadline_computed`
is `null` for that record and the generated Illinois page says so instead of guessing a year.

## How to add a new state

1. **Verify the renewal rule against the state board's own page** (not a secondary source).
   Confirm: is it a single fixed calendar date, a fixed date with a cohort/group split, or
   personal (birth-month / anniversary)? Get the *current* cycle-anchor year if it's
   multi-year, not just the recurring month/day.
2. Add one or more records to `data/cpa_deadlines.json`:
   - Fixed single date → set `renewal_pattern: "fixed_calendar"` and compute
     `next_deadline_computed` by hand (or with a throwaway script like the one used for this
     build) as the actual next occurrence on-or-after today.
   - Fixed date but only a subset of licensees due each year → follow the Ohio pattern:
     `renewal_pattern: "fixed_calendar_cohort"`, `next_deadline_computed: null`, and a
     `cohort_groups` array of `{group, years, next_deadline}`.
   - Personal/birth-month → follow the CA/TX pattern: `renewal_pattern: "birth_month"`,
     `next_deadline_computed: null`, and a `computation` object describing how
     `generate.py` should build the lookup table (`birth_month_parity` for a 2-year
     odd/even-birth-year split like CA, `birth_month_annual` for a straight annual repeat
     like TX). If the rule needs a fact this dataset can't have (like NY's
     first-registration date), use `computation.type: "unresolvable_needs_registration_date"`
     and write a clear note — don't invent a table.
   - Always fill `source_url` (the official state board page) and `last_verified` (the date
     you actually checked it, not today's build date if they differ).
3. If the new state needs page logic beyond the three existing patterns (e.g. a genuinely
   new cohort shape), add a small render function in `generate.py` next to `render_ohio` /
   `render_california` / `render_texas` / `render_new_york`, and branch to it in
   `build_state_page()` by `state_slug`, same as the existing ones.
4. Run `python generate.py`. It will refuse to build (raises `SystemExit`) if any record's
   `next_deadline_computed` is on-or-before `as_of_date` — that's the stale-data guard
   catching a bad hand computation before it ships.
5. Spot-check the generated `docs/[new-slug]/index.html`: title has state + year + "CPA
   License Renewal Deadline", the date shown is genuinely in the future, the source link
   works, "Last verified" is present, and the back-link to `../` resolves.

## Known limitation (flag for whoever picks this up next)

`next_deadline_computed` for wave-1/2 records is a **stored value**, computed once against
`as_of_date: 2026-07-03`, not a live computation from the raw renewal rule the way the
wave-3 birth-month tables are. **Partially mitigated:** `generate.py` now refuses to build
(`SystemExit`) if `as_of_date` is more than 30 days old relative to real wall-clock time
(`date.today()`), not just relative to the JSON's own `as_of_date` field — this was a real gap
found and fixed during adversarial verification (see `verification_note.md`), and it's
independently tested (confirmed the guard actually fires on artificially-staled input, not just
present-but-unreachable code). That guard stops the pipeline from silently shipping a stale site
if it's re-run long after `as_of_date` without anyone updating the data — but it's a tripwire,
not a fix: past 30 days, the build simply refuses to run at all until someone re-verifies every
record and bumps `as_of_date`. Before this goes anywhere near a real launch: re-derive every
wave-1/2 record from its raw rule (month/day + cycle length + anchor year) at generate-time the
same way wave-3 already does, so `as_of_date` can be bumped freely and every date recomputes
correctly without a human re-verification pass each time.

## GitHub Pages deployment (live)

Output is written to `docs/` (not `site/`) specifically because that's GitHub's zero-config Pages
convention: repo **Settings > Pages > Deploy from a branch > `main` / `/docs`**. This is live —
pushing a `generate.py` rebuild to `main` is the entire deploy for the static site (see
`worker/.last_deploy_commit` for the separate Worker deploy step, which pushing this repo does
NOT trigger).
- Every internal link in the generated pages is relative (`../` back-links, no `href="/..."` or
  `localhost`/`127.0.0.1` references anywhere in `docs/`).
- `sitemap.xml`'s `<loc>` entries and `robots.txt`'s `Sitemap:` line use the real
  `https://deadline-radar.com` base (`SITE_BASE_URL` in `generate.py`).
- Custom domain `deadline-radar.com` is on file in Pages settings, `docs/CNAME` matches it.
**Hard requirement to keep this live:** the repo must stay **public**. GitHub Pages on a free
plan refuses to serve a private repo at all (Settings > Pages shows "Upgrade or make this
repository public to enable Pages" instead) — there is no warning banner elsewhere, the site
just silently 404s. If Pages config is ever reset, Source must be re-set to `main` / `/docs`
manually; a repo visibility flip does not auto-restore it even after reverting to public.

## The "remind me" feature

Every state page and the homepage carry a signup form (email + whatever state-specific field is
needed to compute *that reader's* exact deadline — see `signup_form_for_state()` /
`signup_form_homepage()` in `generate.py`) feeding a reminder engine: double opt-in, an
escalating reminder schedule (60/30/14/7/3/1 days out), one-click "I've renewed" / unsubscribe,
and a re-arm offer for the next cycle. This section originally described the local dry-run
version of that engine (`reminders/`, `DryRunSender`, no real send wired up) built before the
live site existed — the production version now runs on real transactional email (see the Worker
API in the Status section above and `reminders/README.md`/HANDOFF.md for current architecture
and send status); `reminders/` itself may still be relevant as the original local prototype the
production engine grew from, but check before assuming it's what's live.

## Current status — see HANDOFF.md, not this section

This README used to describe an early undeployed prototype (10 states, no domain, no Stripe,
dry-run-only email) and was left unmaintained as the project grew into the live production site
described at the top of this file — that staleness is what caused a 2026-09-03 outage (an agent
trusted this section and treated the live repo as safe to make private). Rather than re-stating
specifics here that will drift out of date the same way, current state, count, and feature
status live in the operator's own HANDOFF.md and passive_income_register.md (outside this repo)
— check those, not this file, for what's actually live right now.
