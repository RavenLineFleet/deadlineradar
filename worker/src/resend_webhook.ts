/**
 * Resend webhook signature verification (Svix-signed). Replaces the old
 * SendGrid Event Webhook (roadmap #55) -- Orchestrator directive
 * (2026-09-23, Devin: "Replace SendGrid completely").
 *
 * Resend delivers webhooks through Svix, which signs each delivery with
 * HMAC-SHA256 over `{svix-id}.{svix-timestamp}.{raw body}`, using the
 * webhook's signing secret (a `whsec_<base64>` string -- everything after
 * the `whsec_` prefix IS the base64-encoded raw HMAC key, not itself
 * something to decode further). The result is base64-encoded and sent as
 * one or more space-separated `v1,<signature>` tokens in the
 * svix-signature header (more than one during a secret-rotation window;
 * any single match verifies). A timestamp tolerance (5 minutes, Svix's
 * own documented default) rejects a replayed delivery outside that
 * window even with an otherwise-valid signature -- the old SendGrid
 * verifier had NO such window (see the LOAD-BEARING comment on the
 * sg_event_id dedup check this replaces in index.ts); this closes that
 * gap rather than reproducing it.
 */

function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/** Constant-time string comparison -- a signature check that short-circuits
 * on the first mismatched byte leaks timing information an attacker could
 * use to forge a valid signature one byte at a time. Same discipline as
 * every other secret-comparison in this codebase (see password.ts). */
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * Verifies a Resend/Svix webhook delivery. `rawBody` must be the exact
 * bytes as received (a round-trip through JSON.parse/JSON.stringify can
 * change whitespace/key order and silently break the signature -- same
 * "raw bytes" caution the old SendGrid verifier required). Never throws;
 * any malformed input (missing headers, bad base64, an unparseable
 * secret) fails closed, returning false.
 */
export async function verifyResendEventSignature(
  webhookSecret: string,
  svixId: string | null,
  svixTimestamp: string | null,
  svixSignature: string | null,
  rawBody: string,
  toleranceSeconds = 300,
  nowSeconds: number = Math.floor(Date.now() / 1000)
): Promise<boolean> {
  if (!svixId || !svixTimestamp || !svixSignature) return false;

  const ts = Number.parseInt(svixTimestamp, 10);
  if (!Number.isFinite(ts)) return false;
  if (Math.abs(nowSeconds - ts) > toleranceSeconds) return false;

  const secretB64 = webhookSecret.startsWith("whsec_") ? webhookSecret.slice("whsec_".length) : webhookSecret;

  try {
    const keyBytes = fromBase64(secretB64);
    const key = await crypto.subtle.importKey(
      "raw",
      keyBytes as BufferSource,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );
    const signedContent = `${svixId}.${svixTimestamp}.${rawBody}`;
    const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(signedContent) as BufferSource);
    const expected = toBase64(new Uint8Array(mac));

    // Multiple space-separated "v1,<sig>" tokens during a secret rotation
    // window -- any single match verifies.
    for (const token of svixSignature.split(" ")) {
      const [version, sig] = token.split(",");
      if (version !== "v1" || !sig) continue;
      if (constantTimeEqual(sig, expected)) return true;
    }
    return false;
  } catch {
    return false;
  }
}
