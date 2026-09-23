/**
 * Resend transport (sender.ts) -- prep work for Devin's 2026-09-23 SendGrid
 * -> Resend cutover (SendGrid's trial account is confirmed blocked, 401
 * "Maximum credits exceeded"). `sendViaSendGrid()` gained a new TRAILING
 * `resendApiKey` param that selects this transport when truthy; no real
 * call site passes it yet (that wiring is a deliberate follow-up once the
 * Resend account/key/domain exist), so these are direct unit tests against
 * the exported function, not an end-to-end route test like
 * email-allowlist.spec.ts's SendGrid coverage -- there is no real call site
 * to exercise yet.
 */
import { describe, expect, it, vi } from "vitest";
import { sendViaSendGrid } from "../src/sender";
import type { BuiltEmail } from "../src/emails";

const RESEND_URL = "https://api.resend.com/emails";
const SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send";

function fakeEmail(headers: Record<string, string> = {}): BuiltEmail {
  return { subject: "Test subject", textBody: "Test text body", htmlBody: "<p>Test html body</p>", headers };
}

function okResendResponse(): Response {
  return new Response('{"id":"re_fake_id"}', { status: 200 });
}

describe("sendViaSendGrid() -- Resend transport selection", () => {
  it("with resendApiKey set, POSTs to Resend's endpoint, not SendGrid's", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined, // emailAllowlist
        undefined, // previewLogBody
        undefined, // replyTo
        "fake-resend-key"
      );
      expect(result).toBe(true);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(RESEND_URL);
      expect((init.headers as Record<string, string>).Authorization).toBe("Bearer fake-resend-key");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("with resendApiKey UNSET (every current call site's real behavior), POSTs to SendGrid's endpoint as before", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 202 }));
    try {
      const result = await sendViaSendGrid("fake-sendgrid-key", "someone@example.com", fakeEmail());
      expect(result).toBe(true);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(SENDGRID_URL);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("Resend request body: from/to/subject/text/html shape, FROM_EMAIL/FROM_NAME preserved", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
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
      await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        "reply@deadline-radar.com",
        "fake-resend-key"
      );
      const withReply = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(withReply.reply_to).toBe("reply@deadline-radar.com");

      fetchSpy.mockClear();
      await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
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
      await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        emailWithUnsub,
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
      const body = JSON.parse(String((fetchSpy.mock.calls[0] as [string, RequestInit])[1].body));
      expect(body.headers["List-Unsubscribe"]).toBe("<https://deadline-radar.com/unsubscribe?token=abc>");
      expect(body.headers["List-Unsubscribe-Post"]).toBe("List-Unsubscribe=One-Click");
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no headers on the built email -- Resend payload omits the `headers` field entirely (not an empty object)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
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
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
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
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
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
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "fake-resend-key"
      );
      expect(result).toBe(false);
      expect(logs.some((l) => l.includes("[resend-error]") && l.includes("network down"))).toBe(true);
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("EMAIL_ALLOWLIST gate applies identically to the Resend path -- a non-allowlisted recipient is refused before any fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "real-outside-person@somestranger.com",
        fakeEmail(),
        "owner@example.com", // allowlist -- recipient is NOT on it
        undefined,
        undefined,
        "fake-resend-key"
      );
      expect(result).toBe(false);
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("EMAIL_PREVIEW_LOG_BODY applies identically to the Resend path", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        "1", // previewLogBody
        undefined,
        "fake-resend-key"
      );
      expect(result).toBe(true);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("[preview-email] to=someone@example.com"));
    } finally {
      fetchSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  it("Resend takes priority when BOTH a SendGrid and a Resend key are present (the intended fallback-during-cutover posture)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResendResponse());
    try {
      await sendViaSendGrid(
        "real-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "real-resend-key"
      );
      const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(RESEND_URL);
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("no resendApiKey and no apiKey error -- falsy resendApiKey (empty string) still falls back to SendGrid, not a silent no-op", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 202 }));
    try {
      const result = await sendViaSendGrid(
        "fake-sendgrid-key",
        "someone@example.com",
        fakeEmail(),
        undefined,
        undefined,
        undefined,
        "" // falsy, same as unset
      );
      expect(result).toBe(true);
      const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
      expect(url).toBe(SENDGRID_URL);
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
