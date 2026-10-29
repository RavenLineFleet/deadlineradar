"""
Deadline-Radar i18n: keyed English source-of-truth + Spanish translations,
Phase A rollout (2026-08-19, Devin's direct go-ahead in an RC session).

Design (full writeup: Orchestrator/outbox/assetlab.md, 2026-08-19T13:20 entry):
  - EN is the single source of truth. generate.py's render functions call
    t(key, lang) instead of hardcoding literal English -- this is the ONLY
    place English UI/marketing copy should live once a page is converted.
  - Every EN string has a content hash. A Spanish translation records the
    hash of the EN string it was translated FROM (en_hash). If the English
    string changes later, the stored hash no longer matches -- t() detects
    this and falls back to English rather than ever showing a stale
    translation silently.
  - A translation only ships in Spanish once `reviewed: True` -- this is
    set by a human/AuditLab step, never by whichever agent drafted it (see
    scripts/es_translation_review.py). A freshly (re)drafted translation is
    always reviewed=False until that happens, and t() falls back to
    English for it exactly like a missing translation -- "no unreviewed
    Spanish string ever shows to a real visitor" is enforced by this
    fallback, not by convention.
  - Proper nouns (SITE_NAME, BRAND_NAME) and pure data (counts, dates,
    citation text) are never put in EN/ES directly -- they are passed as
    format() kwargs at call time, same word in every language.

Scope note: only Phase A (UI chrome + top marketing pages) uses this
module. The 55 state data pages (citations, computed dates, legal
consequences) are explicitly NOT in scope until a review-capacity
decision is made -- see the plan doc.
"""

from __future__ import annotations

import hashlib


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# EN: the source of truth. Every string a Phase A page renders to a reader
# (nav labels, footer links/headings, page prose) lives here, keyed by a
# "section.name" identifier. {placeholders} are str.format() fields filled
# in by the caller with proper nouns/numbers/dates -- never translated text.
# ---------------------------------------------------------------------------
EN: dict[str, str] = {
    # Shared chrome -- page_shell() skip link (AuditLab A11Y-13, 2026-08-27)
    "a11y.skip_to_content": "Skip to content",
    # Shared chrome -- site_header()
    "site.tagline": "CPA license renewal deadlines by state — verified and kept current",
    "nav.browse_states": "Browse States",
    "nav.how_we_verify": "How We Verify",
    "nav.guides": "Guides",
    "nav.for_firms": "For Firms",
    "nav.pricing": "Pricing",
    "nav.live_demo": "Live Demo",
    "nav.sign_in": "Sign In",
    "nav.get_reminders": "Get reminders",
    "nav.dashboard": "Dashboard",  # client-side JS swap once a firm session is detected
    # Shared chrome -- site_footer()
    "footer.heading_data_method": "Data & Method",
    "footer.link_mobility_rule_changes": "Mobility Rule Changes",
    "footer.link_practice_privilege_check": "Practice Privilege Check",
    "footer.link_multi_state_firms": "Multi-State Firms",
    "footer.link_compliance_reports": "Compliance Reports",
    "footer.heading_product": "Product",
    "footer.link_all_jurisdictions": "All {count} jurisdictions",
    "footer.link_pricing": "Pricing",
    "footer.link_deadline_calculator": "Deadline Calculator",
    "footer.link_cpe_vs_license": "CPE vs. License Renewal",
    "footer.heading_company": "Company",
    "footer.link_about": "About",
    "footer.link_contact": "Contact",
    "footer.link_security": "Security",
    "footer.link_status": "Status",
    "footer.link_terms": "Terms",
    "footer.link_privacy": "Privacy",
    "footer.trust_chip": "No ad or social trackers. Cookieless analytics only.",
    # Split at the original HTML's exact <strong>...</strong> boundary (only
    # the first sentence is bold) so the markup lives in generate.py, not
    # embedded inside translatable text.
    "footer.disclaimer_bold": "{site_name} is an independent reminder service operated by {brand_name}.",
    "footer.disclaimer_rest": (
        "It is not affiliated with, endorsed by, or connected to NASBA, the AICPA, or any state "
        "board of accountancy. Renewal dates are compiled from public sources for informational "
        "purposes only — not legal, tax, or professional advice. Always confirm your exact renewal "
        "date with your state board or on your license."
    ),
    # /methodology/ -- build_methodology_page()
    "methodology.title": "How We Verify Every Deadline",
    # The "{verified_recent} of {total}" lead-in is bolded in generate.py,
    # OUTSIDE this string (matches the original's <strong> boundary around
    # just the two numbers) -- this key is the sentence that follows it.
    "methodology.freshness_stat": (
        "dated records across this site's datasets (renewal deadlines, "
        "CPE hours, reinstatement, renewal fees) were individually re-checked against their source "
        "within the last {threshold_days} days, as of this page's last build ({build_date}). Every "
        "state page's own “Last verified” line shows that specific citation's own date — "
        "this is the same fact, rolled up across the whole site."
    ),
    "methodology.intro": (
        "CPAs are trained to be skeptical of unverified sources — so here is exactly how this "
        "site's dates are sourced, checked, and kept current. Nothing below is aspirational; it "
        "describes the actual standard already applied to every state page."
    ),
    "methodology.h2_two_source_rule": "The two-source rule",
    "methodology.two_source_intro": "Every date on this site must trace to two independent things before it's published:",
    # Inline <strong> emphasis is part of these two strings deliberately
    # (matches the original hand-written HTML) -- a translation must
    # preserve the tags, not just the words.
    "methodology.two_source_item1": (
        "<strong>The state board's own page</strong> — the plain-English source most people would "
        "find first."
    ),
    "methodology.two_source_item2": (
        "<strong>The actual codified statute or administrative rule</strong> the board's requirement "
        "derives from — not a summary of it, the primary legal text itself. That citation and a "
        "direct link to it are shown under every verified date on this site, labeled “Source of "
        "record.”"
    ),
    "methodology.two_source_fallback": (
        "If we can't find or confirm the second source, the date is not published as a confirmed "
        "fact. Instead the page says so plainly and points you to the official state board to "
        "determine your own exact deadline — we do not guess, interpolate, or infer a date we "
        "can't back up with primary law."
    ),
    "methodology.h2_verified_badge": "What the “Verified” badge means",
    "methodology.verified_badge_body": (
        "A callout shows a <strong>Verified</strong> badge only when that specific date has a real "
        "citation to codified law behind it, checked the way described above. A record without one "
        "never shows the badge — there is no in-between state where a date looks confirmed but isn't."
    ),
    "methodology.h2_last_verified": "What “Last verified” means",
    "methodology.last_verified_intro": (
        "The date shown in each state's trust line is the last time we directly re-checked that "
        "state's citation against the primary source text — not just re-read our own notes about "
        "it. We periodically re-run an automated check across every cited source looking for two "
        "things:"
    ),
    "methodology.last_verified_item1": "a broken or redirected link, or",
    "methodology.last_verified_item2": "any sign the underlying rule has since been amended.",
    "methodology.last_verified_followup": (
        "When either turns up, we re-verify by hand before changing anything a visitor sees — an "
        "automated flag never silently rewrites a published date by itself."
    ),
    "methodology.h2_fall_short": "Where this can still fall short, honestly",
    "methodology.fall_short_body": (
        "Some sources are genuinely harder to verify by automated means — a handful of citations "
        "point to PDF documents or JavaScript-rendered pages our tooling can't text-extract "
        "automatically. Where that's the case, those citations were still individually confirmed by "
        "hand at the time they were published; we disclose the tooling gap rather than pretend an "
        "easier check covers it. If a rule changes between our checks, use the contact link below to "
        "flag it and we'll re-verify and correct it quickly. Articles here are credited to Moose & "
        "Raven LLC, the company behind Deadline-Radar, rather than to a named individual author — the "
        "honest construction for a team this size, and every fact stands on its own cited source "
        "regardless of byline."
    ),
    "methodology.h2_what_we_dont_verify": "What we don't verify this way",
    "methodology.dont_verify_body": (
        "CPE hour completion is self-reported wherever this site or its firm tier ever discusses it "
        "— we label that clearly and never give it the same “Verified” treatment as a "
        "sourced renewal date. We also don't independently verify a state's future policy changes; if "
        "a state proposes a new rule that hasn't taken effect yet, we wait for it to become the "
        "actual current rule before citing it."
    ),
    "methodology.h2_see_for_yourself": "See it for yourself",
    "methodology.see_for_yourself_body": (
        "Pick any state page and look for the “Source of record” line under its date — "
        "the citation and the “read the rule” link go to the primary legal text, not a "
        "summary. That's the same standard behind every date on this site."
    ),
    "methodology.backlink_changelog": "See exactly what's changed and when →",
    "methodology.backlink_contact": "Found something that looks wrong? Tell us →",
    "methodology.meta_description": (
        "Deadline-Radar's sourcing standard: every renewal date traces to the state board's own "
        "page plus the codified rule behind it — never a guess."
    ),
    # /contact/ -- build_contact_page()
    "contact.h1": "Contact",
    "contact.intro": "Questions, a correction to a deadline, or anything else — we'd like to hear from you.",
    "contact.h2_email_us": "Email us",
    # Inline <a> links (mailto/security.txt/RFC) are part of this string
    # deliberately -- the surrounding sentence translates, the links and
    # their targets don't. {email_link}/{security_txt_link}/{rfc_link} are
    # pre-built HTML fragments filled in by generate.py, not translated text.
    "contact.email_body": (
        "We read every message and usually reply within a couple of business days. This is a "
        "small, independent project — there's a real person on the other end, not a support "
        "queue. Our address is also published machine-readably at {security_txt_link} per "
        "{rfc_link}."
    ),
    "contact.h2_wrong_date": "Spotted a wrong date?",
    "contact.wrong_date_body": (
        "Deadlines are compiled from official state board sources and we work hard to keep them "
        "current, but rules change. If a date looks off, email us the state and what you're "
        "seeing and we'll verify it against the source and fix it fast. Always confirm your exact "
        "deadline with your state board before relying on it."
    ),
    "contact.h2_stop_reminders": "Stop your reminders",
    "contact.stop_reminders_body": (
        "The fastest way to stop reminders is the one-click unsubscribe link at the bottom of any "
        "email we send — it's instant and permanent. You're welcome to email us too."
    ),
    "contact.h2_mailing_address": "Mailing address",
    "contact.meta_description": (
        "Contact Deadline-Radar — questions, deadline corrections, or help with your CPA "
        "license renewal reminders. We read every message."
    ),
    # /multi-state-firms/ -- build_multi_state_firms_page()
    "msf.h1": "Running a Multi-State CPA Firm? Here's the Full Picture.",
    "msf.intro": (
        "A firm with staff licensed or practicing across more than one state has a genuinely "
        "different problem than a single-state firm: knowing where everyone can legally work, "
        "catching it before a rule changes underneath you, and keeping a citation behind every "
        "answer. Three pieces of this site work together for exactly that."
    ),
    "msf.h2_map": "1. Map — see every state your team can practice in",
    "msf.map_body": (
        "A color-coded map of exactly which states your team can practice in today without a "
        "local license, plus a firm-level registration check for attest work where your firm "
        "itself (not just the individual CPA) needs to register. Part of a paid firm plan — "
        "{pricing_link}."
    ),
    "msf.see_plans": "see plans",
    "msf.h2_ppc": "2. Practice Privilege Check — verify before staff take on out-of-state work",
    "msf.ppc_body": (
        "Before a staff CPA takes on work in a state they're not locally licensed in, run the "
        "check: service type, home state, target state, and the answer comes back with the rule "
        "and citation behind it — never a guess. {ppc_link}."
    ),
    "msf.ppc_link_text": "Free for any account, no paid plan required",
    "msf.h2_rule_changes": "3. Rule Changes — a running feed, not a one-time check",
    "msf.rule_changes_body": (
        "A running feed of confirmed and pending changes to interstate CPA mobility rules — "
        "practice privileges, notice/fee requirements, and firm registration — sourced the same "
        "way every date on this site is: a citation to the primary statute or rule where we could "
        "confirm it, and clearly labelled where we could only confirm it against the board's own "
        "page, never a guess. Your firm's own calendar surfaces the changes that actually affect "
        "your roster's states. {feed_link}."
    ),
    "msf.feed_link_text": "See the full public feed",
    "msf.try_demo": "Try the live demo →",
    "msf.new_here_bold": "New to Deadline-Radar?",
    "msf.new_here_rest": (
        "See the {overview_link} for pricing, the whole feature set, and how renewal-date "
        "tracking fits alongside these three."
    ),
    "msf.overview_link_text": "full firm overview",
    "msf.backlink_all_states": "← Back to all states",
    "msf.title": "Multi-State CPA Firms: Mobility & Rules",
    "msf.meta_description": (
        "For a CPA firm with staff across multiple states: a coverage map, a free Practice "
        "Privilege Check, and a mobility rule-change feed — all sourced and cited."
    ),
    # /practice-privilege-check/ -- build_practice_privilege_landing_page()
    "ppc.h1": "Practice Privilege Check: Can a CPA Work in Another State Without a License?",
    "ppc.subhead": (
        "Can this CPA provide this service in this state — and what has to happen first? "
        "Every answer is tied to the rule it came from."
    ),
    "ppc.callout_bold": "Informational, not legal advice.",
    "ppc.callout_rest": (
        "Practice-privilege rules change, and they depend on facts we can't see. We show you the "
        "rule and where it came from so you can check it yourself — and where we haven't "
        "verified something against a primary source, we say so instead of guessing. Confirm with "
        "the state board before you rely on any answer here."
    ),
    "ppc.h2_what_it_does": "What Practice Privilege Check actually does",
    "ppc.what_it_does_intro": (
        "A different question from renewal dates: can this CPA provide this specific service in "
        "this specific state right now, without a local license — and what has to happen first?"
    ),
    "ppc.item_pick_service_bold": "Pick a service type:",
    "ppc.item_pick_service_rest": "Tax; Attest (audit, review, or other attest); or Other non-attest (consulting, advisory).",
    "ppc.item_attest_gap_bold": "Watch for the attest gap:",
    "ppc.item_attest_gap_rest": (
        "attest work frequently triggers a firm-registration requirement where tax work doesn't "
        "— that's the most common real-world mobility mistake, and this catches it."
    ),
    "ppc.item_confirm_bold": "What you'll need to confirm:",
    "ppc.item_confirm_rest": (
        "the license is active and in good standing, and the CPA meets substantial equivalence "
        "(150 semester hours, one year of experience, the Uniform CPA Exam). We can't verify "
        "either input ourselves — the answer is only as good as what you tell it, same "
        "honesty standard as every renewal date on this site."
    ),
    "ppc.coverage_body": (
        "Alongside the individual question above there is a separate <strong>firm-level "
        "registration check</strong> — does the FIRM itself need to register somewhere it has no "
        "office, even when the individual CPA is covered. Neither check has a settled answer for "
        "every jurisdiction and service. Where it doesn't — for example, the rule is mid-change, "
        "its primary sources disagree with each other, the qualifying pathway for out-of-state "
        "CPAs is still unsettled, or we hold no separate firm-level rule for the service you "
        "picked (there is none yet for other non-attest work) — the check says so instead of "
        "guessing; confirm with that state's board."
    ),
    "ppc.free_tier_body": (
        "The individual check is free on every tier, for any account — a free signup is all "
        "it takes, no card, no paid plan required. {pricing_link}."
    ),
    "ppc.free_tier_link_text": "The firm-level check and the multistate coverage map are part of a paid plan",
    "ppc.run_check": "Create a free account and run a check →",
    "ppc.tracking_bold": "Tracking a whole firm's roster, not just one lookup?",
    "ppc.tracking_rest": (
        "See the {overview_link} — Roster, Calendar, CPE tracking, and individual Practice "
        "Privilege Check are free there too; paid tiers add the multistate map and the firm-level "
        "registration check. See {pricing_link2}."
    ),
    "ppc.overview_link_text": "firm overview",
    "ppc.full_pricing_link_text": "full pricing",
    "ppc.backlink_all_states": "← Back to all states",
    "ppc.title": "Practice Privilege Check",
    "ppc.meta_description": (
        "What CPA practice privilege means, and how to check if a CPA can serve a client in "
        "another state without a local license — free, all 55 jurisdictions."
    ),
    # /deadline-calculator/ -- build_deadline_calculator_page(). Scope note:
    # the interactive calculator widget itself (state dropdown, JS result
    # text) and the state-name/cycle-type table cells are NOT converted --
    # same "computed personal data, not marketing copy" reasoning that
    # keeps the 55 state pages out of Phase A. Only the surrounding static
    # prose is keyed here.
    "calc.h1": "CPA License Renewal Deadline Calculator",
    "calc.intro": (
        "Select your state below — and your birth month or cohort group, if your state's rule "
        "needs one — to see your exact renewal deadline right here. Free, no signup, no account "
        "required. Every date is sourced — to your state board's codified statute or rule where "
        "we could confirm it against primary law, and clearly labelled where we could only "
        "confirm it against the board's own page — {methodology_link}."
    ),
    "calc.see_exactly_how": "see exactly how",
    "calc.trust_jurisdictions": "jurisdictions covered",
    "calc.trust_determined": "where we compute your exact date",
    "calc.trust_records": "dated records across this site's datasets (renewal deadlines, CPE hours, reinstatement, renewal fees) re-checked in the last 30 days",
    "calc.h2_what_calculated_looks_like": "What “calculated” actually looks like, state by state",
    "calc.what_calculated_intro": (
        "This isn't one formula — every state renews on its own rule, and those rules take "
        "genuinely different shapes. A few real examples, straight from the same dataset the "
        "calculator above draws on:"
    ),
    "calc.table_th_state": "State",
    "calc.table_th_cycle_type": "Cycle type",
    "calc.table_th_returns": "What the tool returns",
    "calc.answer_personal_fact": "Depends on a personal fact — the calculator above asks for it, then answers",
    "calc.answer_no_public_rule": "No public rule ties this to a knowable input — the calculator says so, honestly",
    "calc.h2_how_it_works": "How this actually works",
    "calc.how_it_works_body": (
        "Picking your state above looks up your individual license date in the exact same "
        "dataset that state's own page renders from — there's no second, different formula "
        "running here. Most states resolve to one plain date immediately. States whose rule "
        "depends on a personal fact — your birth month, or which cohort group your license "
        "falls in — ask for that fact before answering, using the exact same inputs that "
        "state's own rule actually uses. Either way, the date you get is never estimated or "
        "guessed — if we can't confirm it against a primary source, the calculator says so "
        "instead of showing a number, same as the table above shows for a state where that's "
        "true. A state whose firm registration renews on a separate clock from the individual "
        "license gets a pointer to "
        "that state's full page — the calculator above answers the individual question only."
    ),
    "calc.prefer_browse": "Prefer to browse a state's full page directly instead of using the calculator above?",
    "calc.h2_faq": "Frequently asked",
    "calc.faq1_q": "Is this actually free?",
    "calc.faq1_a": (
        "Yes — looking up your own renewal date has never required an account, a signup, or a "
        "fee. The paid tier is for firms tracking a whole staff roster in one place, not for an "
        "individual checking their own date."
    ),
    "calc.faq2_q": "Why does my state say “enter your date” instead of showing one?",
    "calc.faq2_a": (
        "Some states' renewal cycles genuinely depend on a fact we don't have — when you were "
        "originally licensed, which cohort group you're in, or similar — and the state board "
        "itself doesn't publish a lookup we can compute from. Rather than guess, the page says so "
        "and lets you enter your own known date to track from there."
    ),
    "calc.faq3_q": "How do you know these dates are right?",
    "calc.faq3_a": (
        "Every date traces to either the state board's own published page or the actual codified "
        "statute/rule, with the date we last confirmed it shown on that state's own page — see "
        "the {verification_writeup_link}."
    ),
    "calc.verification_writeup_link_text": "full verification writeup",
    "calc.tracking_bold": "Tracking a whole firm's staff, not just your own license?",
    "calc.tracking_rest": (
        "See the {overview_link} — the same free, sourced deadline data in one roster view "
        "instead of one state at a time."
    ),
    "calc.overview_link_text": "firm overview",
    "calc.backlink_all_states": "← Back to all states",
    "calc.title": "CPA License Renewal Deadline Calculator",
    "calc.meta_description": (
        "Free CPA license renewal deadline calculator — pick your state, get your exact renewal "
        "date, cited to the statute, rule or board page. No signup required."
    ),
    # /deadline-calculator/ widget chrome + result sentences (AuditLab I18N-2, 2026-10-11) -- everything the
    # interactive calculator prints. {placeholders} are filled client-side by fillTpl() in _DEADLINE_CALC_JS;
    # values containing HTML entities (&hellip; &amp; &rarr;) are written into innerHTML on purpose.
    # calcw.date_style packs the date template and the 12 month names into ONE key (separated by "|") so
    # an unreviewed/stale Spanish entry can never mix English months into a Spanish date or the reverse.
    "calcw.state_label": "Your state",
    "calcw.select_state": "Select your state&hellip;",
    "calcw.select": "Select&hellip;",
    "calcw.select_group": "Select your group&hellip;",
    "calcw.cohort_label": "Your cohort group",
    "calcw.cohort_hint": (
        "Not sure which group applies to you? Check your license certificate or the state board\u2019s lookup."
    ),
    "calcw.birth_month": "Your birth month",
    "calcw.birth_year": "Your birth year",
    "calcw.ph_birth_year": "e.g. 1985",
    "calcw.cert_date": "Your original certificate date",
    "calcw.last_issuance": "Last issuance or renewal date",
    "calcw.ph_year_a": "e.g. 2023",
    "calcw.ph_year_b": "e.g. 2025",
    "calcw.next_due": "Your next {label} renewal is due <strong>{date}</strong>.",
    "calcw.next_due_group": "Your next {label} renewal ({group}) is due <strong>{date}</strong>.",
    "calcw.cpe_clause": " (your CPE for that cycle is due {date})",
    "calcw.passed": (
        "That date has already passed \u2014 check your most recent certificate/permit for its exact renewal date."
    ),
    "calcw.year_one": "{n} year",
    "calcw.year_many": "{n} years",
    "calcw.firm_prefix": "This is your individual license date. ",
    "calcw.firm_different": (
        "Your firm\u2019s own registration renews on a different clock \u2014 see the full {label} page below."
    ),
    "calcw.firm_separate": (
        "A firm registration is a separate filing from individual licenses \u2014 see the full {label} page "
        "below for the firm\u2019s details."
    ),
    "calcw.cta": "See the full {label} page &amp; set up a free reminder &rarr;",
    "calcw.date_style": (
        "{month} {day}, {year}|January|February|March|April|May|June|July|August|September|October|November|December"
    ),
    # Data-label strings shown by the calculator (cohort group names, anchor/term field labels). The generator maps each English
    # label in the data/config to one of these keys (_CALC_LABEL_KEYS) and fails the build on an unmapped one.
    "calcw.lbl_cohort_due_2027": 'Cohort due 2027',
    "calcw.lbl_cohort_due_2028": 'Cohort due 2028',
    "calcw.lbl_cohort_due_2029": 'Cohort due 2029',
    "calcw.lbl_even_birth_year_cpas": 'Even-birth-year CPAs',
    "calcw.lbl_even_birth_year_cohort": 'Even-birth-year cohort',
    "calcw.lbl_even_cert_holders": 'Even-numbered certificate holders',
    "calcw.lbl_even_license_holders": 'Even-numbered license holders',
    "calcw.lbl_even_permit_holders": 'Even-numbered permit holders',
    "calcw.lbl_group_1": 'Group 1',
    "calcw.lbl_group_2": 'Group 2',
    "calcw.lbl_group_3": 'Group 3',
    "calcw.lbl_odd_birth_year_cpas": 'Odd-birth-year CPAs',
    "calcw.lbl_odd_birth_year_cohort": 'Odd-birth-year cohort',
    "calcw.lbl_odd_cert_holders": 'Odd-numbered certificate holders',
    "calcw.lbl_odd_license_holders": 'Odd-numbered license holders',
    "calcw.lbl_odd_permit_holders": 'Odd-numbered permit holders',
    "calcw.lbl_anchor_last_renewal": 'Year of your last renewal (or initial licensure, if never renewed)',
    "calcw.lbl_anchor_first_licensed": 'Year you were first licensed',
    "calcw.lbl_anchor_last_issued": 'Year your license was last issued or renewed',
    "calcw.lbl_term_length": 'Term length shown on your certificate',
    # /pricing/ -- build_pricing_page(). Shared with /for-firms/: the FAQ
    # (_FIRM_FAQ) and the feature-table row labels.
    "pricing.h1": "Pricing",
    "pricing.intro": (
        "Roster, calendar, CPE-hours tracking, and individual Practice Privilege Check are "
        "<strong>free for any firm, up to 3 staff</strong>, no card required, no time limit. A "
        "one-person account also gets the multistate map and firm-level registration check free "
        "(see the table below); paid firm plans add those two for a firm with more than one "
        "person — every paid tier has the identical feature set, priced only by how many staff it "
        "covers; nothing is held back on a cheaper plan. A person licensed in several states "
        "counts once toward the staff limit, and your firm's own permit and registration lines "
        "don't count toward it. Your firm's own permit or registration renewal date is just "
        "another roster line, the same on every plan: it uses no staff seat (up to 2 per state, 25 "
        "in total). The paid firm-level registration check is a different feature: it looks up "
        "whether other states require your firm to register to practice there."
    ),
    "pricing.freshness_stat": (
        "dated records across this site's datasets (renewal deadlines, CPE hours, reinstatement, "
        "renewal fees) were individually re-checked against their source within the last "
        "{threshold_days} days — {methodology_link}."
    ),
    "pricing.methodology_link_text": "see exactly how we verify every deadline",
    "pricing.h2_included": "What's actually included, free vs. paid",
    "pricing.included_intro": (
        "Every paid tier (Essentials through Enterprise) has the identical feature set, priced "
        "only by staff count."
    ),
    "pricing.table_caption": "Free vs. paid firm plan feature comparison",
    "pricing.table_th_feature": "Feature",
    "pricing.table_th_free": "Free",
    "pricing.table_th_paid": "Paid (any tier)",
    "pricing.row_roster": "Roster &amp; staff license tracking",
    "pricing.row_roster_free": "Up to 3 staff",
    # ShopLab cold-read (2026-08-20, orchestrator-approved): the column header
    # says "Paid (any tier)" but this cell named only the top tier's cap --
    # read literally, it promises an Essentials buyer 35 seats. Show the real
    # per-tier range instead of one figure that only applies to Enterprise.
    "pricing.row_roster_paid": "From 5 to 35 staff, depending on tier (see plans above)",
    "pricing.row_calendar": "Calendar view",
    "pricing.row_cpe": "CPE-hour tracking",
    "pricing.row_compliance": '<a href="/compliance-reports/">Compliance Summary &amp; audit trail export</a>',
    "pricing.row_email_reminders": "Email renewal reminders (per staff)",
    # P6 (ValueLab pricing addendum, ruled 2026-08-20): the /for-firms/
    # showcase's Roster tab screenshot leads with coverage %, roster-status
    # breakdown, due-soon count, and at-risk ranking -- all four genuinely
    # paid-gated (drRenderStats()/drRenderAtRisk()'s own
    # drDashboardSynthesisIncluded check, roadmap #151 Phase 4) -- but none
    # of them appeared on this table, which itself calls itself "the real,
    # code-verified breakdown." A prospect sees real data in the showcase,
    # signs up free, hits two upsell placeholders where the screenshot had
    # numbers, then checks pricing and doesn't find what they were shown.
    "pricing.row_coverage_overview": "Coverage overview: coverage %, status breakdown, due-soon count, at-risk ranking",
    # P7 (ValueLab pricing addendum, ruled 2026-08-20): runAdminDigestAlertPass()
    # is a real paid feature (same hasValueLineAccess() gate as Slack/Teams/
    # SMS, worker/src/scheduler.ts), invisible on this table -- the
    # per-staff reminders row above reads as if email is fully covered
    # free, hiding that the firm-wide summary to the admin is paid-only.
    "pricing.row_admin_digest": "Daily firm-wide digest to the admin",
    "pricing.row_ppc": "Individual Practice Privilege Check",
    "pricing.row_slack_teams": "Slack &amp; Teams deadline alerts",
    "pricing.row_documents": "Document storage (2MB/file, 50MB/firm)",
    "pricing.row_invite": "Invite teammates to sign in",
    "pricing.row_invite_free": "Just you",
    "pricing.row_map": "Multistate Map view",
    "pricing.row_map_free": "Free for one person*",
    "pricing.row_firm_reg_check": "Firm-level registration check",
    "pricing.row_referral": "Refer firms: 10% off per referral, up to 100% (off your next month or next year, matching your plan)",
    "pricing.cell_yes": "Yes",
    "pricing.cell_no": "No",
    "pricing.footnote_solo": (
        "* Free for one person. Paid plans start when you invite a teammate — inviting someone "
        "is itself a paid-tier feature, so a genuinely one-person account gets the Map and the "
        "firm-level registration check free too."
    ),
    "pricing.footnote_trial": (
        "New firm accounts get the paid features free for 14 days from signup, with no card "
        "required (referral rewards start when you subscribe). After day 14 the account is on the "
        "Free column unless you subscribe. If the roster is above 3 staff, you choose which 3 stay "
        "active; the rest are paused, never deleted, and restored instantly if you subscribe."
    ),
    "pricing.h2_plans": "Plans",
    "pricing.plans_intro": (
        "If you don't already have a firm account, the buttons below start free signup first; if "
        "you're already signed in, they go straight to checkout for that tier."
    ),
    "pricing.card_individual_title": "Individual",
    "pricing.card_individual_price": "Free",
    "pricing.card_individual_detail": (
        "Roster, Calendar, CPE-hour tracking and Practice Privilege Check — free for any firm, "
        "up to 3 staff, no card and no time limit. A one-person account also gets the Multistate "
        "Map and firm-level registration check free (see the comparison table below). Just want "
        "free renewal reminders? {signup_link} — no account needed."
    ),
    "pricing.signup_free_link_text": "Sign up free",
    "pricing.card_individual_cta": "Create a free account",
    "pricing.staff_up_to": "Up to {n} staff.",
    "pricing.card_more_title": "More than 35 staff?",
    # BILL-52 (AuditLab, MEDIUM, 2026-10-25): the PR6 per-seat wording
    # ("plus the per-seat rate for each seat above 35") was unreachable -- every
    # tier caps at 35 and the add gate refuses the 36th person, so checkout
    # never mints the add-on for a new firm. Says what the product does.
    # BILL-53 (AuditLab, LOW, 2026-10-25): a roster already above 35 can still
    # check out Enterprise and gets the per-seat add-on for the rest, so the
    # denial is scoped to starting a plan, not to every firm.
    # COPY-31 (AuditLab, MEDIUM, 2026-10-26): the per-seat add-on is minted
    # only by first checkout; a subscribed firm gets already_subscribed and the
    # portal has nothing above 35, so the per-seat line is scoped to pre-subscribe.
    # BILL-55 (AuditLab, MEDIUM, 2026-10-26): no gated path lets a roster pass
    # 35 people (decideAdd + the BILL-54 PATCH gate), so the pre-subscribe
    # per-seat case is unreachable too. Dropped; matches /for-firms/ and the 402.
    "pricing.card_more_detail": "Enterprise covers up to 35 staff, the largest plan you can start online. Need more than 35? {contact_link} and we'll work out what fits.",
    "pricing.contact_us_link_text": "Contact us",
    "pricing.includes_coverage_overview": "Coverage overview &amp; at-risk ranking",
    "pricing.includes_admin_digest": "Daily firm-wide digest",
    "pricing.includes_map": "Multistate Map",
    "pricing.includes_firm_reg": "Firm-level registration check",
    "pricing.includes_slack_teams": "Slack &amp; Teams alerts",
    "pricing.includes_documents": "Document storage",
    "pricing.includes_referral": "Referral discounts, up to 100% off your next month or year",
    "pricing.faq_heading": "Questions firms ask before signing up",
    "pricing.backlink_body": "See exactly {methodology_link2}, or read the {breakdown_link}.",
    "pricing.methodology_link_text2": "how we verify every deadline",
    "pricing.breakdown_link_text": "full firm-tier breakdown",
    "pricing.title": "Pricing",
    "pricing.meta_description": (
        "Pricing: free individual reminders, free Practice Privilege Check, "
        "firm plans from $199 to $549/year (or $20 to $55/month) — identical features at every tier."
    ),
    # Shared FAQ (_FIRM_FAQ), used by /pricing/ and /for-firms/. Indexed
    # 1-8 in source order rather than named -- 8 distinct Q&As with no
    # natural short slug.
    "faq.firm.1_q": "Is the license status actually verified, or just self-reported?",
    "faq.firm.1_a": (
        "The renewal DATES are verified the same rigorous way every free page on this site is: "
        "sourced to the codified statute or rule where we could confirm it, and clearly labelled "
        "where we could only confirm it against the board's own page, cited and rechecked on our "
        "freshness cadence — {methodology_link}. What this is <em>not</em> is a recurring human "
        "lookup of each staff member's individual license status — there's no manual check-in "
        "against the state board or CPAverify.org on your behalf. Signup itself is self-serve: "
        "your admin adds the roster directly, and reminders start right away for each person — "
        "no confirmation step to wait on. Each staff member still gets one transparent email the "
        "moment they're added, naming your firm and with an equally prominent one-click opt-out. "
        "If someone opts out, you'll see it in your activity log."
    ),
    "faq.firm.2_q": "What if my staff are licensed in a birth-month or “bring your own date” state?",
    "faq.firm.2_a": (
        "Still tracked the same way it works on the free tier: that staff member enters their own "
        "birth month or license expiration date once, and it shows up on your roster view like "
        "everyone else's."
    ),
    "faq.firm.3_q": "Can I cancel anytime?",
    "faq.firm.3_a": (
        "Yes. Roster, calendar, CPE Hours, and individual Practice Privilege Check are free with "
        "no card required and no time limit. If you upgrade for the map and firm-level "
        "registration check, you can cancel that subscription at any point — there's no "
        "contract to get out of, and your account just drops back to the free tier at the end of "
        # ValueLab pricing-ruling smaller item (approved, batched 2026-08-21):
        # Terms §5 and the dashboard's own cancel-confirm dialog both state
        # no-refund-on-cancel; this public FAQ answer didn't, reading as if
        # cancelling mid-period might return money. Matches the same
        # "no refund ... but you keep full access through the end of the
        # period" phrasing already used in Terms/the confirm dialog.
        "the period you already paid for, with no refund for the time already paid — you keep "
        "full access through the end of that period either way."
    ),
    "faq.firm.4_q": "Which plan should my firm pick?",
    "faq.firm.4_a": (
        "Whatever covers your current staff count — Essentials (up to 5), Growth (up to "
        "10), Professional (up to 20), or Enterprise (up to 35). Every tier has the exact same "
        "feature set (Roster, Calendar, Map, CPE Hours, Practice Privilege Check); the only thing "
        "that changes between tiers is how many staff it covers, never what it can do. "
        # AuditLab COPY-30 (2026-10-25): self-serve plan change went live
        # 2026-10-11 (Manage billing -> Stripe Customer Portal, partner-only),
        # so the 2026-08-20 "no self-serve tier change yet" stance is retired.
        # The portal withholds switching for some firms (per-seat add-on, a
        # live founding trial, the staleness guard), hence the email fallback.
        "Outgrow your tier later? A partner on the firm can switch to a larger plan from "
        "Manage billing on the dashboard's Account tab; if a larger plan isn't offered there, "
        "email us and we'll sort it out."
    ),
    "faq.firm.5_q": "I'm a single CPA, not a firm — is this for me?",
    "faq.firm.5_a": (
        "This page is about the firm tier: a roster for whoever is tracking multiple staff CPAs. "
        "If you're only tracking your own license, the free individual reminders on our homepage "
        "already cover that at no cost, unchanged. CPE-hour tracking and Practice Privilege Check "
        "are also free for a solo CPA — {create_account_link} to use them."
    ),
    "faq.firm.create_account_link_text": "create a free account",
    "faq.firm.6_q": "Do you track CPE hours too?",
    "faq.firm.6_a": (
        "Yes — the dashboard has a CPE Hours tab where your firm can log completed hours "
        "against each state's own requirement. That log is your own self-reported record, not "
        "independently verified, and we keep it clearly labeled and separate from the sourced "
        "renewal dates — we won't blur the two. "
        "Licensed in more than one state? Add one line per state; each line is tracked against "
        "that state's own requirement, and lines for the same person (matched by email address) count once toward "
        "your plan's staff limit. Each person can have up to 10 state lines; your firm's own permits "
        "and registrations are separate lines that use no staff seat, up to 2 per state and 25 in total."
    ),
    "faq.firm.7_q": "How is this different from my staff just signing up for free individually?",
    "faq.firm.7_a": (
        "Nothing stops them from doing that today, and it's not a bad idea either way. What the "
        "firm tier adds is the view your admin doesn't get from 20 separate free sign-ups: one "
        "roster, one place to see who's current and who's at risk, plus the firm's own "
        "registration — not 20 inboxes to hope someone's watching."
    ),
    "faq.firm.8_q": "Who actually sets up my staff — your team, or us?",
    "faq.firm.8_a": (
        "You do, directly, through the self-serve dashboard: your admin adds each staff member's "
        "name, email, state, and license type, and their reminders start right away — no "
        "waiting on them to confirm anything, so your firm's coverage never has a silent gap. "
        "There's no concierge onboarding where we collect a roster by email and enter it "
        "for you. Each staff member gets one transparent email the moment they're added, naming "
        "your firm and with an equally prominent one-click opt-out, so nobody is tracked silently."
    ),
}


# ---------------------------------------------------------------------------
# ES: Spanish translations. Populated by scripts/es_translation_review.py
# whenever a build detects a missing/stale entry -- never hand-authored
# directly with reviewed=True. See module docstring.
# ---------------------------------------------------------------------------
ES: dict[str, dict] = {
    'calc.answer_no_public_rule': {
        "text": 'Ninguna norma pública vincula esto a un dato conocible — la calculadora lo indica, con honestidad',
        "en_hash": '49eb743f39981a7a',
        "reviewed": True,
    },
    'calc.answer_personal_fact': {
        "text": 'Depende de un dato personal — la calculadora de arriba lo solicita y luego responde',
        "en_hash": '31e2ae91c4be6298',
        "reviewed": True,
    },
    'calc.backlink_all_states': {
        "text": '← Volver a todos los estados',
        "en_hash": '116d8e518da7ebb0',
        "reviewed": True,
    },
    'calc.faq1_a': {
        "text": 'Sí — buscar su propia fecha de renovación nunca ha requerido una cuenta, un registro, ni una tarifa. El nivel de pago es para firmas que siguen el personal de toda una plantilla en un solo lugar, no para una persona que verifica su propia fecha.',
        "en_hash": 'ab900442e2387f17',
        "reviewed": True,
    },
    'calc.faq1_q': {
        "text": '¿Esto es realmente gratis?',
        "en_hash": 'df21c6549d0935a3',
        "reviewed": True,
    },
    'calc.faq2_a': {
        "text": 'Algunos ciclos de renovación estatales genuinamente dependen de un dato que no tenemos — cuándo fue licenciado originalmente, a qué grupo de cohorte pertenece, o algo similar — y la propia junta estatal no publica una consulta de la que podamos calcularlo. En lugar de adivinar, la página lo indica y le permite ingresar su propia fecha conocida para hacer seguimiento desde ahí.',
        "en_hash": 'bb073024d8770490',
        "reviewed": True,
    },
    'calc.faq2_q': {
        "text": '¿Por qué mi estado dice “ingrese su fecha” en lugar de mostrar una?',
        "en_hash": '07d33e7c54453c2c',
        "reviewed": True,
    },
    'calc.faq3_a': {
        "text": 'Cada fecha remite ya sea a la propia página publicada de la junta estatal o al estatuto/norma codificada real, con la fecha en que la confirmamos por última vez mostrada en la propia página de ese estado — vea {verification_writeup_link}.',
        "en_hash": '63e2990771bd11e3',
        "reviewed": True,
    },
    'calc.faq3_q': {
        "text": '¿Cómo sabe que estas fechas son correctas?',
        "en_hash": '8405553712638fcb',
        "reviewed": True,
    },
    'calc.h1': {
        "text": 'Calculadora de fechas límite de renovación de licencia de CPA',
        "en_hash": 'b1c1ab4dab8f32f5',
        "reviewed": True,
    },
    'calc.h2_faq': {
        "text": 'Preguntas frecuentes',
        "en_hash": '980ef788f803854f',
        "reviewed": True,
    },
    'calc.h2_how_it_works': {
        "text": 'Cómo funciona esto realmente',
        "en_hash": '4603d55140703671',
        "reviewed": True,
    },
    'calc.h2_what_calculated_looks_like': {
        "text": 'Cómo se ve “calculado” realmente, estado por estado',
        "en_hash": 'b06fa2fbff4dbe66',
        "reviewed": True,
    },
    'calc.how_it_works_body': {
        "text": 'Seleccionar su estado arriba lo busca en el mismo conjunto de datos exacto del que se renderiza la propia página de ese estado — no hay una segunda fórmula distinta ejecutándose aquí. La mayoría de los estados se resuelven a una sola fecha de inmediato. Los estados cuya norma depende de un dato personal — su mes de nacimiento, o a qué grupo de cohorte pertenece su licencia — solicitan ese dato antes de responder, usando exactamente los mismos datos que la norma de ese estado realmente usa. De cualquier forma, la fecha que obtiene nunca se estima ni se adivina — si no podemos confirmarla contra una fuente primaria, la calculadora lo indica en lugar de mostrar un número, igual que la tabla de arriba muestra para un estado donde eso es cierto. Un estado cuyo registro de firma se renueva en un calendario separado al de la licencia individual recibe un enlace a la página completa de ese estado — la calculadora de arriba responde únicamente la pregunta individual.',
        "en_hash": '139dafec7bb4f60d',
        "reviewed": True,
    },
    'calc.intro': {
        "text": 'Seleccione su estado abajo — y su mes de nacimiento o grupo de cohorte, si la norma de su estado lo requiere — para ver su fecha límite de renovación exacta aquí mismo. Gratis, sin registro, sin cuenta requerida. Cada fecha tiene fuente — al estatuto o norma codificada de la junta de su estado donde pudimos confirmarla contra la ley primaria, y claramente etiquetada donde solo pudimos confirmarla contra la propia página de la junta — {methodology_link}.',
        "en_hash": '55921c7a6a3d7ff1',
        "reviewed": True,
    },
    'calc.meta_description': {
        # SRC-16 (AuditLab 2026-10-09): replaces the 210-char draft with a
        # 157-char rewrite that keeps the three-category citation clause.
        # Reviewed: AuditLab PASS (Candidate A).
        "text": 'Calculadora gratuita de renovación de CPA — elija su estado y obtenga su fecha exacta, con cita del estatuto, la norma o la página de la junta. Sin registro.',
        "en_hash": 'b62bd9da04a4a3ec',
        "reviewed": True,
    },
    'calc.overview_link_text': {
        "text": 'resumen para firmas',
        "en_hash": '3f6d1dad09e67630',
        "reviewed": True,
    },
    'calc.prefer_browse': {
        "text": '¿Prefiere explorar la página completa de un estado directamente en lugar de usar la calculadora de arriba?',
        "en_hash": '453e76467d6d1aad',
        "reviewed": True,
    },
    'calc.see_exactly_how': {
        "text": 'vea exactamente cómo',
        "en_hash": '912c78494d4afd19',
        "reviewed": True,
    },
    'calc.table_th_cycle_type': {
        "text": 'Tipo de ciclo',
        "en_hash": '8d02fef87a0950bd',
        "reviewed": True,
    },
    'calc.table_th_returns': {
        "text": 'Qué devuelve la herramienta',
        "en_hash": '4e1c758fdd2493bd',
        "reviewed": True,
    },
    'calc.table_th_state': {
        "text": 'Estado',
        "en_hash": 'a3b50c476732c740',
        "reviewed": True,
    },
    'calc.title': {
        "text": 'Calculadora de renovación de licencia CPA',
        "en_hash": 'b1c1ab4dab8f32f5',
        "reviewed": True,
    },
    'calc.tracking_bold': {
        "text": '¿Está siguiendo al personal de toda una firma, no solo su propia licencia?',
        "en_hash": '1280dd36fae3291a',
        "reviewed": True,
    },
    'calc.tracking_rest': {
        "text": 'Vea el {overview_link} — los mismos datos de fechas límite gratuitos y con fuente en una sola vista de plantilla en lugar de un estado a la vez.',
        "en_hash": '509ff165cdf7906e',
        "reviewed": True,
    },
    'calc.trust_determined': {
        "text": 'donde calculamos su fecha exacta',
        "en_hash": '3c86e69a480dbb56',
        "reviewed": True,
    },
    'calc.trust_jurisdictions': {
        "text": 'jurisdicciones cubiertas',
        "en_hash": '56604af188f2bf67',
        "reviewed": True,
    },
    'calc.trust_records': {
        "text": 'registros con fecha en los conjuntos de datos de este sitio (fechas límite de renovación, horas de CPE, reincorporación, tarifas de renovación) re-verificados en los últimos 30 días',
        "en_hash": '5386639e7cdd5970',
        "reviewed": False,
    },
    'calc.verification_writeup_link_text': {
        "text": 'el resumen completo de verificación',
        "en_hash": 'cfc9493e65b8e6a6',
        "reviewed": True,
    },
    'calc.what_calculated_intro': {
        "text": 'Esto no es una sola fórmula — cada estado renueva según su propia norma, y esas normas toman formas genuinamente distintas. Algunos ejemplos reales, directamente del mismo conjunto de datos del que se nutre la calculadora de arriba:',
        "en_hash": 'cf483167dc0e6e13',
        "reviewed": True,
    },
    'contact.email_body': {
        "text": 'Leemos cada mensaje y normalmente respondemos en un par de días hábiles. Este es un proyecto pequeño e independiente — hay una persona real del otro lado, no una cola de soporte. Nuestra dirección también se publica de forma legible por máquina en {security_txt_link} según {rfc_link}.',
        "en_hash": '9fd5d0895e10799f',
        "reviewed": True,
    },
    'contact.h1': {
        "text": 'Contacto',
        "en_hash": '2b5c3d26721ae9c3',
        "reviewed": True,
    },
    'contact.h2_email_us': {
        "text": 'Escríbanos',
        "en_hash": 'd9172c43bec620b2',
        "reviewed": True,
    },
    'contact.h2_mailing_address': {
        "text": 'Dirección postal',
        "en_hash": '88a4628a726eaebd',
        "reviewed": True,
    },
    'contact.h2_stop_reminders': {
        "text": 'Detenga sus recordatorios',
        "en_hash": '98d3ecda22a1df9f',
        "reviewed": True,
    },
    'contact.h2_wrong_date': {
        "text": '¿Encontró una fecha incorrecta?',
        "en_hash": 'acb84418844b64c5',
        "reviewed": True,
    },
    'contact.intro': {
        "text": 'Preguntas, una corrección a una fecha límite, o cualquier otra cosa — nos gustaría saber de usted.',
        "en_hash": 'ed62b5488c50ce0e',
        "reviewed": True,
    },
    'contact.meta_description': {
        "text": 'Contacte a Deadline-Radar — preguntas, correcciones de fechas límite, o ayuda con sus recordatorios de renovación de licencia de CPA. Leemos cada mensaje.',
        "en_hash": '60a7cfd676f97646',
        "reviewed": True,
    },
    'contact.stop_reminders_body': {
        "text": 'La forma más rápida de detener los recordatorios es el enlace de baja de un clic al final de cualquier correo que enviemos — es instantáneo y permanente. También puede escribirnos por correo.',
        "en_hash": 'ab859a2b560d7439',
        "reviewed": True,
    },
    'contact.wrong_date_body': {
        "text": 'Las fechas límite se recopilan de fuentes oficiales de las juntas estatales y trabajamos arduamente para mantenerlas actualizadas, pero las normas cambian. Si una fecha parece incorrecta, envíenos por correo el estado y lo que está viendo, y la verificaremos contra la fuente y la corregiremos rápidamente. Siempre confirme su fecha límite exacta con la junta de su estado antes de confiar en ella.',
        "en_hash": 'cd5fa637791b647b',
        "reviewed": True,
    },
    'footer.disclaimer_bold': {
        "text": '{site_name} es un servicio independiente de recordatorios operado por {brand_name}.',
        "en_hash": '2cb7ec3bc67c1631',
        "reviewed": True,
    },
    'footer.disclaimer_rest': {
        "text": 'No está afiliado, respaldado ni conectado con NASBA, el AICPA, ni ninguna junta estatal de contabilidad. Las fechas de renovación se recopilan de fuentes públicas únicamente con fines informativos — no constituyen asesoría legal, fiscal ni profesional. Confirme siempre su fecha exacta de renovación con la junta de su estado o en su licencia.',
        "en_hash": '1581cbb44a7d7d92',
        "reviewed": True,
    },
    'footer.heading_company': {
        "text": 'Empresa',
        "en_hash": 'de4743c879734dc3',
        "reviewed": True,
    },
    'footer.heading_data_method': {
        "text": 'Datos y método',
        "en_hash": '6c976c3bde81f379',
        "reviewed": True,
    },
    'footer.heading_product': {
        "text": 'Producto',
        "en_hash": 'fb9ef894175c3274',
        "reviewed": True,
    },
    'footer.link_about': {
        # AuditLab 2026-10-22T00:52:47Z (es_review_batch1): APPROVE, ship-
        # coupled to generate.py's site_footer() also adding a visible
        # EN-destination tag on this link, since /about/ has no es page yet.
        "text": 'Acerca de',
        "en_hash": '4efca0d10c5feb8e',
        "reviewed": True,
    },
    'footer.link_all_jurisdictions': {
        "text": 'Las {count} jurisdicciones',
        "en_hash": '4afea1a2f22c51d4',
        "reviewed": True,
    },
    'footer.link_contact': {
        "text": 'Contacto',
        "en_hash": '2b5c3d26721ae9c3',
        "reviewed": True,
    },
    'footer.link_cpe_vs_license': {
        "text": 'CPE vs. renovación de licencia',
        "en_hash": '6fa789f39949b20c',
        "reviewed": True,
    },
    'footer.link_deadline_calculator': {
        "text": 'Calculadora de fechas límite',
        "en_hash": 'b9e66e3732c7ce9b',
        "reviewed": True,
    },
    'footer.link_mobility_rule_changes': {
        "text": 'Cambios en normas de movilidad',
        "en_hash": 'ac8fe0fb626fff29',
        "reviewed": True,
    },
    'footer.link_multi_state_firms': {
        "text": 'Firmas multiestatales',
        "en_hash": '9c3eae1fe07e81cd',
        "reviewed": True,
    },
    'footer.link_practice_privilege_check': {
        "text": 'Verificación de privilegio de práctica',
        "en_hash": '45adea10b3b47ad0',
        "reviewed": True,
    },
    'footer.link_pricing': {
        "text": 'Precios',
        "en_hash": 'dfe95783edfef791',
        "reviewed": True,
    },
    'footer.link_privacy': {
        "text": 'Privacidad',
        "en_hash": '54a57c3147c49f33',
        "reviewed": True,
    },
    'footer.link_security': {
        "text": 'Seguridad',
        "en_hash": '8f6fb4eb7f42c0e2',
        "reviewed": True,
    },
    'footer.link_status': {
        "text": 'Estado del servicio',
        "en_hash": '920e413c7d411b61',
        "reviewed": True,
    },
    'footer.link_terms': {
        "text": 'Términos',
        "en_hash": 'ede5489964834a51',
        "reviewed": True,
    },
    'footer.trust_chip': {
        "text": 'Sin rastreadores de anuncios ni redes sociales. Solo análisis sin cookies.',
        "en_hash": '961b66a3099b5455',
        "reviewed": True,
    },
    'methodology.backlink_changelog': {
        "text": 'Vea exactamente qué cambió y cuándo →',
        "en_hash": 'e457cf09346c5ba0',
        "reviewed": True,
    },
    'methodology.backlink_contact': {
        "text": '¿Encontró algo que parece incorrecto? Avísenos →',
        "en_hash": '492504b0149705b1',
        "reviewed": True,
    },
    'methodology.dont_verify_body': {
        "text": 'La finalización de horas de CPE es autoinformada dondequiera que este sitio o su nivel para firmas la mencione — lo etiquetamos claramente y nunca le damos el mismo tratamiento de “Verificado” que a una fecha de renovación con fuente. Tampoco verificamos de forma independiente los futuros cambios de política de un estado; si un estado propone una nueva norma que aún no ha entrado en vigor, esperamos a que se convierta en la norma vigente real antes de citarla.',
        "en_hash": 'ab7f4ec75f1ad0ec',
        "reviewed": True,
    },
    'methodology.fall_short_body': {
        "text": 'Algunas fuentes son genuinamente más difíciles de verificar por medios automatizados — un puñado de citas remiten a documentos PDF o páginas renderizadas con JavaScript que nuestras herramientas no pueden extraer automáticamente. Cuando ese es el caso, esas citas fueron confirmadas individualmente a mano en el momento en que se publicaron; revelamos la limitación de la herramienta en lugar de fingir que una verificación más sencilla la cubre. Si una norma cambia entre nuestras verificaciones, use el enlace de contacto abajo para señalarlo y la volveremos a verificar y corregir rápidamente. Los artículos aquí se atribuyen a Moose & Raven LLC, la empresa detrás de Deadline-Radar, en lugar de a un autor individual con nombre propio — la construcción honesta para un equipo de este tamaño, y cada dato se sostiene en su propia fuente citada sin importar la firma.',
        "en_hash": '8a47387ad08b6ab0',
        "reviewed": True,
    },
    'methodology.freshness_stat': {
        "text": 'registros con fecha en los conjuntos de datos de este sitio (fechas límite de renovación, horas de CPE, reincorporación, tarifas de renovación) fueron verificados individualmente contra su fuente en los últimos {threshold_days} días, a la fecha de la última compilación de esta página ({build_date}). La línea “Última verificación” de cada página estatal muestra la fecha propia de esa cita específica — es el mismo dato, agregado para todo el sitio.',
        "en_hash": '1de9b744025d0a3f',
        "reviewed": True,
    },
    'methodology.h2_fall_short': {
        "text": 'Dónde esto todavía puede quedar corto, con honestidad',
        "en_hash": '511cd5c7e3cd89a5',
        "reviewed": True,
    },
    'methodology.h2_last_verified': {
        "text": 'Qué significa “Última verificación”',
        "en_hash": '002ed2b97d962a07',
        "reviewed": True,
    },
    'methodology.h2_see_for_yourself': {
        "text": 'Compruébelo usted mismo',
        "en_hash": 'ccc55589a3e05697',
        "reviewed": True,
    },
    'methodology.h2_two_source_rule': {
        "text": 'La regla de las dos fuentes',
        "en_hash": 'cfd64ad44ece3baa',
        "reviewed": True,
    },
    'methodology.h2_verified_badge': {
        "text": 'Qué significa la insignia “Verificado”',
        "en_hash": '7724212222d01f1a',
        "reviewed": True,
    },
    'methodology.h2_what_we_dont_verify': {
        "text": 'Qué no verificamos de esta manera',
        "en_hash": '1e566dc227fc25f3',
        "reviewed": True,
    },
    'methodology.intro': {
        "text": 'Los CPA están capacitados para ser escépticos ante fuentes no verificadas — así que esto es exactamente cómo se obtienen, verifican y mantienen actualizadas las fechas de este sitio. Nada de lo siguiente es aspiracional; describe el estándar real ya aplicado a cada página estatal.',
        "en_hash": '0394c54d6bb494c9',
        "reviewed": True,
    },
    'methodology.last_verified_followup': {
        "text": 'Cuando aparece cualquiera de los dos, verificamos manualmente antes de cambiar algo que un visitante vea — una alerta automatizada nunca reescribe silenciosamente una fecha publicada por sí sola.',
        "en_hash": '5fe6baed2ba11d6e',
        "reviewed": True,
    },
    'methodology.last_verified_intro': {
        "text": 'La fecha que se muestra en la línea de confianza de cada estado es la última vez que verificamos directamente la cita de ese estado contra el texto de la fuente primaria — no solo releímos nuestras propias notas al respecto. Periódicamente volvemos a ejecutar una verificación automatizada en cada fuente citada, buscando dos cosas:',
        "en_hash": '337ad3176c49636d',
        "reviewed": True,
    },
    'methodology.last_verified_item1': {
        "text": 'un enlace roto o redirigido, o',
        "en_hash": 'ca9718bfa0078007',
        "reviewed": True,
    },
    'methodology.last_verified_item2': {
        "text": 'cualquier indicio de que la norma subyacente haya sido modificada desde entonces.',
        "en_hash": 'ffd2697ad74d2e49',
        "reviewed": True,
    },
    'methodology.meta_description': {
        "text": 'El estándar de verificación de Deadline-Radar: cada fecha de renovación remite a la página de la junta estatal y a la norma codificada — nunca una suposición.',
        "en_hash": '788cc76b0aa7c29c',
        "reviewed": True,
    },
    'methodology.see_for_yourself_body': {
        "text": 'Elija cualquier página estatal y busque la línea “Source of record” debajo de su fecha — la cita y el enlace “leer la norma” llevan al texto legal primario, no a un resumen. Ese es el mismo estándar detrás de cada fecha en este sitio.',
        "en_hash": 'a8ceea4d0a4bee54',
        "reviewed": True,
    },
    'methodology.title': {
        "text": 'Cómo verificamos cada fecha límite',
        "en_hash": '9c23eb5b14479668',
        "reviewed": True,
    },
    'methodology.two_source_fallback': {
        "text": 'Si no podemos encontrar o confirmar la segunda fuente, la fecha no se publica como un hecho confirmado. En su lugar, la página lo indica claramente y le remite a la junta estatal oficial para que determine su fecha límite exacta — no adivinamos, interpolamos ni inferimos una fecha que no podamos respaldar con la ley primaria.',
        "en_hash": 'db14e52be12a036f',
        "reviewed": True,
    },
    'methodology.two_source_intro': {
        "text": 'Toda fecha en este sitio debe remitirse a dos elementos independientes antes de publicarse:',
        "en_hash": '7500c6c8e1eed63c',
        "reviewed": True,
    },
    'methodology.two_source_item1': {
        "text": '<strong>La propia página de la junta estatal</strong> — la fuente en lenguaje sencillo que la mayoría de las personas encontraría primero.',
        "en_hash": 'ae4f97ae377fc2a7',
        "reviewed": True,
    },
    'methodology.two_source_item2': {
        "text": '<strong>El estatuto codificado o la norma administrativa real</strong> de la que se deriva el requisito de la junta — no un resumen, sino el texto legal primario mismo. Esa cita y un enlace directo a ella se muestran debajo de cada fecha verificada en este sitio, con la etiqueta “Source of record”.',
        "en_hash": 'fad15d7df2de1f83',
        "reviewed": True,
    },
    'methodology.verified_badge_body': {
        "text": 'Un recuadro destacado muestra una insignia <strong>Verificado</strong> solo cuando esa fecha específica tiene una cita real a la ley codificada que la respalda, verificada de la manera descrita arriba. Un registro sin ella nunca muestra la insignia — no existe un estado intermedio donde una fecha parezca confirmada sin estarlo.',
        "en_hash": '9133faed51b64a34',
        "reviewed": True,
    },
    'msf.backlink_all_states': {
        "text": '← Volver a todos los estados',
        "en_hash": '116d8e518da7ebb0',
        "reviewed": True,
    },
    'msf.feed_link_text': {
        "text": 'Vea el feed público completo',
        "en_hash": 'a6b7dfba7a46fe57',
        "reviewed": True,
    },
    'msf.h1': {
        "text": '¿Dirige una firma de CPA multiestatal? Aquí está el panorama completo.',
        "en_hash": '995b35a40657fce4',
        "reviewed": True,
    },
    'msf.h2_map': {
        "text": '1. Mapa — vea en qué estados puede ejercer todo su equipo',
        "en_hash": 'd9ca0e416bf72b16',
        "reviewed": True,
    },
    'msf.h2_ppc': {
        "text": '2. Verificación de privilegio de práctica — confirme antes de que el personal asuma trabajo fuera del estado',
        "en_hash": '1b970c435bfcdf55',
        "reviewed": True,
    },
    'msf.h2_rule_changes': {
        "text": '3. Cambios de normas — un feed continuo, no una verificación única',
        "en_hash": '24fab8ed43ef3163',
        "reviewed": True,
    },
    'msf.intro': {
        "text": 'Una firma con personal licenciado o ejerciendo en más de un estado tiene un problema genuinamente distinto al de una firma de un solo estado: saber dónde puede trabajar legalmente cada persona, detectarlo antes de que una norma cambie bajo sus pies, y mantener una cita detrás de cada respuesta. Tres piezas de este sitio trabajan juntas exactamente para eso.',
        "en_hash": 'df89adddee99fb48',
        "reviewed": True,
    },
    'msf.map_body': {
        "text": 'Un mapa codificado por colores que muestra exactamente en qué estados su equipo puede ejercer hoy sin una licencia local, más una verificación de registro a nivel de firma para trabajo de certificación donde su propia firma (no solo el CPA individual) necesita registrarse. Parte de un plan de firma de pago — {pricing_link}.',
        "en_hash": 'bf6e29d476fad8f9',
        "reviewed": True,
    },
    'msf.meta_description': {
        "text": 'Para firmas de CPA en varios estados: mapa de cobertura, Verificación de privilegio de práctica gratuita y feed de cambios de normas — con fuentes y citas.',
        "en_hash": '48bb583c964fff69',
        "reviewed": True,
    },
    'msf.new_here_bold': {
        "text": '¿Nuevo en Deadline-Radar?',
        "en_hash": 'a377ab1ca0457c97',
        "reviewed": True,
    },
    'msf.new_here_rest': {
        "text": 'Vea el {overview_link} para precios, el conjunto completo de funciones, y cómo el seguimiento de fechas de renovación encaja junto con estas tres.',
        "en_hash": 'c992446c0c6f005c',
        "reviewed": True,
    },
    'msf.overview_link_text': {
        "text": 'resumen completo para firmas',
        "en_hash": '36f6173bffc454fd',
        "reviewed": True,
    },
    'msf.ppc_body': {
        "text": 'Antes de que un CPA de su equipo asuma trabajo en un estado donde no tiene licencia local, ejecute la verificación: tipo de servicio, estado de origen, estado de destino, y la respuesta llega con la norma y la cita que la respaldan — nunca una suposición. {ppc_link}.',
        "en_hash": 'e92e4be0a06603ba',
        "reviewed": True,
    },
    'msf.ppc_link_text': {
        "text": 'Gratis para cualquier cuenta, sin plan de pago requerido',
        "en_hash": 'e3e7ab62fcddaa42',
        "reviewed": True,
    },
    'msf.rule_changes_body': {
        "text": 'Un feed continuo de cambios confirmados y pendientes a las normas de movilidad interestatal de CPA — privilegios de práctica, requisitos de aviso/tarifa, y registro de firmas — obtenido de la misma manera que cada fecha en este sitio: una cita al estatuto o norma primaria donde pudimos confirmarla, y claramente etiquetado donde solo pudimos confirmarla contra la propia página de la junta, nunca una suposición. El calendario de su propia firma muestra los cambios que realmente afectan a los estados de su equipo. {feed_link}.',
        "en_hash": 'd0fa020ab8c1681b',
        "reviewed": True,
    },
    'msf.see_plans': {
        "text": 'ver planes',
        "en_hash": '70bc8613cdb53212',
        "reviewed": True,
    },
    'msf.title': {
        "text": 'Firmas de CPA multiestatales: movilidad',
        "en_hash": '8fd2d63e653e2e9b',
        "reviewed": True,
    },
    'msf.try_demo': {
        "text": 'Pruebe la demo en vivo →',
        "en_hash": 'f6fe3745b0891272',
        "reviewed": True,
    },
    'nav.browse_states': {
        "text": 'Explorar estados',
        "en_hash": 'a6c6d2388d99558e',
        "reviewed": True,
    },
    'nav.dashboard': {
        "text": 'Panel de control',
        "en_hash": '67b696468610b879',
        "reviewed": True,
    },
    'nav.for_firms': {
        "text": 'Para firmas',
        "en_hash": '2f38ed7d5ac795a2',
        "reviewed": True,
    },
    'nav.get_reminders': {
        "text": 'Recibir recordatorios',
        "en_hash": 'a2aa9444da3e4a2b',
        "reviewed": True,
    },
    'nav.guides': {
        "text": 'Guías',
        "en_hash": '572cd72feb9a84e0',
        "reviewed": True,
    },
    'nav.how_we_verify': {
        "text": 'Cómo verificamos',
        "en_hash": 'f85fa15963b89714',
        "reviewed": True,
    },
    'nav.live_demo': {
        "text": 'Demo en vivo',
        "en_hash": '1841d3c3a6598770',
        "reviewed": True,
    },
    'nav.sign_in': {
        "text": 'Iniciar sesión',
        "en_hash": 'bcc0bcc9140b0c97',
        "reviewed": True,
    },
    'ppc.backlink_all_states': {
        "text": '← Volver a todos los estados',
        "en_hash": '116d8e518da7ebb0',
        "reviewed": True,
    },
    'ppc.callout_bold': {
        "text": 'Informativo, no asesoría legal.',
        "en_hash": '7808cd56162dfcd4',
        "reviewed": True,
    },
    'ppc.callout_rest': {
        "text": 'Las normas de privilegio de práctica cambian, y dependen de hechos que no podemos ver. Le mostramos la norma y de dónde proviene para que pueda verificarla usted mismo — y donde no hemos verificado algo contra una fuente primaria, lo decimos en lugar de adivinar. Confirme con la junta estatal antes de confiar en cualquier respuesta aquí.',
        "en_hash": 'e7572963bd42c96b',
        "reviewed": True,
    },
    'ppc.coverage_body': {
        "text": 'Junto a la pregunta individual de arriba hay una <strong>verificación de registro a nivel de firma</strong> separada — si la FIRMA misma necesita registrarse en algún lugar donde no tiene oficina, incluso cuando el CPA individual está cubierto. Ninguna de las dos verificaciones tiene una respuesta definida para cada jurisdicción y cada servicio. Cuando no la tiene — por ejemplo, la norma está en transición, sus fuentes primarias se contradicen entre sí, la vía de calificación para CPA de otros estados aún no está definida, o no tenemos una norma separada a nivel de firma para el servicio que eligió (todavía no existe ninguna para otros servicios no certificados) — la verificación lo indica en lugar de adivinar; confirme con la junta de ese estado.',
        "en_hash": 'ac79d7d4ad1d50d7',
        "reviewed": True,
    },
    'ppc.free_tier_body': {
        "text": 'La verificación individual es gratuita en todos los niveles, para cualquier cuenta — un registro gratuito es todo lo que se necesita, sin tarjeta, sin plan de pago requerido. {pricing_link}.',
        "en_hash": '5ec420f51ca1d848',
        "reviewed": True,
    },
    'ppc.free_tier_link_text': {
        "text": 'La verificación a nivel de firma y el mapa de cobertura multiestatal son parte de un plan de pago',
        "en_hash": 'eb07b4dd34ea150b',
        "reviewed": True,
    },
    'ppc.full_pricing_link_text': {
        "text": 'los precios completos',
        "en_hash": 'c2504a2f405b4965',
        "reviewed": True,
    },
    'ppc.h1': {
        "text": 'Verificación de privilegio de práctica: ¿puede un CPA trabajar en otro estado sin licencia?',
        "en_hash": '87a0ea2bc82b5a93',
        "reviewed": True,
    },
    'ppc.h2_what_it_does': {
        "text": 'Qué hace realmente Verificación de privilegio de práctica',
        "en_hash": '59cbfffc71e3cc8c',
        "reviewed": True,
    },
    'ppc.item_attest_gap_bold': {
        "text": 'Cuidado con el vacío de certificación:',
        "en_hash": 'c02136f1660a298a',
        "reviewed": True,
    },
    'ppc.item_attest_gap_rest': {
        "text": 'el trabajo de certificación frecuentemente activa un requisito de registro de firma donde el trabajo de impuestos no lo hace — ese es el error de movilidad más común en la práctica real, y esto lo detecta.',
        "en_hash": '2e8f0a2375570ee1',
        "reviewed": True,
    },
    'ppc.item_confirm_bold': {
        "text": 'Lo que necesitará confirmar:',
        "en_hash": 'c06a9eb3db6624ac',
        "reviewed": True,
    },
    'ppc.item_confirm_rest': {
        "text": 'que la licencia esté activa y en regla, y que el CPA cumpla con la equivalencia sustancial (150 horas semestrales, un año de experiencia, el Examen Uniforme de CPA). No podemos verificar ninguno de los dos datos nosotros mismos — la respuesta es tan buena como lo que usted indique, el mismo estándar de honestidad que cada fecha de renovación en este sitio.',
        "en_hash": '3915218c6b09d85b',
        "reviewed": True,
    },
    'ppc.item_pick_service_bold': {
        "text": 'Elija un tipo de servicio:',
        "en_hash": 'c4d4753c649835d7',
        "reviewed": True,
    },
    'ppc.item_pick_service_rest': {
        "text": 'Impuestos; Certificación (auditoría, revisión, u otra certificación); u Otro no certificado (consultoría, asesoría).',
        "en_hash": '6b98fc4b698f9cf2',
        "reviewed": True,
    },
    'ppc.meta_description': {
        "text": 'Qué es el privilegio de práctica de CPA y cómo verificar si un CPA puede atender a un cliente en otro estado sin licencia local — gratis, 55 jurisdicciones.',
        "en_hash": '518674a590e5aa94',
        "reviewed": True,
    },
    'ppc.overview_link_text': {
        "text": 'resumen para firmas',
        "en_hash": '3f6d1dad09e67630',
        "reviewed": True,
    },
    'ppc.run_check': {
        "text": 'Cree una cuenta gratuita y ejecute una verificación →',
        "en_hash": 'ba920255af861584',
        "reviewed": True,
    },
    'ppc.subhead': {
        "text": '¿Puede este CPA prestar este servicio en este estado — y qué tiene que suceder primero? Cada respuesta está vinculada a la norma de la que proviene.',
        "en_hash": '5858df5cb22dc3cd',
        "reviewed": True,
    },
    'ppc.title': {
        "text": 'Verificación de privilegio de práctica',
        "en_hash": '45adea10b3b47ad0',
        "reviewed": True,
    },
    'ppc.tracking_bold': {
        "text": '¿Está siguiendo el personal de toda una firma, no solo una consulta?',
        "en_hash": '6d356959f481b6e4',
        "reviewed": True,
    },
    'ppc.tracking_rest': {
        "text": 'Vea el {overview_link} — Roster, Calendario, seguimiento de CPE, y la Verificación de privilegio de práctica individual también son gratis allí; los niveles de pago agregan el mapa multiestatal y la verificación de registro a nivel de firma. Vea {pricing_link2}.',
        "en_hash": 'b7aa7c7b031dda01',
        "reviewed": True,
    },
    'ppc.what_it_does_intro': {
        "text": 'Una pregunta distinta a las fechas de renovación: ¿puede este CPA prestar este servicio específico en este estado específico ahora mismo, sin una licencia local — y qué tiene que suceder primero?',
        "en_hash": '6d90d13b10200e27',
        "reviewed": True,
    },
    'a11y.skip_to_content': {
        "text": 'Saltar al contenido',
        "en_hash": 'ac576a66d4563e60',
        "reviewed": True,
    },
    'nav.pricing': {
        "text": 'Precios',
        "en_hash": 'dfe95783edfef791',
        "reviewed": True,
    },
    'footer.link_compliance_reports': {
        "text": 'Informes de cumplimiento',
        "en_hash": '02f61b55c9a42b38',
        "reviewed": True,
    },
    'site.tagline': {
        "text": 'Fechas límite de renovación de licencias de CPA por estado — verificadas y actualizadas',
        "en_hash": 'dd3f728722518b7c',
        "reviewed": True,
    },
    'calcw.birth_month': {
        "text": 'Su mes de nacimiento',
        "en_hash": '58b9a896c475fd27',
        "reviewed": True,
    },
    'calcw.birth_year': {
        "text": 'Su año de nacimiento',
        "en_hash": '2800ae8c4ce3d96f',
        "reviewed": True,
    },
    'calcw.cert_date': {
        "text": 'La fecha de su certificado original',
        "en_hash": '4c09d059d9e0bce4',
        "reviewed": True,
    },
    'calcw.cohort_hint': {
        "text": '¿No está seguro de qué grupo le corresponde? Consulte su certificado de licencia o la herramienta de búsqueda de la junta estatal.',
        "en_hash": '1a005d5d565f81ea',
        "reviewed": True,
    },
    'calcw.cohort_label': {
        "text": 'Su grupo de cohorte',
        "en_hash": '4a43d3dd65da4e79',
        "reviewed": True,
    },
    'calcw.cpe_clause': {
        "text": ' (su CPE para ese ciclo vence el {date})',
        "en_hash": '6e90e97d358bb02d',
        "reviewed": True,
    },
    'calcw.cta': {
        "text": 'Vea la página completa de {label} y configure un recordatorio gratuito &rarr;',
        "en_hash": '2c02f990ff16a4f9',
        "reviewed": True,
    },
    'calcw.date_style': {
        "text": '{day} de {month} de {year}|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre',
        "en_hash": '52a3d241adbbf3df',
        "reviewed": True,
    },
    'calcw.firm_different': {
        "text": 'El registro de su firma se renueva en un calendario distinto — vea la página completa de {label} más abajo.',
        "en_hash": '4a925f6becf4bcaf',
        "reviewed": True,
    },
    'calcw.firm_prefix': {
        "text": 'Esta es la fecha de su licencia individual. ',
        "en_hash": '92465a0bd33e8b64',
        "reviewed": True,
    },
    'calcw.firm_separate': {
        "text": 'El registro de una firma es un trámite aparte de las licencias individuales — vea la página completa de {label} más abajo para conocer los detalles de la firma.',
        "en_hash": 'e318f8a6c7d344a2',
        "reviewed": True,
    },
    'calcw.last_issuance': {
        "text": 'Fecha de la última emisión o renovación',
        "en_hash": 'd4717e2461959ecd',
        "reviewed": True,
    },
    'calcw.next_due': {
        "text": 'Su próxima renovación de {label} vence el <strong>{date}</strong>.',
        "en_hash": '482de177c6309a6a',
        "reviewed": True,
    },
    'calcw.next_due_group': {
        "text": 'Su próxima renovación de {label} ({group}) vence el <strong>{date}</strong>.',
        "en_hash": '99d53d9b587b15ec',
        "reviewed": True,
    },
    'calcw.passed': {
        "text": 'Esa fecha ya pasó — consulte su certificado o permiso más reciente para conocer la fecha exacta de renovación.',
        "en_hash": 'e94d9a91ba42fc46',
        "reviewed": True,
    },
    'calcw.ph_birth_year': {
        "text": 'p. ej., 1985',
        "en_hash": 'a26553853444d93c',
        "reviewed": True,
    },
    'calcw.ph_year_a': {
        "text": 'p. ej., 2023',
        "en_hash": 'a37284d65c1d2285',
        "reviewed": True,
    },
    'calcw.ph_year_b': {
        "text": 'p. ej., 2025',
        "en_hash": '3e6455ed40572930',
        "reviewed": True,
    },
    'calcw.select': {
        "text": 'Seleccione&hellip;',
        "en_hash": 'aaf6959f9689f33b',
        "reviewed": True,
    },
    'calcw.select_group': {
        "text": 'Seleccione su grupo&hellip;',
        "en_hash": '601ac0fae329d257',
        "reviewed": True,
    },
    'calcw.select_state': {
        "text": 'Seleccione su estado&hellip;',
        "en_hash": 'dfa22e6787ddfd72',
        "reviewed": True,
    },
    'calcw.state_label': {
        "text": 'Su estado',
        "en_hash": '747ef243069f09a6',
        "reviewed": True,
    },
    'calcw.year_many': {
        "text": '{n} años',
        "en_hash": 'e12ed5b4ac222dcc',
        "reviewed": True,
    },
    'calcw.year_one': {
        "text": '{n} año',
        "en_hash": '1ba137ae2367a137',
        "reviewed": True,
    },
    'calcw.lbl_cohort_due_2027': {
        "text": 'Cohorte con vencimiento en 2027',
        "en_hash": '1e812668d34b4e8e',
        "reviewed": True,
    },
    'calcw.lbl_cohort_due_2028': {
        "text": 'Cohorte con vencimiento en 2028',
        "en_hash": 'e83bc1cd478166e2',
        "reviewed": True,
    },
    'calcw.lbl_cohort_due_2029': {
        "text": 'Cohorte con vencimiento en 2029',
        "en_hash": 'c15c1e365ca30d7e',
        "reviewed": True,
    },
    'calcw.lbl_even_birth_year_cpas': {
        "text": 'CPA nacidos en año par',
        "en_hash": '9c15ca2e03b15985',
        "reviewed": True,
    },
    'calcw.lbl_even_birth_year_cohort': {
        "text": 'Cohorte de año de nacimiento par',
        "en_hash": '505d5a4c1c20f762',
        "reviewed": True,
    },
    'calcw.lbl_even_cert_holders': {
        "text": 'Titulares de certificado con número par',
        "en_hash": 'd3beab9fe2c38747',
        "reviewed": True,
    },
    'calcw.lbl_even_license_holders': {
        "text": 'Titulares de licencia con número par',
        "en_hash": 'e4c9409137806d61',
        "reviewed": True,
    },
    'calcw.lbl_even_permit_holders': {
        "text": 'Titulares de permiso con número par',
        "en_hash": 'e802f62f49dbb5f3',
        "reviewed": True,
    },
    'calcw.lbl_group_1': {
        "text": 'Grupo 1',
        "en_hash": 'bf37557cc9016d1c',
        "reviewed": True,
    },
    'calcw.lbl_group_2': {
        "text": 'Grupo 2',
        "en_hash": '5b87e8ee3a4d1e58',
        "reviewed": True,
    },
    'calcw.lbl_group_3': {
        "text": 'Grupo 3',
        "en_hash": '39126b5487425841',
        "reviewed": True,
    },
    'calcw.lbl_odd_birth_year_cpas': {
        "text": 'CPA nacidos en año impar',
        "en_hash": 'a41ea9badce001a2',
        "reviewed": True,
    },
    'calcw.lbl_odd_birth_year_cohort': {
        "text": 'Cohorte de año de nacimiento impar',
        "en_hash": '618c22e3a844ca4f',
        "reviewed": True,
    },
    'calcw.lbl_odd_cert_holders': {
        "text": 'Titulares de certificado con número impar',
        "en_hash": '4f7838a47250812c',
        "reviewed": True,
    },
    'calcw.lbl_odd_license_holders': {
        "text": 'Titulares de licencia con número impar',
        "en_hash": '423bbf56a79c9ca5',
        "reviewed": True,
    },
    'calcw.lbl_odd_permit_holders': {
        "text": 'Titulares de permiso con número impar',
        "en_hash": '84fd30579bed2a4e',
        "reviewed": True,
    },
    'calcw.lbl_anchor_last_renewal': {
        "text": 'Año de su última renovación (o de su licenciamiento inicial, si nunca ha renovado)',
        "en_hash": 'c5d1b037d829ebea',
        "reviewed": True,
    },
    'calcw.lbl_anchor_first_licensed': {
        "text": 'Año en que obtuvo su licencia por primera vez',
        "en_hash": 'fd302f986bbbb124',
        "reviewed": True,
    },
    'calcw.lbl_anchor_last_issued': {
        "text": 'Año en que su licencia fue emitida o renovada por última vez',
        "en_hash": 'f4a7e0625ef4c019',
        "reviewed": True,
    },
    'calcw.lbl_term_length': {
        "text": 'Duración del período que figura en su certificado',
        "en_hash": 'c20685c182eef588',
        "reviewed": True,
    },
}


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Look up EN[key] (lang="en") or its reviewed, non-stale Spanish
    translation (lang="es"), formatting with kwargs. Falls back to English
    for lang="es" when no translation exists, it's stale (EN changed since
    translation), or it hasn't been reviewed yet -- a visitor never sees an
    unreviewed or stale Spanish string, they see correct English instead."""
    if key not in EN:
        raise KeyError(f"unknown i18n key: {key!r}")
    source = EN[key]
    if lang == "es":
        entry = ES.get(key)
        if entry is not None and entry.get("reviewed") and entry.get("en_hash") == _hash(source):
            source = entry["text"]
    return source.format(**kwargs) if kwargs else source


def en_hash(key: str) -> str:
    if key not in EN:
        raise KeyError(f"unknown i18n key: {key!r}")
    return _hash(EN[key])


def stale_or_missing_keys() -> list[str]:
    """Keys whose Spanish translation is missing, stale, or not yet
    reviewed -- the build-time signal for both the AuditLab review-request
    generator and the preship gate."""
    out = []
    for key in EN:
        entry = ES.get(key)
        if entry is None or not entry.get("reviewed") or entry.get("en_hash") != en_hash(key):
            out.append(key)
    return out
