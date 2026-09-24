/**
 * sendEmail() -- Resend transport (sender.ts). SendGrid has been removed
 * entirely (Orchestrator directive, 2026-09-23, Devin: "Replace SendGrid
 * completely" -- its trial account was credit-blocked 09-02; Resend was
 * wired as a fallback-swap the same day, cut over to active, live-tested,
 * then made the sole transport). Resend is the only transport now, so
 * these are direct unit tests against the exported function's real,
 * single-transport behavior.
 */
import { describe, expect, it, vi } from "vitest";
import { sendEmail } from "../src/sender";
import type { BuiltEmail } from "../src/emails";

const RESEND_URL = "https://api.resend.com/emails";

function fakeEmail(headers: Record<string, string> = {}): BuiltEmail {
  return { subject: "Test subject", textBody: "Test text body", htmlBody: "<p>Test html body</p>", headers };
}

function okResendResponse(): Response {
  return new Response('{"id":"re_fake_id"}', { status: 200 });
}

describe("sendEmail() -- Resend transport", () => {
  it("POSTs to Resend's endpoint with the API key as a Bearer token", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      expect(result).toBe(true);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(RESEND_URL);
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer fake-resend-key");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("request body: from/to/subject/text/html shape, FROM_EMAIL/FROM_NAME preserved", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      const body = JSON.parse(String(init.body));
      expect(body.from).toBe("Deadline-Radar <noreply@deadline-radar.com>");
      expect(body.to).toEqual(["someone@example.com"]);
      expect(body.subject).toBe("Test subject");
      expect(body.text).toBe("Test text body");
      expect(body.html).toBe("<p>Test html body</p>");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("reply_to is set when replyTo is passed, omitted when it isn't", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendEmail("fake-resend-key", "someone@example.com", fakeEmail(), undefined, undefined, "reply@deadline-radar.com");
      const withReply = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(withReply.reply_to).toBe("reply@deadline-radar.com");

      fetchSpy.mockClear();
      await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      const withoutReply = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(withoutReply).not.toHaveProperty("reply_to");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("List-Unsubscribe/List-Unsubscribe-Post headers (RFC 8058) pass through unchanged", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const emailWithUnsub = fakeEmail({
        "List-Unsubscribe": "<https://deadline-radar.com/unsubscribe?token=abc>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
      });
      await sendEmail("fake-resend-key", "someone@example.com", emailWithUnsub);
      const body = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(body.headers["List-Unsubscribe"]).toBe("<https://deadline-radar.com/unsubscribe?token=abc>");
      expect(body.headers["List-Unsubscribe-Post"]).toBe("List-Unsubscribe=One-Click");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no headers on the built email -- payload omits the `headers` field entirely (not an empty object)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      const body = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(body).not.toHaveProperty("headers");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("a 2xx returns true and logs nothing", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      expect(result).toBe(true);
      expect(logSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("a non-2xx (e.g. an invalid/unverified domain) logs [resend-fail] with status+body and returns false", async () => {
    const logs: string[] = [];
    const logSpy = vi.spyOn(console, "log").mockImplementation((m: unknown) => { logs.push(String(m)); });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"statusCode":403,"message":"The gmail.com domain is not verified","name":"validation_error"}', {
        status: 403,
      })
    );
    try {
      const result = await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      expect(result).toBe(false);
      const line = logs.find((l) => l.includes("[resend-fail]"));
      expect(line).toBeDefined();
      expect(line).toContain("status=403");
      expect(line).toContain("not verified");
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("a thrown fetch (network failure / timeout abort) logs [resend-error] and returns false, never throwing", async () => {
    const logs: string[] = [];
    const logSpy = vi.spyOn(console, "log").mockImplementation((m: unknown) => { logs.push(String(m)); });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    try {
      const result = await sendEmail("fake-resend-key", "someone@example.com", fakeEmail());
      expect(result).toBe(false);
      expect(logs.some((l) => l.includes("[resend-error]") && l.includes("network down"))).toBe(true);
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("EMAIL_ALLOWLIST gate: a non-allowlisted recipient is refused before any fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendEmail(
        "fake-resend-key",
        "real-outside-person@somestranger.com",
        fakeEmail(),
        "owner@example.com" // allowlist -- recipient is NOT on it
      );
      expect(result).toBe(false);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("EMAIL_PREVIEW_LOG_BODY logs the full built email", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendEmail("fake-resend-key", "someone@example.com", fakeEmail(), undefined, "1");
      expect(result).toBe(true);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("[preview-email] to=someone@example.com"));
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });
});
