/**
 * GET /r/<code> -- short-link redirector for email/social outreach clicks.
 *
 * Orchestrator directive (2026-10-05, Devin RED-approved: "We need to be
 * able to track if someone from linkedin and email check us out."):
 * Cloudflare Web Analytics has no query-string dimension (confirmed --
 * requestQuery is an unknown field there), so an outreach `?src=` tag is
 * invisible to it, and a click that never gets logged on its first send is
 * lost for good -- these firms are never re-emailed. This logs the click
 * itself, server-side, before redirecting.
 *
 * SECURITY: `TRACKED_LINK_CODES` is a hard-coded allowlist. An unknown code
 * 302s to "/" and logs nothing -- the destination is never attacker-
 * controlled, so this cannot become an open redirect.
 */
import type { Env } from "./env";

export type UaClass = "browser" | "bot" | "mail_scanner";

// code -> destination path (resolved against https://deadline-radar.com/).
// Adding a code is a one-line change here; nothing else needs to know
// about it. AuditLab TL-1 (MEDIUM, 2026-10-05): a plain object literal
// inherits Object.prototype, so a lookup like `obj["constructor"]` returns
// a non-undefined value for 12 keys that were never added here, bypassing
// the "unknown code" branch below. A Map has no prototype-chain lookups at
// all -- the whole bug class is structurally impossible, not just guarded
// against at the call site.
export const TRACKED_LINK_CODES: ReadonlyMap<string, string> = new Map([
  ["co1", "/colorado/"], // Colorado batch outreach (email + forms)
  ["w1", "/"], // week-1 multi-state batch
  ["li", "/"], // daily LinkedIn posts
  ["bsky", "/"], // daily Bluesky posts
  ["mast", "/"], // daily Mastodon posts
  ["x", "/"], // daily X posts
  ["nl", "/"], // Deadline-Radar Brief (Beehiiv newsletter)
]);

// Mail-security gateways the directive names (Microsoft Safe Links/O365
// ATP, Proofpoint URL Defense, Mimecast, Barracuda) now mostly fetch links
// with a spoofed ordinary-browser User-Agent rather than an identifiable
// one (documented in a mautic bot-detection issue thread), so UA substring
// matching alone only catches the two that don't bother spoofing:
// Barracuda's "Barracuda Sentinel (EE)" (seen in the wild per an Adobe
// Marketo community report) and Microsoft's separate link-preview fetcher
// "MicrosoftPreview/1.0" (Microsoft's own crawler documentation). For the
// two that do spoof, and as a backstop for Microsoft's own scanning infra,
// this also matches the ASN the hit arrives from -- looked up against
// public ASN registries (db-ip.com / ip2location.com), not guessed:
//   Microsoft:  AS8075
//   Proofpoint: AS26211 (pphosted.com, US-West), AS22843 (US-East), AS52129, AS13916
//   Mimecast:   AS30031, AS33538, AS39588, AS397726 (North America), AS136792 (Australia)
//   Barracuda:  AS15324 (their own ASN; Email Gateway Defense/Sentinel also
//               runs partly on AWS ranges an ASN check can't see -- the UA
//               match above is the primary Barracuda signal, this is only
//               a backstop)
// ASN-based matching is inherently approximate (ranges shift, gateways add
// capacity) -- this is a best-effort list from today's lookup, not a
// guarantee, and the report tool surfaces raw ASNs too so a pattern this
// list misses is still visible for a future update.
const MAIL_SCANNER_ASNS: ReadonlySet<number> = new Set([
  8075, 26211, 22843, 52129, 13916, 30031, 33538, 39588, 397726, 136792, 15324,
]);

const MAIL_SCANNER_UA_SUBSTRINGS = ["barracuda sentinel", "microsoftpreview"];

// Social link-preview fetchers -- one per code this fleet posts a link to
// (LinkedIn/Bluesky/Mastodon/X all fetch a shared URL server-side within
// seconds of the post, to build the preview card) -- plus the generic
// crawler/scripted-client tokens any bot-detector checks for. Verified UA
// substrings, not guessed: "linkedinbot" (LinkedIn), "twitterbot" (X's
// Twitterbot/1.0, still current), "bluesky cardyb" (Bluesky's own preview
// fetcher), "mastodon/" (Mastodon's per-instance "Mastodon/{version}
// (+https://{instance}/)" format -- federation means many different
// instance domains will fetch the same link, each with this UA prefix).
// Kept separate from MAIL_SCANNER_* because the directive's ua_class keeps
// "bot" and "mail-scanner" as distinct categories.
const BOT_UA_SUBSTRINGS = [
  "linkedinbot",
  "twitterbot",
  "bluesky cardyb",
  "mastodon/",
  "facebookexternalhit",
  "slackbot",
  "discordbot",
  "telegrambot",
  "whatsapp",
  "bot",
  "spider",
  "crawler",
  "curl/",
  "wget/",
  "python-requests",
  "go-http-client",
  "okhttp",
  "headlesschrome",
  "phantomjs",
];

export interface ClassifyInput {
  userAgent: string | null;
  method: string;
  asn: number | null;
  /** How many OTHER clicks on this same code landed in the 60s immediately
   * before this one -- 0 for a lone click. See classifyClick's own comment
   * for why a rolling count, not a "since first click" timestamp. */
  priorHitsInLast60s: number;
}

export interface ClassifyResult {
  uaClass: UaClass;
  isHuman: boolean;
}

/**
 * Three independent signals, any one of which is sufficient to flag a hit
 * as automated: a known mail-scanner/bot UA or ASN; a non-GET request (a
 * real browser only ever GETs a clicked link -- AuditLab TL-4, MEDIUM,
 * 2026-10-05: HEAD alone left POST/DELETE/etc. unflagged, and with no rate
 * limiting on this endpoint, anyone who learns a short, guessable,
 * published-in-email code like `x` or `li` could otherwise inflate its
 * count arbitrarily); or arriving as part of a burst -- at least one OTHER
 * click on the same code in the preceding 60 seconds. This is deliberately
 * a rolling burst count, not "60s after a *known send time*" (there is no
 * per-recipient send timestamp to compare against for these campaign
 * codes) and not "60s after the day's first click" either -- AuditLab
 * caught two problems with that earlier design in the same review:
 *
 *   - TL-2 (MEDIUM): scoped to a code's entire lifetime, it only ever
 *     fired in a code's first 60 seconds EVER, then was permanently inert
 *     -- true-ish for one-shot `co1`/`w1`, but wrong for `li`/`bsky`/
 *     `mast`/`x`/`nl`, which get a fresh scanner/preview burst on EVERY
 *     day's post or send.
 *   - TL-8 (LOW): day-scoping that fixed TL-2 introduced a new cost --
 *     the FIRST hit of every UTC day was unconditionally flagged even
 *     when it was a perfectly ordinary human click, because an empty
 *     window reads as "0 seconds since the first hit".
 *
 * A rolling "was there a burst" count fixes both at once: a lone click
 * (human or a single one-shot scanner hit) is never flagged by this
 * signal alone, a real multi-engine scanner sweep (which is what the
 * directive's four named gateways actually produce -- plural prefetches
 * within seconds, not one) still is, and it re-arms continuously rather
 * than needing any day-boundary logic at all. Trade-off, stated plainly:
 * a lone scanner hit with no UA/ASN match and no sibling hit within 60s
 * is NOT caught by this signal (the UA/ASN lists carry that case).
 */
export function classifyClick(input: ClassifyInput): ClassifyResult {
  const ua = (input.userAgent ?? "").toLowerCase();
  let uaClass: UaClass = "browser";

  if (MAIL_SCANNER_UA_SUBSTRINGS.some((s) => ua.includes(s)) || (input.asn != null && MAIL_SCANNER_ASNS.has(input.asn))) {
    uaClass = "mail_scanner";
  } else if (BOT_UA_SUBSTRINGS.some((s) => ua.includes(s))) {
    uaClass = "bot";
  }

  if (uaClass === "browser" && input.method !== "GET") {
    uaClass = "bot";
  }
  if (uaClass === "browser" && input.priorHitsInLast60s > 0) {
    uaClass = "mail_scanner";
  }

  return { uaClass, isHuman: uaClass === "browser" };
}

async function priorHitsInLast60sForCode(db: D1Database, code: string, now: Date): Promise<number> {
  const windowStart = new Date(now.getTime() - 60_000).toISOString();
  const row = await db
    .prepare("SELECT COUNT(*) as n FROM link_clicks WHERE code = ?1 AND clicked_at >= ?2")
    .bind(code, windowStart)
    .first<{ n: number }>();
  return row?.n ?? 0;
}

/**
 * Handles GET/HEAD /r/<code>. Always redirects (unknown codes to "/", with
 * nothing logged); logging failures are swallowed so a DB hiccup can never
 * turn into a broken redirect for a real visitor, same posture as the
 * existing /go/mtcpa handler (index.ts).
 */
export async function handleTrackedLink(url: URL, request: Request, env: Env): Promise<Response> {
  const code = url.pathname.slice("/r/".length);

  if (!TRACKED_LINK_CODES.has(code)) {
    return new Response(null, { status: 302, headers: { Location: "https://deadline-radar.com/" } });
  }
  const destination = TRACKED_LINK_CODES.get(code)!;

  try {
    const cf = request.cf;
    const country = typeof cf?.country === "string" ? cf.country : null;
    const colo = typeof cf?.colo === "string" ? cf.colo : null;
    const asn = typeof cf?.asn === "number" ? cf.asn : null;
    const now = new Date();

    const priorHitsInLast60s = await priorHitsInLast60sForCode(env.DB, code, now);
    const { uaClass, isHuman } = classifyClick({
      userAgent: request.headers.get("User-Agent"),
      method: request.method,
      asn,
      priorHitsInLast60s,
    });

    await env.DB.prepare(
      "INSERT INTO link_clicks (code, clicked_at, country, colo, asn, ua_class, is_human) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
      .bind(code, now.toISOString(), country, colo, asn, uaClass, isHuman ? 1 : 0)
      .run();
  } catch {
    // swallow -- see function comment above
  }

  const destUrl = new URL(destination, "https://deadline-radar.com/");
  destUrl.searchParams.set("src", code);
  return new Response(null, { status: 302, headers: { Location: destUrl.toString() } });
}
