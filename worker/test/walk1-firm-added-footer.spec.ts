/**
 * WALK-1 (2026-11-12, firm buyer walk): every email to a firm-added staff
 * member carried the footer "You're getting this because you asked
 * Deadline-Radar to track a CPA license renewal deadline." The staff member
 * never asked; their firm added them, and the body of the same email says so.
 * Pins: firm-added emails name the firm in the footer and never say "you
 * asked"; a self-signup reminder (no firmName) keeps the old clause unchanged;
 * the firm name is HTML-escaped in the HTML footer.
 */
import { describe, expect, it } from "vitest";

const SELF_CLAUSE = "You're getting this because you asked Deadline-Radar to track a CPA license renewal deadline.";

async function reminder(firmName: string | null) {
  const { buildReminderEmail } = await import("../src/emails");
  return buildReminderEmail(
    "Connecticut",
    "December 31, 2026",
    60,
    49,
    "https://example.com/next",
    "https://example.com/stop",
    "https://example.com/unsub",
    null,
    firmName
  );
}

describe("WALK-1 firm-added footer", () => {
  it("staff-added email names the firm and never says 'you asked'", async () => {
    const { buildFirmStaffAddedEmail } = await import("../src/emails");
    const built = buildFirmStaffAddedEmail("Acme CPAs", "Connecticut", "https://example.com/unsub");
    for (const body of [built.textBody, built.htmlBody]) {
      expect(body).not.toContain("because you asked");
    }
    expect(built.textBody).toContain(
      "You're getting this because Acme CPAs added you to Deadline-Radar to track your CPA license renewal deadline."
    );
    expect(built.htmlBody).toContain("because Acme CPAs added you to Deadline-Radar");
  });

  it("firm-tracked reminder names the firm and never says 'you asked'", async () => {
    const built = await reminder("Acme CPAs");
    for (const body of [built.textBody, built.htmlBody]) {
      expect(body).not.toContain("because you asked");
      expect(body).toContain("because Acme CPAs added you to Deadline-Radar");
    }
  });

  it("self-signup reminder keeps the original clause exactly", async () => {
    const built = await reminder(null);
    expect(built.textBody).toContain(
      SELF_CLAUSE + " We send only renewal reminders for that one deadline -- no marketing, ever."
    );
    expect(built.htmlBody).toContain(
      "because you asked Deadline-Radar to track a CPA license renewal deadline. We send only renewal " +
        "reminders for that one deadline &mdash; no marketing, ever."
    );
    expect(built.textBody).not.toContain("added you to");
  });

  it("escapes the firm name in the HTML footer", async () => {
    const built = await reminder("A&B <CPAs>");
    expect(built.htmlBody).toContain("because A&amp;B &lt;CPAs&gt; added you");
    expect(built.htmlBody).not.toContain("because A&B <CPAs> added you");
  });
});
