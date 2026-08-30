"""Set ASSISTANT_DROPLET_SHARED_SECRET and verify it matches the droplet, in one step.

AuditLab (2026-08-30, following the 2026-08-29 outage): a secret set independently on both the
Worker and the droplet has no graceful degradation path by design -- a mismatch hard-fails every
real question with a 502 until someone notices. That is exactly what happened the first time this
secret was rotated: both sides set a value, neither compared notes, and the widget was down
site-wide until the values were resynced.

This script closes that gap for every FUTURE rotation: it curls the droplet DIRECTLY with the
candidate value FIRST, and only sets the wrangler secret if that confirms a match. It cannot verify
a value already deployed (wrangler secrets aren't readable back once set -- by design, and this
script doesn't try to work around that) -- it only prevents a NEW mismatch from shipping silently.

AuditLab (2026-08-30): verify-before-set, not set-then-verify -- the point of this tool is "don't let
a secret change break the widget," so it must not itself be able to break a currently-working widget
on a typo'd candidate. Verifying first makes that failure mode "nothing changed" instead of "widget
now broken, only found out after."

Verification is free: POST /chat with an empty body and the secret gets validated before any LLM
call. A matching secret returns 400 "empty message" (reached app validation). A wrong or missing
secret returns 401 (rejected at the auth layer, per ShopLab's fix). Never sends a real question, so
running this costs nothing even if run repeatedly.

Usage:
    echo -n "<value>" | python3 scripts/set_and_verify_assistant_secret.py

Reads the secret from stdin (not argv, so it never appears in shell history or `ps`). Requires
wrangler to be authenticated for this Worker (same precondition as any other `wrangler secret put`).
"""
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

DROPLET_CHAT_URL = "https://deadlineradar-assistant.143-198-52-110.nip.io/chat"
ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = ROOT / "worker"


def exe(name: str) -> str:
    """Resolve to a real executable path -- see deploy_worker.py's own exe()
    docstring: subprocess.run() without shell=True does not consult PATHEXT
    on Windows, so a bare "npx" raises FileNotFoundError there."""
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"Could not find '{name}' on PATH. Is Node installed?")
    return found


def verify_against_droplet(secret: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        DROPLET_CHAT_URL,
        data=b"{}",
        headers={"Content-Type": "application/json", "X-Assistant-Shared-Secret": secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(500).decode("utf-8", errors="replace")
            return False, f"unexpected 200 for an empty body -- droplet behavior changed, investigate: {body}"
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8", errors="replace")
        if e.code == 400 and "empty message" in body.lower():
            return True, "droplet accepted the secret (400 empty message == reached app validation)"
        if e.code == 401:
            return False, f"droplet REJECTED the secret (401): {body}"
        return False, f"unexpected status {e.code}: {body}"
    except Exception as e:
        return False, f"could not reach the droplet to verify ({type(e).__name__}: {e})"


def main() -> int:
    secret = sys.stdin.readline().rstrip("\n")
    if not secret:
        print("ERROR: no secret provided on stdin. Usage: echo -n '<value>' | python3 scripts/set_and_verify_assistant_secret.py", file=sys.stderr)
        return 1

    print("Verifying the candidate against the droplet directly, BEFORE touching the Worker's secret (0 LLM cost)...")
    ok, detail = verify_against_droplet(secret)
    if not ok:
        print(f"MISMATCH OR ERROR: {detail}", file=sys.stderr)
        print(
            "The candidate does NOT match what the droplet currently requires. Nothing was changed on "
            "the Worker side -- if the widget was working before this run, it still is.",
            file=sys.stderr,
        )
        return 1
    print(f"MATCH CONFIRMED: {detail}")

    print("Setting wrangler secret ASSISTANT_DROPLET_SHARED_SECRET...")
    proc = subprocess.run(
        [exe("npx"), "wrangler", "secret", "put", "ASSISTANT_DROPLET_SHARED_SECRET"],
        input=secret,
        text=True,
        cwd=WORKER_DIR,
        capture_output=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print("ERROR: the candidate matches the droplet, but wrangler secret put FAILED:", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        print("The Worker's secret was NOT updated -- rerun once wrangler is working.", file=sys.stderr)
        return 1

    print("Wrangler secret set. Rotation complete -- one real question through the live widget is still worth a manual check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
