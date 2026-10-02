#!/usr/bin/env python3
"""Canonical payload hashing for the blog two-party approval gate (BLOG-1,
AuditLab spec, 2026-10-02T14:41 MDT).

BLOG-1 happened because an approval that genuinely existed, for genuinely
approved content, got applied to a draft that had grown a new section after
the approval was given -- the review and the shipped bytes silently
diverged. This module exists so that never happens invisibly again: both
the reviewer's draft and generate.py's BLOG_ARTICLES entry hash to the same
digest when they carry the same six published fields, and a change to
either one changes the digest.

Canonicalization is deliberately minimal -- CRLF to LF, and a single
leading/trailing newline stripped from body_html only (the artifact of a
triple-quoted literal in generate.py, absent from a draft file). Nothing
else: no whitespace collapsing, no case folding, no entity normalization.
Every extra normalization step is a bypass class -- whatever it erases
becomes a change that ships without re-approval, which is exactly the
failure this exists to close.

Usage:
    python3 scripts/blog_payload_hash.py --draft path/to/draft.md
    python3 scripts/blog_payload_hash.py --slug some-blog-slug
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

PAYLOAD_FIELDS = ("slug", "published", "title", "seo_title", "meta_description", "body_html")

_FRONTMATTER_FIELD_RE = {
    "slug": re.compile(r"^slug:\s?(.*)$"),
    "published": re.compile(r"^published:\s?(.*)$"),
    "title": re.compile(r"^title:\s?(.*)$"),
    "seo_title": re.compile(r"^seo_title:\s?(.*)$"),
    "meta_description": re.compile(r"^meta_description:\s?(.*)$"),
}
_REQUIRED_DRAFT_FIELDS = ("slug", "published", "title", "meta_description")
_BODY_HTML_MARKER = "\nBODY_HTML:\n"


def _canon_text(s: str) -> str:
    return s.replace("\r\n", "\n")


def _canon_body_html(s: str) -> str:
    """CRLF->LF, then strip exactly one leading and one trailing newline if
    present (conditional, not str.strip() -- a draft's extracted body may
    not carry either, while generate.py's triple-quoted literal carries
    both; this is what makes the two extractions converge)."""
    s = _canon_text(s)
    if s.startswith("\n"):
        s = s[1:]
    if s.endswith("\n"):
        s = s[:-1]
    return s


def canonical_payload_bytes(
    slug: str, published: str, title: str, seo_title: str, meta_description: str, body_html: str
) -> bytes:
    fields = [
        _canon_text(slug),
        _canon_text(published),
        _canon_text(title),
        _canon_text(seo_title),
        _canon_text(meta_description),
        _canon_body_html(body_html),
    ]
    return b"\x00".join(f.encode("utf-8") for f in fields)


def payload_sha256(
    slug: str, published: str, title: str, seo_title: str, meta_description: str, body_html: str
) -> str:
    return hashlib.sha256(
        canonical_payload_bytes(slug, published, title, seo_title, meta_description, body_html)
    ).hexdigest()


def payload_sha256_from_fields(fields: dict) -> str:
    return payload_sha256(
        fields["slug"],
        fields["published"],
        fields["title"],
        fields.get("seo_title", ""),
        fields["meta_description"],
        fields["body_html"],
    )


def extract_from_draft(path: pathlib.Path) -> dict:
    """Parse an AssetLab blog-draft file the way AuditLab reviews it:
    frontmatter keys up to the SOURCING block (ignored entirely -- marked
    "for reviewers, not published"), then everything after the BODY_HTML:
    line, verbatim, is body_html."""
    text = path.read_text(encoding="utf-8")
    text = _canon_text(text)

    idx = text.find(_BODY_HTML_MARKER)
    if idx == -1:
        raise ValueError(f"{path}: no 'BODY_HTML:' line found")
    head = text[:idx]
    body_html = text[idx + len(_BODY_HTML_MARKER):]

    fields: dict[str, str] = {"seo_title": ""}
    for line in head.splitlines():
        if line.startswith("SOURCING"):
            break
        for key, pat in _FRONTMATTER_FIELD_RE.items():
            m = pat.match(line)
            if m:
                fields[key] = m.group(1)
                break

    missing = [f for f in _REQUIRED_DRAFT_FIELDS if f not in fields]
    if missing:
        raise ValueError(f"{path}: missing required frontmatter field(s): {missing}")

    fields["body_html"] = body_html
    return fields


def extract_from_slug(slug: str, repo_root: pathlib.Path = REPO_ROOT) -> dict:
    """Read the live BLOG_ARTICLES entry the way generate.py actually ships
    it -- this is the shipped-bytes side of the gate, not a copy of them."""
    sys.path.insert(0, str(repo_root))
    import generate as generate_module  # noqa: E402

    matches = [a for a in generate_module.BLOG_ARTICLES if a["slug"] == slug]
    if not matches:
        raise ValueError(f"no BLOG_ARTICLES entry for slug {slug!r}")
    if len(matches) > 1:
        raise ValueError(f"multiple BLOG_ARTICLES entries for slug {slug!r}")
    a = matches[0]
    return {
        "slug": a["slug"],
        "published": a["published"],
        "title": a["title"],
        "seo_title": a.get("seo_title", ""),
        "meta_description": a["meta_description"],
        "body_html": a["body_html"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--draft", type=pathlib.Path, help="path to an AssetLab blog draft file")
    group.add_argument("--slug", type=str, help="slug to look up in generate.py's BLOG_ARTICLES")
    args = parser.parse_args()

    fields = extract_from_draft(args.draft) if args.draft else extract_from_slug(args.slug)
    print(payload_sha256_from_fields(fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
