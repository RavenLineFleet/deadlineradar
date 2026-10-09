import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { classifyClick, EXTERNAL_LINK_SRC_VALUES, EXTERNAL_TRACKED_LINK_DESTINATIONS, TRACKED_LINK_CODES } from "../src/tracked_links";

type ClickRow = {
  id: number;
  code: string;
  clicked_at: string;
  country: string | null;
  colo: string | null;
  asn: number | null;
  ua_class: string;
  is_human: number;
};

async function latestClick(): Promise<ClickRow> {
  return (await env.DB.prepare("SELECT * FROM link_clicks ORDER BY id DESC LIMIT 1").first<ClickRow>())!;
}

async function countClicks(code?: string): Promise<number> {
  const row = code
    ? await env.DB.prepare("SELECT COUNT(*) as n FROM link_clicks WHERE code = ?").bind(code).first<{ n: number }>()
    : await env.DB.prepare("SELECT COUNT(*) as n FROM link_clicks").first<{ n: number }>();
  return row!.n;
}

describe("GET /r/:code -- tracked outreach short links", () => {
  it("every allowlisted code 302s to its destination with ?src=<code> and logs one row", async () => {
    for (const [code, destination] of TRACKED_LINK_CODES) {
      const before = await countClicks(code);
      const resp = await SELF.fetch(`https://deadline-radar.com/r/${code}`, {
        headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" },
        redirect: "manual",
      });
      expect(resp.status).toBe(302);
      const location = new URL(resp.headers.get("Location")!);
      expect(location.origin + location.pathname).toBe(`https://deadline-radar.com${destination}`);
      expect(location.searchParams.get("src")).toBe(code);
      expect(await countClicks(code)).toBe(before + 1);
    }
  });

  it("external codes (survey) 302 to their exact allowlisted URL, no ?src=, and log one row", async () => {
    for (const [code, destination] of EXTERNAL_TRACKED_LINK_DESTINATIONS) {
      expect(TRACKED_LINK_CODES.has(code)).toBe(false);
      const before = await countClicks(code);
      const resp = await SELF.fetch(`https://deadline-radar.com/r/${code}`, {
        headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" },
        redirect: "manual",
      });
      expect(resp.status).toBe(302);
      expect(resp.headers.get("Location")).toBe(destination);
      expect(destination.startsWith("https://docs.google.com/forms/d/e/")).toBe(true);
      expect(await countClicks(code)).toBe(before + 1);
    }
    expect(EXTERNAL_TRACKED_LINK_DESTINATIONS.has("survey")).toBe(true);
  });

  it("/r/survey?src=<allowed> logs under 'survey:<src>' and still 302s to the bare form URL; unknown src logs bare 'survey'", async () => {
    const form = EXTERNAL_TRACKED_LINK_DESTINATIONS.get("survey")!;
    for (const src of EXTERNAL_LINK_SRC_VALUES) {
      const before = await countClicks(`survey:${src}`);
      const resp = await SELF.fetch(`https://deadline-radar.com/r/survey?src=${src}`, { redirect: "manual" });
      expect(resp.status).toBe(302);
      expect(resp.headers.get("Location")).toBe(form);
      expect(await countClicks(`survey:${src}`)).toBe(before + 1);
    }
    const bareBefore = await countClicks("survey");
    const resp = await SELF.fetch("https://deadline-radar.com/r/survey?src=%27%3Bdrop%20table", { redirect: "manual" });
    expect(resp.headers.get("Location")).toBe(form);
    expect(await countClicks("survey")).toBe(bareBefore + 1);
    expect((await latestClick()).code).toBe("survey");
  });

  it("an unknown code 302s to / and logs NO row", async () => {
    const before = await countClicks();
    const resp = await SELF.fetch("https://deadline-radar.com/r/not-a-real-code", { redirect: "manual" });
    expect(resp.status).toBe(302);
    expect(resp.headers.get("Location")).toBe("https://deadline-radar.com/");
    expect(await countClicks()).toBe(before);
  });

  it("is not an open redirect -- a code-shaped attempt at an absolute URL is treated as an unknown code", async () => {
    const before = await countClicks();
    const resp = await SELF.fetch(
      "https://deadline-radar.com/r/" + encodeURIComponent("https://evil.example/phish"),
      { redirect: "manual" }
    );
    expect(resp.status).toBe(302);
    expect(resp.headers.get("Location")).toBe("https://deadline-radar.com/");
    expect(await countClicks()).toBe(before);
  });

  // AuditLab TL-1 (MEDIUM, 2026-10-05): a plain object literal's inherited
  // Object.prototype keys (constructor, toString, __proto__, ...) used to
  // return a non-undefined value from a bracket lookup, bypassing the
  // unknown-code branch -- 12 keys, enumerated in AuditLab's report. The
  // fix (a Map, which has no prototype-chain lookups) makes the whole
  // class structurally impossible rather than guarding each one.
  it("inherited Object.prototype keys are NOT bypasses -- all 12 are treated as unknown codes", async () => {
    const inheritedKeys = [
      "constructor",
      "__defineGetter__",
      "__defineSetter__",
      "hasOwnProperty",
      "__lookupGetter__",
      "__lookupSetter__",
      "isPrototypeOf",
      "propertyIsEnumerable",
      "toString",
      "valueOf",
      "__proto__",
      "toLocaleString",
    ];
    for (const key of inheritedKeys) {
      const before = await countClicks();
      const resp = await SELF.fetch(`https://deadline-radar.com/r/${encodeURIComponent(key)}`, { redirect: "manual" });
      expect(resp.status).toBe(302);
      expect(resp.headers.get("Location")).toBe("https://deadline-radar.com/");
      expect(await countClicks()).toBe(before);
    }
  });

  it("a known scanner User-Agent (Barracuda Sentinel) is flagged, not counted human", async () => {
    // Use a code not yet exercised by the "every allowlisted code" case's
    // >60s-later run so this isn't ALSO caught by the first-seen timing
    // signal -- isolate the UA signal specifically.
    await SELF.fetch("https://deadline-radar.com/r/w1", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
    }); // seed an earlier "first seen today" so the next hit isn't itself flagged by timing

    const resp = await SELF.fetch("https://deadline-radar.com/r/w1", {
      headers: { "User-Agent": "Barracuda Sentinel (EE)" },
      redirect: "manual",
      cf: { asn: 64512 } as any, // deliberately NOT one of the known scanner ASNs -- isolate the UA signal
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("mail_scanner");
    expect(row.is_human).toBe(0);
  });

  it("a known scanner ASN (Proofpoint) is flagged even with an ordinary browser User-Agent", async () => {
    await SELF.fetch("https://deadline-radar.com/r/nl", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
    });

    const resp = await SELF.fetch("https://deadline-radar.com/r/nl", {
      headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36" },
      redirect: "manual",
      cf: { asn: 26211 } as any, // Proofpoint US-West
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("mail_scanner");
    expect(row.is_human).toBe(0);
    expect(row.asn).toBe(26211);
  });

  it("a social link-preview bot (LinkedIn) is classified bot, not mail_scanner", async () => {
    await SELF.fetch("https://deadline-radar.com/r/li", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
    });

    const resp = await SELF.fetch("https://deadline-radar.com/r/li", {
      headers: { "User-Agent": "LinkedInBot/1.0 (compatible; Mozilla/5.0; Jakarta Commons-HttpClient/3.1 +http://www.linkedin.com)" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("bot");
    expect(row.is_human).toBe(0);
  });

  it("a HEAD request is flagged even with an ordinary browser User-Agent", async () => {
    await SELF.fetch("https://deadline-radar.com/r/bsky", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
    });

    const resp = await SELF.fetch("https://deadline-radar.com/r/bsky", {
      method: "HEAD",
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("bot");
    expect(row.is_human).toBe(0);
  });

  // AuditLab TL-4 (MEDIUM, 2026-10-05): HEAD alone left POST/PUT/DELETE/etc.
  // unflagged -- with no rate limiting and a short, guessable, published
  // code, that let anyone inflate the human count arbitrarily.
  it("a POST (or any non-GET) is flagged even with an ordinary browser User-Agent", async () => {
    await SELF.fetch("https://deadline-radar.com/r/co1", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
    });

    const resp = await SELF.fetch("https://deadline-radar.com/r/co1", {
      method: "POST",
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("bot");
    expect(row.is_human).toBe(0);
  });

  it("the very first hit on a code today is flagged (within-60s-of-first-seen-today, seeded by itself)", async () => {
    const resp = await SELF.fetch("https://deadline-radar.com/r/mast", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("mail_scanner");
    expect(row.is_human).toBe(0);
  });

  it("an ordinary browser hit well after the first-seen-today burst is counted human", async () => {
    await SELF.fetch("https://deadline-radar.com/r/x", {
      headers: { "User-Agent": "MicrosoftPreview/1.0" },
      redirect: "manual",
    });

    // Back-date that seed row's clicked_at so "now" reads as > 60s after
    // the code's first-seen-today hit, without sleeping the test suite
    // for real (and without crossing a UTC day boundary -- 5 minutes).
    await env.DB
      .prepare(
        "UPDATE link_clicks SET clicked_at = ? WHERE id = (SELECT id FROM link_clicks WHERE code = 'x' ORDER BY id ASC LIMIT 1)"
      )
      .bind(new Date(Date.now() - 5 * 60 * 1000).toISOString())
      .run();

    const resp = await SELF.fetch("https://deadline-radar.com/r/x", {
      headers: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("browser");
    expect(row.is_human).toBe(1);
  });

  // AuditLab TL-2 (MEDIUM, 2026-10-05): the original "first-ever" query was
  // unwindowed, so a recurring code (li/bsky/mast/x/nl) went permanently
  // inert on this signal after its first 60 seconds of existence -- a NEW
  // day's post/send burst was never caught again. Proven here: a code with
  // only YESTERDAY's history still gets today's first hit flagged.
  it("a recurring code's EARLIER-DAY history does not suppress today's new burst from being flagged", async () => {
    await SELF.fetch("https://deadline-radar.com/r/nl", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    await env.DB
      .prepare(
        "UPDATE link_clicks SET clicked_at = ? WHERE id = (SELECT id FROM link_clicks WHERE code = 'nl' ORDER BY id DESC LIMIT 1)"
      )
      .bind(new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString()) // 25h ago -- a prior UTC day
      .run();

    // Today's first hit on this same recurring code: ordinary browser UA,
    // unlisted ASN -- if the day-window fix is missing, this reads as "not
    // the first-ever hit, and it's been >60s since that old one" and gets
    // counted human. With the fix, it's the first hit TODAY and is flagged.
    const resp = await SELF.fetch("https://deadline-radar.com/r/nl", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("mail_scanner");
    expect(row.is_human).toBe(0);
  });

  it("logs no IP and no raw user-agent string -- only code/time/coarse-geo/classification columns", async () => {
    await SELF.fetch("https://deadline-radar.com/r/co1", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120", "cf-connecting-ip": "203.0.113.90" },
      redirect: "manual",
      cf: { asn: 64512, country: "US", colo: "DEN" } as any,
    });
    const row = await latestClick();
    expect(Object.keys(row).sort()).toEqual(
      ["asn", "clicked_at", "code", "colo", "country", "id", "is_human", "ua_class"].sort()
    );
    expect(row.country).toBe("US");
    expect(row.colo).toBe("DEN");
    expect(row.asn).toBe(64512);
  });

  it("also works through the /api prefix the way /go/mtcpa does (defensive -- the real Route is /r/*, not /api/r/*)", async () => {
    const resp = await SELF.fetch("https://deadline-radar.com/api/r/w1", { redirect: "manual" });
    expect(resp.status).toBe(302);
  });
});

describe("classifyClick (unit)", () => {
  it("plain browser hit, long after first-seen-today, no scanner signal -> browser/human", () => {
    const result = classifyClick({
      userAgent: "Mozilla/5.0 Chrome/120",
      method: "GET",
      asn: 64512,
      secondsSinceFirstSeenToday: 3600,
    });
    expect(result).toEqual({ uaClass: "browser", isHuman: true });
  });

  it("UA/ASN/non-GET/timing signals each independently flag, and UA/ASN beat a late timestamp", () => {
    expect(
      classifyClick({ userAgent: "Barracuda Sentinel (EE)", method: "GET", asn: 64512, secondsSinceFirstSeenToday: 3600 }).uaClass
    ).toBe("mail_scanner");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: 8075, secondsSinceFirstSeenToday: 3600 }).uaClass).toBe(
      "mail_scanner"
    );
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "HEAD", asn: 64512, secondsSinceFirstSeenToday: 3600 }).uaClass).toBe("bot");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "POST", asn: 64512, secondsSinceFirstSeenToday: 3600 }).uaClass).toBe("bot");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: 64512, secondsSinceFirstSeenToday: 10 }).uaClass).toBe(
      "mail_scanner"
    );
  });

  it("a null/unknown ASN never matches the scanner set", () => {
    const result = classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: null, secondsSinceFirstSeenToday: 3600 });
    expect(result).toEqual({ uaClass: "browser", isHuman: true });
  });
});
