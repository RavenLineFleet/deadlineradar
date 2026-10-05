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
// about it.
export const TRACKED_LINK_CODES: Record<string, string> = {
  co1: "/colorado/", // Colorado batch outreach (email + forms)
  w1: "/", // week-1 multi-state batch
  li: "/", // daily LinkedIn posts
  bsky: "/", // daily Bluesky posts
  mast: "/", // daily Mastodon posts
  x: "/", // daily X posts
  nl: "/", // Deadline-Radar Brief (Beehiiv newsletter)
};

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
  /** Seconds between `clicked_at` and the earliest prior click on this
   * same code (0 if this IS the earliest -- see classifyClick's own
   * comment on that choice). */
  secondsSinceFirstSeen: number;
}

export interface ClassifyResult {
  uaClass: UaClass;
  isHuman: boolean;
}

/**
 * Three independent signals, any one of which is sufficient to flag a hit
 * as automated: a known mail-scanner/bot UA or ASN; a HEAD request (a real
 * browser only ever GETs a clicked link); or landing within 60s of the
 * earliest-ever hit on this code. That last one is deliberately not "60s
 * after a *known send time*" -- these are one-shot campaign codes with no
 * per-recipient token, so there is no send-time record to compare against.
 * In practice a brand-new code gets no traffic before its campaign goes
 * out, so the scanner burst that precedes any real recipient opening the
 * message IS the earliest handful of hits -- real humans essentially never
 * read and click within a minute of send. Trade-off, stated plainly: if a
 * human is *genuinely* the very first-ever click on a code (e.g. someone
 * tests the link right after posting it), that hit is misclassified as
 * mail_scanner. Nothing is lost by that -- flagged hits are stored, not
 * dropped -- it just slightly undercounts "human" on that one row.
 */
export function classifyClick(input: ClassifyInput): ClassifyResult {
  const ua = (input.userAgent ?? "").toLowerCase();
  let uaClass: UaClass = "browser";

  if (MAIL_SCANNER_UA_SUBSTRINGS.some((s) => ua.includes(s)) || (input.asn != null && MAIL_SCANNER_ASNS.has(input.asn))) {
    uaClass = "mail_scanner";
  } else if (BOT_UA_SUBSTRINGS.some((s) => ua.includes(s))) {
    uaClass = "bot";
  }

  if (uaClass === "browser" && input.method === "HEAD") {
    uaClass = "bot";
  }
  if (uaClass === "browser" && input.secondsSinceFirstSeen < 60) {
    uaClass = "mail_scanner";
  }

  return { uaClass, isHuman: uaClass === "browser" };
}

async function secondsSinceFirstSeenForCode(db: D1Database, code: string, now: Date): Promise<number> {
  const row = await db.prepare("SELECT MIN(clicked_at) as first FROM link_clicks WHERE code = ?").bind(code).first<{
    first: string | null;
  }>();
  const firstIso = row?.first ?? null;
  if (!firstIso) return 0;
  const firstMs = Date.parse(firstIso);
  if (Number.isNaN(firstMs)) return 0;
  return Math.max(0, (now.getTime() - firstMs) / 1000);
}

/**
 * Handles GET/HEAD /r/<code>. Always redirects (unknown codes to "/", with
 * nothing logged); logging failures are swallowed so a DB hiccup can never
 * turn into a broken redirect for a real visitor, same posture as the
 * existing /go/mtcpa handler (index.ts).
 */
export async function handleTrackedLink(url: URL, request: Request, env: Env): Promise<Response> {
  const code = url.pathname.slice("/r/".length);
  const destination = TRACKED_LINK_CODES[code];

  if (destination === undefined) {
    return new Response(null, { status: 302, headers: { Location: "https://deadline-radar.com/" } });
  }

  try {
    const cf = request.cf;
    const country = typeof cf?.country === "string" ? cf.country : null;
    const colo = typeof cf?.colo === "string" ? cf.colo : null;
    const asn = typeof cf?.asn === "number" ? cf.asn : null;
    const now = new Date();

    const secondsSinceFirstSeen = await secondsSinceFirstSeenForCode(env.DB, code, now);
    const { uaClass, isHuman } = classifyClick({
      userAgent: request.headers.get("User-Agent"),
      method: request.method,
      asn,
      secondsSinceFirstSeen,
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
