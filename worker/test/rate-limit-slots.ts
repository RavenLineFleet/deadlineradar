import { env } from "cloudflare:test";

/**
 * Since SEC-70/71 a bogus id or malformed body spends no slot, so a test can no
 * longer reach a route's 429 by repeating a refusal. These put the bucket at its
 * ceiling directly; the test then proves the limit with a real request.
 */
export async function fillBucket(key: string, bucket: string, count: number): Promise<void> {
  await env.DB.prepare(
    `WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?3)
     INSERT INTO rate_limit_hits (ip, bucket, ts) SELECT ?1, ?2, ?4 FROM n`
  )
    .bind(key, bucket, count, Math.floor(Date.now() / 1000))
    .run();
}

export async function freeOneSlot(key: string, bucket: string): Promise<void> {
  await env.DB.prepare(
    "DELETE FROM rate_limit_hits WHERE rowid = (SELECT rowid FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2 LIMIT 1)"
  )
    .bind(key, bucket)
    .run();
}

export async function slotsUsed(key: string, bucket: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_limit_hits WHERE ip = ?1 AND bucket = ?2").bind(key, bucket).first<{ n: number }>();
  return row?.n ?? 0;
}
