"""FOUNDING-CLOSE (Orchestrator 2026-11-18): when 5 of 5 founding slots are used and generate.py still
advertises the offer, `founding_firm_grant.py status` must drop a directive into the inboxes and exit
non-zero -- a trigger nobody has to remember.

    python -m pytest scripts/test_founding_close_trigger.py -q
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import founding_firm_grant as ffg  # noqa: E402


def _grants(n):
    return [
        {"slot": i, "firm_id": f"firm{i}xx", "granted_at": f"2026-12-0{i}T10:00:00.000Z",
         "verified_by": "devin", "trial_started_at": None, "stripe_subscription_id": None}
        for i in range(1, n + 1)
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    inboxes = (tmp_path / "assetlab_inbox", tmp_path / "orch_inbox")
    for d in inboxes:
        d.mkdir()
    monkeypatch.setattr(ffg, "FULL_NOTICE_INBOXES", inboxes)
    state = {"n": 5, "open": True}
    monkeypatch.setattr(ffg, "site_flag_open", lambda *a, **k: state["open"])

    def fake_rows(sql, local, read=False):
        return _grants(state["n"]) if "FROM founding_firm_grants" in sql else []

    monkeypatch.setattr(ffg, "rows", fake_rows)
    return inboxes, state


def _status(local=False):
    ffg.cmd_status(types.SimpleNamespace(local=local))


def _files(inboxes):
    return sorted(p.name for d in inboxes for p in d.iterdir())


def test_full_and_open_writes_both_inboxes_and_exits_nonzero(env):
    inboxes, _ = env
    with pytest.raises(SystemExit) as ei:
        _status()
    assert ei.value.code == ffg.FULL_STATUS_EXIT != 0
    names = _files(inboxes)
    assert len(names) == 2 and names[0] == names[1]
    assert "20261205T100000Z" in names[0]
    body = (inboxes[0] / names[0]).read_text(encoding="utf-8")
    assert body.startswith("---\nfrom: founding_firm_grant.py\n")
    assert "kind: directive\nreply_required: yes\n" in body
    assert "FOUNDING_FIRMS_OPEN = False" in body


def test_rerun_is_idempotent(env):
    inboxes, _ = env
    for _ in range(3):
        with pytest.raises(SystemExit):
            _status()
    assert len(_files(inboxes)) == 2


def test_four_of_five_is_silent(env):
    inboxes, state = env
    state["n"] = 4
    _status()  # no SystemExit
    assert _files(inboxes) == []


def test_full_but_flag_already_false_is_silent(env):
    inboxes, state = env
    state["open"] = False
    _status()
    assert _files(inboxes) == []


def test_local_exits_but_never_writes(env):
    inboxes, _ = env
    with pytest.raises(SystemExit):
        _status(local=True)
    assert _files(inboxes) == []


def test_missing_inbox_is_skipped(tmp_path):
    written = ffg.write_full_notices("2026-12-05T10:00:00.000Z", (tmp_path / "nope", tmp_path))
    assert written == [tmp_path / ffg.full_notice_name("2026-12-05T10:00:00.000Z")]


@pytest.mark.parametrize("text,expected", [
    ("x = 1\nFOUNDING_FIRMS_OPEN = True\n", True),
    ("x = 1\nFOUNDING_FIRMS_OPEN = False\n", False),
    ("x = 1\n", True),  # unreadable/ambiguous fails toward notifying
    ("FOUNDING_FIRMS_OPEN = False\nFOUNDING_FIRMS_OPEN = True\n", True),
])
def test_site_flag_open_parse(tmp_path, text, expected):
    (tmp_path / "generate.py").write_text(text, encoding="utf-8")
    assert ffg.site_flag_open(tmp_path) is expected


def test_site_flag_open_missing_file(tmp_path):
    assert ffg.site_flag_open(tmp_path) is True


def test_real_generate_py_flag_is_readable():
    # Positive control: the regex really finds the shipped assignment (today: open).
    text = (ffg.REPO_ROOT / "generate.py").read_text(encoding="utf-8")
    assert len(ffg.FLAG_RE.findall(text)) == 1


def test_grant_of_slot_5_fires_the_notice(env, monkeypatch):
    # The trigger must ride the grant itself: `grant --apply` re-runs status after the INSERT lands.
    inboxes, state = env
    state["n"] = 4
    monkeypatch.setattr(ffg, "d1", lambda sql, local: state.update(n=5))

    def fake_rows(sql, local, read=False):
        if "WHERE firm_id =" in sql:
            return [{"slot": 5}]
        return _grants(state["n"]) if "FROM founding_firm_grants" in sql else []

    monkeypatch.setattr(ffg, "rows", fake_rows)
    args = types.SimpleNamespace(local=False, apply=True, firm_id="Abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
                                 verified_by=ffg.VERIFIED_BY[0], evidence="checked state board roster and website")
    monkeypatch.setattr(ffg, "check_firm_id", lambda _id: None)
    with pytest.raises(SystemExit) as ei:
        ffg.cmd_grant(args)
    assert ei.value.code == ffg.FULL_STATUS_EXIT
    assert len(_files(inboxes)) == 2
