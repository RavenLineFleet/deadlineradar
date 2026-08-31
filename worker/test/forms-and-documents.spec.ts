/**
 * Forms & Documents cluster (2026-08-31, roadmap #303/#304/#306):
 *   - Renewal document checklist (migration 0072) -- checklist_items
 *   - Compliance sign-off audit trail + lightweight e-signature
 *     (migration 0073) -- compliance_attestations
 * See those migrations' own docstrings for design rationale, especially
 * 0073's SECURITY NOTE on what signature_text does and does not guarantee.
 */
import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import * as store from "../src/store";

const BASE = "https://deadline-radar.com";

// Same gate-bypass shape documents.spec.ts uses -- these routes are gated
// by the same valueLineDenialResponse() as document storage, so tests that
// aren't ABOUT that gate backdate created_at to stay on the grandfathered
// free-tier path, same as every other non-gate test in that file.
async function createFirmWithSession(name: string, adminEmail: string): Promise<{ firmId: string; memberId: string; cookie: string }> {
  const firm = await store.createFirm(env.DB, { name, adminEmail });
  await env.DB.prepare("UPDATE firms SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?1").bind(firm.id).run();
  const { rawSessionToken } = await store.createSession(env.DB, firm.id);
  return { firmId: firm.id, memberId: firm.memberId, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function seedStaffMember(firmId: string, role: store.FirmMemberRole): Promise<{ memberId: string; cookie: string }> {
  const member = await store.createFirmMember(env.DB, {
    firmId,
    email: `formsdocs-${role}-${Date.now()}-${Math.random()}@example.com`,
    name: `Test ${role}`,
    role,
  });
  const { rawSessionToken } = await store.createSession(env.DB, firmId, member.id);
  return { memberId: member.id, cookie: `dr_firm_session=${rawSessionToken}` };
}

async function addStaff(cookie: string, fields: Record<string, string>): Promise<{ id: string; email: string }> {
  const resp = await SELF.fetch(`${BASE}/firm/licenses`, {
    method: "POST",
    headers: { "content-type": "application/json", Cookie: cookie },
    body: JSON.stringify(fields),
  });
  expect(resp.status).toBe(201);
  return (await resp.json()) as { id: string; email: string };
}

async function addLicense(cookie: string, label: string, stateSlug = "georgia", licenseTypeId = "ga-individual") {
  return addStaff(cookie, {
    staff_label: label,
    email: `${label.toLowerCase().replace(/\s+/g, "-")}-${Date.now()}@example.com`,
    state_slug: stateSlug,
    license_type_id: licenseTypeId,
  });
}

// ---------------------------------------------------------------------------
// Checklist (roadmap #303)
// ---------------------------------------------------------------------------

describe("POST /firm/licenses/:id/checklist", () => {
  it("401s with no session", async () => {
    const resp = await SELF.fetch(`${BASE}/firm/licenses/whatever/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ label: "x" }),
    });
    expect(resp.status).toBe(401);
  });

  it("404s for a subscriber that doesn't belong to this firm", async () => {
    const { cookie } = await createFirmWithSession("Checklist Firm A", `checklista-${Date.now()}@example.com`);
    const { cookie: cookieB } = await createFirmWithSession("Checklist Firm B", `checklistb-${Date.now()}@example.com`);
    const staffB = await addLicense(cookieB, "B Staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staffB.id}/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ label: "x" }),
    });
    expect(resp.status).toBe(404);
  });

  it("Staff role is 403'd (read-only convention)", async () => {
    const { firmId, cookie } = await createFirmWithSession("Checklist Staff Gate Firm", `checklistgate-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Gated Staff");
    const { cookie: staffCookie } = await seedStaffMember(firmId, "staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: staffCookie },
      body: JSON.stringify({ label: "x" }),
    });
    expect(resp.status).toBe(403);
    // But Staff CAN still read the (empty) list.
    const listResp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, { headers: { Cookie: staffCookie } });
    expect(listResp.status).toBe(200);
  });

  it("template:true seeds the default 4-item template", async () => {
    const { cookie } = await createFirmWithSession("Template Firm", `template-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Template Staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ template: true }),
    });
    expect(resp.status).toBe(201);
    const body = (await resp.json()) as { items: { label: string; status: string }[] };
    expect(body.items.length).toBe(store.CHECKLIST_DEFAULT_TEMPLATE.length);
    expect(body.items.every((i) => i.status === "pending")).toBe(true);
  });

  it("rejects an empty label", async () => {
    const { cookie } = await createFirmWithSession("Empty Label Firm", `emptylabel-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Empty Label Staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ label: "   " }),
    });
    expect(resp.status).toBe(400);
  });

  it("enforces CHECKLIST_MAX_ITEMS_PER_LICENSE", async () => {
    const { firmId, cookie } = await createFirmWithSession("Max Items Firm", `maxitems-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Max Items Staff");
    for (let i = 0; i < store.CHECKLIST_MAX_ITEMS_PER_LICENSE; i++) {
      const created = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: `Item ${i}`, sortOrder: i });
      expect(created).not.toBeNull();
    }
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ label: "One too many" }),
    });
    expect(resp.status).toBe(400);
  });
});

describe("PATCH /firm/checklist/:id", () => {
  it("401s with no session", async () => {
    const resp = await SELF.fetch(`${BASE}/firm/checklist/whatever`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status: "complete" }),
    });
    expect(resp.status).toBe(401);
  });

  it("updates status", async () => {
    const { firmId, cookie } = await createFirmWithSession("Patch Status Firm", `patchstatus-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Patch Status Staff");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: "Item", sortOrder: 0 });
    const resp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ status: "complete" }),
    });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { item: { status: string } };
    expect(body.item.status).toBe("complete");
  });

  it("rejects an invalid status value", async () => {
    const { firmId, cookie } = await createFirmWithSession("Invalid Status Firm", `invalidstatus-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Invalid Status Staff");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: "Item", sortOrder: 0 });
    const resp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ status: "not-a-real-status" }),
    });
    expect(resp.status).toBe(400);
  });

  it("links a document that belongs to the SAME subscriber", async () => {
    const { firmId, cookie } = await createFirmWithSession("Link Doc Firm", `linkdoc-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Link Doc Staff");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: "Item", sortOrder: 0 });
    const doc = await store.createDocument(env.DB, {
      firmId,
      subscriberId: staff.id,
      kind: "license",
      r2Key: `${firmId}/${staff.id}/doc1`,
      filename: "cert.pdf",
      contentType: "application/pdf",
      sizeBytes: 1000,
    });
    const resp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ document_id: doc!.id }),
    });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { item: { document_id: string | null } };
    expect(body.item.document_id).toBe(doc!.id);
  });

  it("REJECTS linking a document that belongs to a DIFFERENT subscriber on the same firm", async () => {
    const { firmId, cookie } = await createFirmWithSession("Cross License Firm", `crosslicense-${Date.now()}@example.com`);
    const staffA = await addLicense(cookie, "Cross License Staff A");
    const staffB = await addLicense(cookie, "Cross License Staff B");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staffA.id, label: "Item", sortOrder: 0 });
    const docBelongingToB = await store.createDocument(env.DB, {
      firmId,
      subscriberId: staffB.id,
      kind: "license",
      r2Key: `${firmId}/${staffB.id}/doc1`,
      filename: "cert.pdf",
      contentType: "application/pdf",
      sizeBytes: 1000,
    });
    const resp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ document_id: docBelongingToB!.id }),
    });
    expect(resp.status).toBe(404);
  });

  it("does not leak or allow mutation across firms", async () => {
    const { firmId, cookie } = await createFirmWithSession("Isolation Firm A", `isoa-${Date.now()}@example.com`);
    const { cookie: cookieB } = await createFirmWithSession("Isolation Firm B", `isob-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Isolation Staff");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: "Item", sortOrder: 0 });

    const patchResp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", Cookie: cookieB },
      body: JSON.stringify({ status: "complete" }),
    });
    expect(patchResp.status).toBe(404);

    const deleteResp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, { method: "DELETE", headers: { Cookie: cookieB } });
    expect(deleteResp.status).toBe(404);

    // Firm A's own list is untouched.
    const listResp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, { headers: { Cookie: cookie } });
    const body = (await listResp.json()) as { items: unknown[] };
    expect(body.items.length).toBe(1);
  });
});

describe("DELETE /firm/checklist/:id", () => {
  it("soft-deletes -- item disappears from the list", async () => {
    const { firmId, cookie } = await createFirmWithSession("Delete Item Firm", `deleteitem-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Delete Item Staff");
    const item = await store.createChecklistItem(env.DB, { firmId, subscriberId: staff.id, label: "Item", sortOrder: 0 });
    const resp = await SELF.fetch(`${BASE}/firm/checklist/${item!.id}`, { method: "DELETE", headers: { Cookie: cookie } });
    expect(resp.status).toBe(200);
    const listResp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/checklist`, { headers: { Cookie: cookie } });
    const body = (await listResp.json()) as { items: unknown[] };
    expect(body.items.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Compliance attestations (roadmap #304 + #306)
// ---------------------------------------------------------------------------

describe("POST /firm/licenses/:id/attestations", () => {
  it("401s with no session", async () => {
    const resp = await SELF.fetch(`${BASE}/firm/licenses/whatever/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ signature_text: "Jane Doe" }),
    });
    expect(resp.status).toBe(401);
  });

  it("404s for a subscriber that doesn't belong to this firm", async () => {
    const { cookie } = await createFirmWithSession("Attest Firm A", `attesta-${Date.now()}@example.com`);
    const { cookie: cookieB } = await createFirmWithSession("Attest Firm B", `attestb-${Date.now()}@example.com`);
    const staffB = await addLicense(cookieB, "Attest B Staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staffB.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ signature_text: "Jane Doe" }),
    });
    expect(resp.status).toBe(404);
  });

  it("Staff role is 403'd, but can still read", async () => {
    const { firmId, cookie } = await createFirmWithSession("Attest Staff Gate Firm", `attestgate-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Attest Gated Staff");
    const { cookie: staffCookie } = await seedStaffMember(firmId, "staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: staffCookie },
      body: JSON.stringify({ signature_text: "Jane Doe" }),
    });
    expect(resp.status).toBe(403);
    const listResp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, { headers: { Cookie: staffCookie } });
    expect(listResp.status).toBe(200);
  });

  it("rejects a blank/too-short signature", async () => {
    const { cookie } = await createFirmWithSession("Blank Sig Firm", `blanksig-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Blank Sig Staff");
    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ signature_text: " " }),
    });
    expect(resp.status).toBe(400);
  });

  it("creates an attestation with the signer's identity resolved server-side, not client-supplied", async () => {
    const { firmId, cookie, memberId } = await createFirmWithSession("Attest Create Firm", `attestcreate-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Attest Create Staff", "alabama", "al-all");
    const member = await store.getFirmMemberById(env.DB, firmId, memberId);

    const resp = await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      // A hostile client trying to spoof a DIFFERENT signer's identity --
      // the route must ignore this and resolve identity from the session.
      body: JSON.stringify({ signature_text: "Jane Doe", attested_by_name: "Someone Else", attested_by_email: "spoof@example.com" }),
    });
    expect(resp.status).toBe(201);
    const body = (await resp.json()) as {
      attestation: { attested_by_name: string; attested_by_email: string; signature_text: string; statement: string; staff_label: string };
    };
    expect(body.attestation.attested_by_email).toBe(member!.email);
    expect(body.attestation.attested_by_name).toBe(member!.name || member!.email);
    expect(body.attestation.signature_text).toBe("Jane Doe");
    expect(body.attestation.statement).toBe(store.ATTESTATION_STATEMENT);
    expect(body.attestation.staff_label).toBe("Attest Create Staff");

    // status_snapshot captured the license's real state at sign time.
    const row = await env.DB.prepare("SELECT status_snapshot FROM compliance_attestations WHERE id = ?1")
      .bind((await (await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, { headers: { Cookie: cookie } })).json() as { attestations: { id: string }[] }).attestations[0]!.id)
      .first<{ status_snapshot: string }>();
    const snapshot = JSON.parse(row!.status_snapshot) as { state_slug: string };
    expect(snapshot.state_slug).toBe("alabama");
  });

  it("appears in GET /firm/audit-trail", async () => {
    const { cookie } = await createFirmWithSession("Attest Audit Firm", `attestaudit-${Date.now()}@example.com`);
    const staff = await addLicense(cookie, "Attest Audit Staff");
    await SELF.fetch(`${BASE}/firm/licenses/${staff.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookie },
      body: JSON.stringify({ signature_text: "Audit Signer" }),
    });
    const resp = await SELF.fetch(`${BASE}/firm/audit-trail`, { headers: { Cookie: cookie } });
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as { attestations: { signature_text: string; staff_label: string }[] };
    expect(body.attestations.length).toBe(1);
    expect(body.attestations[0]?.signature_text).toBe("Audit Signer");
    expect(body.attestations[0]?.staff_label).toBe("Attest Audit Staff");
  });

  it("does not leak another firm's attestations, in the per-license list OR the audit-trail export", async () => {
    const { cookie: cookieA } = await createFirmWithSession("Attest Iso A", `attestisoa-${Date.now()}@example.com`);
    const { cookie: cookieB } = await createFirmWithSession("Attest Iso B", `attestisob-${Date.now()}@example.com`);
    const staffA = await addLicense(cookieA, "Attest Iso Staff A");
    await SELF.fetch(`${BASE}/firm/licenses/${staffA.id}/attestations`, {
      method: "POST",
      headers: { "content-type": "application/json", Cookie: cookieA },
      body: JSON.stringify({ signature_text: "A Signer" }),
    });

    const auditB = await SELF.fetch(`${BASE}/firm/audit-trail`, { headers: { Cookie: cookieB } });
    const bodyB = (await auditB.json()) as { attestations: unknown[] };
    expect(bodyB.attestations.length).toBe(0);

    // Firm B also can't read firm A's license's attestations directly (404,
    // same ownership check every other per-license read uses).
    const directB = await SELF.fetch(`${BASE}/firm/licenses/${staffA.id}/attestations`, { headers: { Cookie: cookieB } });
    expect(directB.status).toBe(404);
  });
});
