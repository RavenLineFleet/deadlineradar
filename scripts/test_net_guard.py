"""SSRF-3: the five standalone fetchers refuse loopback/private hosts, cross-host redirects onto them,
and non-http(s) URLs. A real loopback server is the target; every arm must fail closed.

    python -m pytest scripts/test_net_guard.py -q
"""
import http.server
import os
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import net_guard  # noqa: E402

MARKER = b"INTERNAL-ONLY-ssrf3-marker"


@pytest.fixture(scope="module")
def loopback():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/internal")
                self.end_headers()
                return
            if self.path == "/xredir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.2:{self.server.server_port}/internal")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>" + MARKER + b"</html>")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _guarded_open(url):
    return net_guard.urlopen(urllib.request.Request(url), timeout=5)


@pytest.mark.parametrize("path", ["/internal", "/redir", "/xredir"])
def test_net_guard_refuses_loopback_and_redirects(loopback, path):
    with pytest.raises(urllib.error.URLError) as ei:
        _guarded_open(loopback + path)
    assert net_guard.is_blocked(ei.value)


def test_policy_hook_is_the_only_thing_refusing(loopback, monkeypatch):
    # positive control: with the policy opened up, the same server IS reachable through the same opener
    monkeypatch.setattr(net_guard, "_address_allowed", lambda ip, port: True)
    with _guarded_open(loopback + "/redir") as r:
        assert MARKER in r.read()


@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "ftp://example.com/x", "data:text/plain,hi"])
def test_net_guard_refuses_non_http_seed(url):
    with pytest.raises(urllib.error.URLError) as ei:
        _guarded_open(url)
    assert net_guard.is_blocked(ei.value)


def test_redirect_to_file_scheme_refused():
    h = net_guard._SchemeCheckedRedirect()
    req = urllib.request.Request("https://example.com/")
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(req, None, 302, "Found", {}, "file:///etc/passwd")


def test_address_policy_table():
    a = net_guard._address_allowed
    assert not a("127.0.0.1", 80) and not a("169.254.169.254", 80) and not a("10.0.0.5", 80)
    assert not a("::1", 80) and not a("::ffff:127.0.0.1", 80) and not a("64:ff9b::7f00:1", 80)
    assert a("23.1.1.1", 80)


def test_source_check_fetch_is_guarded(loopback):
    import source_check
    for path in ("/internal", "/redir", "/xredir"):
        cls, body, _ct, _st = source_check._fetch(loopback + path)
        assert cls == "BLOCKED" and not (body and MARKER in body), path


def test_check_citation_links_fetch_is_guarded(loopback):
    import check_citation_links
    for path in ("/internal", "/redir", "/xredir"):
        status, body, _d = check_citation_links._fetch(loopback + path)
        assert status is None and body is None, path


def test_citation_auto_extend_fetch_is_guarded(loopback):
    import citation_auto_extend_check
    r = citation_auto_extend_check._real_fetch(loopback + "/redir")
    assert not r.ok and not (r.body and MARKER in r.body)


def test_amendment_currency_fetch_is_guarded(loopback):
    import amendment_currency_check
    assert amendment_currency_check._fetch_text(loopback + "/redir") is None


def test_no_raw_urlopen_left_in_the_five():
    for name in ("source_check", "check_citation_links", "codified_source_audit",
                 "amendment_currency_check", "citation_auto_extend_check"):
        src = open(os.path.join(HERE, name + ".py"), encoding="utf-8").read()
        assert "urllib.request.urlopen(" not in src, name
        assert not re.search(r"^from urllib\.request import[^\n]*\burlopen\b", src, re.M), name
        assert "net_guard" in src, name


def test_address_allowed_matches_reverify_copy():
    import inspect
    sys.path.insert(0, os.path.join(HERE, "reverify"))
    import fetch as rv_fetch
    body = lambda f: inspect.getsource(f).split('"""', 2)[2]  # noqa: E731
    assert body(net_guard._address_allowed) == body(rv_fetch._address_allowed)
