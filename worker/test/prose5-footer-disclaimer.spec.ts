// PROSE-5: every customer-facing footer variant (reminder, drip-course,
// newsletter) must carry the non-affiliation + not-advice sentence, in BOTH
// the HTML and text parts.
import { describe, expect, it } from "vitest";
import {
  buildDripCourseStep1Email,
  buildDripCourseStep2Email,
  buildDripCourseStep3Email,
  buildDripCourseStep4Email,
  buildNewsletterConfirmationEmail,
  buildNewsletterDigestEmail,
} from "../src/emails";

const U = "https://deadline-radar.com/api/x/unsubscribe?token=t";
const cases: Array<[string, () => { htmlBody: string; textBody: string }]> = [
  ["drip step 1", () => buildDripCourseStep1Email("Ann", "Texas", "renewal is annual by birth month", U)],
  ["drip step 2", () => buildDripCourseStep2Email("Ann", "Texas", U)],
  ["drip step 3", () => buildDripCourseStep3Email(null, "Texas", U)],
  ["drip step 4", () => buildDripCourseStep4Email(null, U)],
  ["newsletter confirmation", () => buildNewsletterConfirmationEmail("https://deadline-radar.com/api/newsletter/confirm?token=c", U)],
  ["newsletter digest", () =>
    buildNewsletterDigestEmail(
      [{ jurisdiction: "Colorado", topic: "mobility", summary: "s", effectiveDate: "2026-08-01",
         citation: "c", citationUrl: "https://example.gov/x", detailUrl: "https://deadline-radar.com/rule-changes/" }],
      U,
    )],
];

describe("PROSE-5 footer disclaimer on drip + newsletter emails", () => {
  for (const [name, build] of cases) {
    it(name, () => {
      const b = build();
      for (const body of [b.htmlBody, b.textBody]) {
        expect(body).toContain("not affiliated with, endorsed by, or connected to NASBA, the AICPA, or any state board");
        expect(body).toContain("not legal, tax, or professional advice");
        expect(body).toContain("confirm your exact renewal date with");
      }
    });
  }
});
