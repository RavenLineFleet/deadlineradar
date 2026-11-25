"""CITE-79: a cleartext citation URL is allowed only on a host recorded as TLS-less.

    python -m pytest scripts/test_cite79_cleartext_hosts.py -q
"""
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate  # noqa: E402

REPO = Path(HERE).parent


def _repo(tmp_path, body):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.json").write_text(body, encoding="utf-8")
    return tmp_path


def test_real_repo_clean():
    assert preship_gate.check_cleartext_citation_hosts(REPO) == []


def test_new_cleartext_host_fails(tmp_path):
    errs = preship_gate.check_cleartext_citation_hosts(
        _repo(tmp_path, '{"url": "http://commerce.utah.gov//wp-content/x.pdf"}'))
    assert len(errs) == 1 and "commerce.utah.gov" in errs[0]


def test_allowlisted_and_https_pass(tmp_path):
    body = '{"a": "http://reports.oah.state.nc.us/ncac/x.html", "b": "https://commerce.utah.gov/x.pdf"}'
    assert preship_gate.check_cleartext_citation_hosts(_repo(tmp_path, body)) == []


def test_utah_recipe_is_https():
    for f in ("data/reverify_recipes.json", "scripts/reverify/hand_spec.json"):
        s = (REPO / f).read_text(encoding="utf-8")
        assert "https://commerce.utah.gov/wp-content/uploads/2022/10/certified-public-accountant-reinstatement.pdf" in s
        assert "commerce.utah.gov//" not in s


def test_prose_embedded_cleartext_url_fails(tmp_path):
    # GATE-148: a URL inside prose (not a whole JSON value) is still a cleartext citation.
    body = '{"note": "statute at http://www.leg.state.fl.us/statutes/x.html. See also"}'
    errs = preship_gate.check_cleartext_citation_hosts(_repo(tmp_path, body))
    assert len(errs) == 1 and "http://www.leg.state.fl.us/statutes/x.html " in errs[0]


def test_verification_history_is_exempt(tmp_path):
    # GATE-148: verification_history records what a past check fetched; it is not rewritten.
    body = '{"records": [{"verification_history": ["2026-11-01: confirmed via http://commerce.utah.gov//x.pdf"]}]}'
    assert preship_gate.check_cleartext_citation_hosts(_repo(tmp_path, body)) == []


def test_real_repo_history_holds_the_exempted_urls():
    # Positive control for the exemption: the real tree DOES carry cleartext URLs in history,
    # so test_real_repo_clean passing is the exemption working, not the URLs being absent.
    s = (REPO / "data" / "reinstatement.json").read_text(encoding="utf-8")
    assert "http://commerce.utah.gov//" in s
