import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { classifyClick, TRACKED_LINK_CODES } from "../src/tracked_links";

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

  it("a known scanner User-Agent (Barracuda Sentinel), alone with no burst, is still flagged by UA", async () => {
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

  it("a known scanner ASN (Proofpoint), alone with no burst, is still flagged even with an ordinary browser User-Agent", async () => {
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

  it("a HEAD request, alone with no burst, is still flagged even with an ordinary browser User-Agent", async () => {
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
  it("a POST (or any non-GET), alone with no burst, is still flagged even with an ordinary browser User-Agent", async () => {
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

  // AuditLab TL-8 (LOW, 2026-10-05): the day-windowed design this replaced
  // unconditionally flagged the first hit of every UTC day on every code,
  // even a perfectly ordinary human click, because an empty window read as
  // "0 seconds since the first hit". The rolling burst-count design fixes
  // this: a LONE click (no sibling within 60s) is never flagged by this
  // signal, no matter how "first" it is.
  it("a genuinely lone first-ever click, with no UA/ASN match and no burst, is counted human", async () => {
    // Storage is NOT isolated between tests in this file (confirmed:
    // without this, the earlier "every allowlisted code" test's own hit
    // on 'mast' is still within the 60s window and poisons "lone"). Clear
    // this code's history first so "lone" is actually true here.
    await env.DB.prepare("DELETE FROM link_clicks WHERE code = 'mast'").run();

    const resp = await SELF.fetch("https://deadline-radar.com/r/mast", {
      headers: { "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36" },
      redirect: "manual",
      cf: { asn: 64512 } as any, // not in MAIL_SCANNER_ASNS
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("browser");
    expect(row.is_human).toBe(1);
  });

  // AuditLab TL-2 (MEDIUM, 2026-10-05): the original design queried
  // MIN(clicked_at) over a code's entire lifetime, so it went permanently
  // inert after a code's first 60 seconds ever -- wrong for the five
  // recurring codes (li/bsky/mast/x/nl), which get a fresh scanner/preview
  // burst on EVERY day's post or send, not just the code's debut. The
  // rolling burst-count design re-arms continuously with no day-boundary
  // logic at all: proven here by a code with only OLD (>1h, well past any
  // "today" window) history still catching a NEW burst.
  it("a recurring code's old history does not suppress a brand-new burst from being flagged", async () => {
    // Clear 'x' first -- same cross-test contamination reason as the
    // "lone first-ever click" test above. This test wants to control
    // exactly what counts as "old" vs "new" history itself.
    await env.DB.prepare("DELETE FROM link_clicks WHERE code = 'x'").run();

    await SELF.fetch("https://deadline-radar.com/r/x", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    await env.DB
      .prepare(
        "UPDATE link_clicks SET clicked_at = ? WHERE id = (SELECT id FROM link_clicks WHERE code = 'x' ORDER BY id DESC LIMIT 1)"
      )
      .bind(new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString()) // 25h ago
      .run();

    // A fresh two-hit burst, long after that old (now out-of-window)
    // history: the second of the two lands within 60s of the first and
    // IS flagged, proving the signal re-armed rather than staying inert.
    await SELF.fetch("https://deadline-radar.com/r/x", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    const resp = await SELF.fetch("https://deadline-radar.com/r/x", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(resp.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("mail_scanner");
    expect(row.is_human).toBe(0);
  });

  it("the second of two ordinary-browser hits within 60s is flagged; a third hit 61s+ later is not", async () => {
    // Clear 'co1' first -- same cross-test contamination reason as above
    // (other tests touch 'co1' too and storage is not isolated).
    await env.DB.prepare("DELETE FROM link_clicks WHERE code = 'co1'").run();
    const startId = (await env.DB.prepare("SELECT COALESCE(MAX(id), 0) as id FROM link_clicks").first<{ id: number }>())!.id;

    await SELF.fetch("https://deadline-radar.com/r/co1", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    const second = await SELF.fetch("https://deadline-radar.com/r/co1", {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/120" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(second.status).toBe(302);
    expect((await latestClick()).ua_class).toBe("mail_scanner");

    // Back-date only the two rows THIS test just created (never a blanket
    // `WHERE code = ...` -- other tests touch 'co1' too and storage may
    // not be isolated between tests) so the third hit lands outside their
    // 60s window, without sleeping the test suite for real or depending
    // on SQLite's own datetime()-string parsing of the stored format.
    await env.DB
      .prepare("UPDATE link_clicks SET clicked_at = ? WHERE id > ?")
      .bind(new Date(Date.now() - 5 * 60 * 1000).toISOString(), startId)
      .run();

    const third = await SELF.fetch("https://deadline-radar.com/r/co1", {
      headers: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15" },
      redirect: "manual",
      cf: { asn: 64512 } as any,
    });
    expect(third.status).toBe(302);
    const row = await latestClick();
    expect(row.ua_class).toBe("browser");
    expect(row.is_human).toBe(1);
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
  it("plain browser hit, no burst, no scanner signal -> browser/human", () => {
    const result = classifyClick({
      userAgent: "Mozilla/5.0 Chrome/120",
      method: "GET",
      asn: 64512,
      priorHitsInLast60s: 0,
    });
    expect(result).toEqual({ uaClass: "browser", isHuman: true });
  });

  it("UA/ASN/non-GET/burst signals each independently flag, and UA/ASN beat zero burst", () => {
    expect(
      classifyClick({ userAgent: "Barracuda Sentinel (EE)", method: "GET", asn: 64512, priorHitsInLast60s: 0 }).uaClass
    ).toBe("mail_scanner");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: 8075, priorHitsInLast60s: 0 }).uaClass).toBe("mail_scanner");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "HEAD", asn: 64512, priorHitsInLast60s: 0 }).uaClass).toBe("bot");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "POST", asn: 64512, priorHitsInLast60s: 0 }).uaClass).toBe("bot");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: 64512, priorHitsInLast60s: 1 }).uaClass).toBe("mail_scanner");
    expect(classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: 64512, priorHitsInLast60s: 5 }).uaClass).toBe("mail_scanner");
  });

  it("a null/unknown ASN never matches the scanner set", () => {
    const result = classifyClick({ userAgent: "Mozilla/5.0", method: "GET", asn: null, priorHitsInLast60s: 0 });
    expect(result).toEqual({ uaClass: "browser", isHuman: true });
  });
});
