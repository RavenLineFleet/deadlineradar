"""HDR-3 (AuditLab LOW, 2026-11-27): the security-header gate covered two of the
three origins. The assistant host is the third; these tests pin that it is in the
gate and that the gate fails for it in every way it fails for the other two. No
network: every case goes through the `fetch` injection seam.

    python -m pytest scripts/test_hdr3_assistant_header_layer.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import preship_gate as gate  # noqa: E402

ASSISTANT_URL = "https://assistant.deadline-radar.com/health"
LAYERS = {layer["name"]: layer for layer in gate._SECURITY_HEADER_LAYERS}
GOOD_BODY = {
    "static": b"<!doctype html><html></html>",
    "api": b'{"status":"ok","version":"abc123"}',
    "assistant": b'{"status":"ok","build":"6c82dc239195"}',
}


def _fetch(overrides=None):
    """A fake fetcher serving every layer its own expected headers and a good
    identity body, except where `overrides[url]` supplies (status, body, headers, err)."""
    overrides = overrides or {}

    def fetch(url):
        if url in overrides:
            return overrides[url]
        layer = next(l for l in gate._SECURITY_HEADER_LAYERS if l["url"] == url)
        # served in mixed case on purpose: the comparison must be case-insensitive on names
        headers = {k.title(): v for k, v in layer["expected"].items()}
        return 200, GOOD_BODY[layer["name"]], headers, None

    return fetch


def _assistant_headers(**changes):
    h = dict(LAYERS["assistant"]["expected"])
    for k, v in changes.items():
        if v is None:
            h.pop(k)
        else:
            h[k] = v
    return h


def test_the_gate_covers_all_three_origins():
    assert set(LAYERS) == {"static", "api", "assistant"}
    assert LAYERS["assistant"]["url"] == ASSISTANT_URL
    assert len({l["url"] for l in gate._SECURITY_HEADER_LAYERS}) == 3


def test_clean_when_every_layer_serves_its_expected_headers():
    assert gate.check_security_header_layers(fetch=_fetch()) == []


def test_assistant_layer_pins_the_six_headers_measured_2026_11_27():
    assert LAYERS["assistant"]["expected"] == {
        "x-content-type-options": "nosniff",
        "x-frame-options": "SAMEORIGIN",
        "content-security-policy": "frame-ancestors 'self'",
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "referrer-policy": "strict-origin-when-cross-origin",
        "x-robots-tag": "noindex, nofollow",
    }


@pytest.mark.parametrize("header", sorted(LAYERS["assistant"]["expected"]))
def test_each_assistant_header_fails_when_missing(header):
    errs = gate.check_security_header_layers(
        fetch=_fetch({ASSISTANT_URL: (200, GOOD_BODY["assistant"], _assistant_headers(**{header: None}), None)}))
    assert len(errs) == 1
    assert "assistant layer" in errs[0] and repr(header) in errs[0] and "got None" in errs[0]


@pytest.mark.parametrize("header", sorted(LAYERS["assistant"]["expected"]))
def test_each_assistant_header_fails_when_changed(header):
    errs = gate.check_security_header_layers(
        fetch=_fetch({ASSISTANT_URL: (200, GOOD_BODY["assistant"], _assistant_headers(**{header: "x"}), None)}))
    assert len(errs) == 1
    assert "assistant layer" in errs[0] and repr(header) in errs[0] and "drifted" in errs[0]


def test_unreachable_assistant_host_fails_and_is_not_reported_as_drift():
    errs = gate.check_security_header_layers(fetch=_fetch({ASSISTANT_URL: (None, b"", {}, "timed out")}))
    assert len(errs) == 1
    assert "could not verify headers for the assistant layer" in errs[0] and "drifted" not in errs[0]


@pytest.mark.parametrize("status,body", [
    (200, b'{"status":"ok","version":"abc123"}'),   # the Worker's health body: wrong origin
    (200, b'{"status":"ok","build":""}'),
    (200, b'{"status":"ok"}'),
    (200, b'{"status":"degraded","build":"6c82dc239195"}'),
    (200, b"<!doctype html><title>Just a moment...</title>"),
    (200, b"[]"),
    (403, b'{"status":"ok","build":"6c82dc239195"}'),
    (502, b""),
])
def test_wrong_identity_fails_before_any_header_is_trusted(status, body):
    # headers are perfect here on purpose: a block page can carry the right header set
    errs = gate.check_security_header_layers(
        fetch=_fetch({ASSISTANT_URL: (status, body, _assistant_headers(), None)}))
    assert len(errs) == 1
    assert "assistant layer" in errs[0] and "did not return identity" in errs[0]


def test_assistant_failure_does_not_mask_or_get_masked_by_another_layer():
    static_url = LAYERS["static"]["url"]
    errs = gate.check_security_header_layers(fetch=_fetch({
        static_url: (None, b"", {}, "dns"),
        ASSISTANT_URL: (200, GOOD_BODY["assistant"], _assistant_headers(**{"x-robots-tag": None}), None),
    }))
    assert len(errs) == 2
    assert any("static layer" in e for e in errs) and any("assistant layer" in e for e in errs)
