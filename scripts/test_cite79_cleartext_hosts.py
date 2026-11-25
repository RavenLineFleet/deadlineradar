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
