"""SRC-36 (AuditLab, 2026-10-12): the link checker must see what a browser sees.

adminrules.utah.gov/api/public/getfile/ content-negotiates on Accept: a browser
navigation gets a ~2KB React shell (blank page), a client sending NO Accept gets
the PDF. The checker used to send no Accept, so it was green on a link no reader
could open. These tests pin BOTH directions against a local server that behaves
the same way, so reverting the header edit goes red.
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_citation_links as ccl  # noqa: E402

PDF = b"%PDF-1.4\n" + b"x" * 5000
SHELL = (b'<!doctype html><html><head><title>Utah Office of Administrative Rules</title></head>'
         b'<body><div id="root"></div><script src="/static/app.js"></script></body></html>')


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/getfile"):
            if self.headers.get("Accept"):
                body, ctype = SHELL, "text/html"
            else:
                body, ctype = PDF, "application/pdf"
        elif self.path.startswith("/real-page"):
            body, ctype = b"<html><title>R156-1</title><body>" + b"rule text " * 600 + b"</body></html>", "text/html"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_checker_sends_a_browser_accept_header():
    assert ccl.REQUEST_HEADERS.get("Accept", "").startswith("text/html")
    assert ccl.REQUEST_HEADERS.get("Sec-Fetch-Dest") == "document"


def test_fetch_gets_the_browser_view_not_the_no_accept_view():
    srv, base = _serve()
    try:
        status, body, _ = ccl._fetch(f"{base}/getfile/x.pdf")
    finally:
        srv.shutdown()
    assert status == 200
    assert body == SHELL, "checker saw the no-Accept PDF -- it is not fetching like a browser"


def test_accept_negotiated_shell_is_not_called_live():
    srv, base = _serve()
    try:
        status, body, _ = ccl._fetch(f"{base}/getfile/x.pdf")
        sig_status, sig_body, _ = ccl._fetch(f"{base}/nope-control/")  # 404 baseline
    finally:
        srv.shutdown()
    verdict, reason = ccl.classify(status, body, sig_status, sig_body, "Utah Admin. Code R156-1-308a", f"{base}/getfile/x.pdf")
    assert verdict == "UNVERIFIABLE", (verdict, reason)
    assert "empty JS mount node" in reason


def test_pdf_and_real_pages_are_not_flagged_as_shells():
    assert not ccl._looks_like_empty_spa_shell(PDF)
    assert not ccl._looks_like_empty_spa_shell(b"<html><title>R156-1</title><body>" + b"rule text " * 600 + b"</body></html>")
    assert not ccl._looks_like_empty_spa_shell(None)


def test_shell_signatures_are_detected():
    assert ccl._looks_like_empty_spa_shell(SHELL)
    assert ccl._looks_like_empty_spa_shell(b"<div id='app'>\n</div>")
    assert ccl._looks_like_empty_spa_shell(b"<noscript>You need to enable JavaScript to run this app.</noscript>")
    assert not ccl._looks_like_empty_spa_shell(b'<div id="root"><p>real content</p></div>')


def test_browser_verified_entry_clears_then_ages_out(monkeypatch):
    # SRC-59 (AuditLab, 2026-10-18): this used to assert against the real
    # production entries for adminrules.utah.gov, which is wrong on two
    # counts -- a mechanism test shouldn't depend on live data staying true,
    # and those specific entries were removed because the platform they
    # verified a human could read has since 404'd entirely. Synthetic entry,
    # tests the same aging mechanism without claiming anything about a real
    # URL's current status.
    import datetime as dt
    u = "https://example.gov/some-rule-page"
    monkeypatch.setitem(ccl.BROWSER_VERIFIED, u, ("2026-10-13", "real Chrome, renders the cited text"))
    assert "human-verified" in ccl.browser_verified_note(u, today=dt.date(2026, 10, 13))
    assert ccl.browser_verified_note(u, today=dt.date(2027, 4, 20)) is None  # >180d: back to the queue
    assert ccl.browser_verified_note("https://example.gov/never-recorded.pdf", today=dt.date(2026, 10, 13)) is None
