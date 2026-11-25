"""Public-address-only urlopen for the standalone fetch scripts (SSRF-3, AuditLab 2026-11-24).

source_check, check_citation_links, codified_source_audit, amendment_currency_check and
citation_auto_extend_check fetched dataset URLs with the default urllib opener: any scheme
(file:// included), redirects auto-followed to any host, loopback and private addresses allowed.
Three of them run unattended monthly from run_freshness_sweep.bat, and the sweep writes fetched
bodies to disk. This is the same policy as scripts/reverify/fetch.py's _PUBLIC_ONLY_OPENER:

  * the seed and every redirect target must be http or https;
  * every connection resolves once, refuses any non-globally-routable answer, and connects to the
    address it validated (a DNS answer cannot change between the check and the connect);
  * no file/ftp/data handlers and no proxy handler are installed at all.

Redirects are still followed (these scripts classify the final page), but each hop goes through
the same guarded connection, so a 302 onto 127.0.0.1 or a private host fails as BlockedAddress.

reverify/fetch.py keeps its own copy because its tests monkeypatch fetch._address_allowed; keep
the two _address_allowed bodies identical (scripts/test_net_guard.py pins that).
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


class BlockedAddress(OSError):
    """The host resolved to (or redirected onto) a non-public address."""


class BlockedScheme(OSError):
    """The URL (seed or redirect target) is not http/https."""


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _address_allowed(ip: str, port: int) -> bool:
    """Production policy: globally routable unicast only."""
    addr = ipaddress.ip_address(ip.split("%")[0])
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    elif addr.version == 6 and addr in _NAT64:
        addr = ipaddress.ip_address(int(addr) & 0xFFFFFFFF)   # 64:ff9b::7f00:1 embeds 127.0.0.1
    return addr.is_global and not addr.is_multicast


def _guarded_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for _fam, _typ, _proto, _canon, sa in infos:
        if not _address_allowed(sa[0], sa[1]):
            raise BlockedAddress(f"{host} resolves to non-public address {sa[0]}")
    last_exc: OSError | None = None
    for fam, typ, proto, _canon, sa in infos:
        sock = None
        try:
            sock = socket.socket(fam, typ, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            return sock
        except OSError as e:
            last_exc = e
            if sock is not None:
                sock.close()
    raise last_exc or OSError(f"no address for {host}")


class _GuardedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _guarded_create_connection


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _guarded_create_connection


class _GuardedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardedHTTPConnection, req)


class _GuardedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardedHTTPSConnection, req, context=self._context)


class _SchemeCheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme.lower() not in ALLOWED_SCHEMES:
            raise urllib.error.URLError(BlockedScheme(f"redirect to non-http(s) URL {newurl!r}"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener() -> urllib.request.OpenerDirector:
    # Built by hand: urllib.request.build_opener() always adds ProxyHandler, FileHandler,
    # FTPHandler and DataHandler, which is exactly the surface this module exists to remove.
    opener = urllib.request.OpenerDirector()
    for h in (urllib.request.UnknownHandler(), urllib.request.HTTPDefaultErrorHandler(),
              _SchemeCheckedRedirect(), urllib.request.HTTPErrorProcessor(),
              _GuardedHTTPHandler(), _GuardedHTTPSHandler()):
        opener.add_handler(h)
    return opener


_OPENER = _build_opener()


def urlopen(req, timeout: float):
    """Drop-in for urllib.request.urlopen(req, timeout=...) with the policy above."""
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    if urlparse(url).scheme.lower() not in ALLOWED_SCHEMES:
        raise urllib.error.URLError(BlockedScheme(f"non-http(s) URL {url!r}"))
    return _OPENER.open(req, timeout=timeout)


def is_blocked(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, (BlockedAddress, BlockedScheme)) or isinstance(reason, (BlockedAddress, BlockedScheme))
