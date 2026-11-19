"""SRC-278: the /<state>-cpa-cpe-carryover/ page states the separate annual
CPE floor wherever the CPE-requirements page does, and nowhere else.

    python -m pytest scripts/test_src278_carryover_annual_floor.py -q
"""
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import generate  # noqa: E402

AS_OF = date(2026, 11, 19)
FLOOR_HEADING = "A separate rule: a minimum every year"
REQ_FLOOR_PHRASE = "-hour minimum</strong> in each 1-year"

RULES = generate.load_renewal_rules_by_slug()
CPE = generate.load_cpe_hours_by_slug()


def _carryover_html(slug, cpe_record):
    _, _, html = generate.build_cpe_carryover_page(RULES[slug], [], cpe_record, AS_OF)
    return html


def test_live_partition_matches_auditlab():
    # AuditLab's harm partition: CA + TX carry a binding floor; CO has none,
    # MO/NC have a 1-year cycle whose minimum equals the total.
    with_floor = {s for s in RULES if FLOOR_HEADING in _carryover_html(s, CPE.get(s))}
    assert with_floor == {"california", "texas"} & set(RULES)
    assert {"california", "texas"} <= set(RULES)


def test_tx_sentence_has_the_real_numbers():
    html = _carryover_html("texas", CPE["texas"])
    assert ("requires a <strong>20-hour minimum in each 1-year period</strong> of its "
            "3-year cycle, so you can't front-load the whole 120-hour requirement") in html
    assert 'href="../texas-cpa-cpe-requirements/"' in html


def test_no_cpe_record_renders_nothing():
    assert FLOOR_HEADING not in _carryover_html("texas", None)


def test_alternative_track_is_not_a_floor():
    # CITE-52 (Arkansas shape): annual_minimum is one leg of a choice, not a floor.
    rec = dict(CPE["texas"], annual_minimum_hours=40, annual_minimum_basis="alternative_track")
    assert FLOOR_HEADING not in _carryover_html("texas", rec)


def test_parity_with_requirements_page_for_every_state():
    # The forward blast radius: when renewal_rules.json grows past the pilot,
    # every carryover page must agree with its requirements page.
    for slug, cpe_record in CPE.items():
        _, _, req_html = generate.build_cpe_hours_page(cpe_record, [], AS_OF, {})
        on_req = REQ_FLOOR_PHRASE in req_html
        on_carry = bool(generate._carryover_annual_floor_html(slug, cpe_record["state"], cpe_record))
        assert on_req == on_carry, slug


def test_parity_census_is_not_vacuous():
    floors = [s for s, r in CPE.items() if generate._cpe_binding_annual_floor(r)]
    assert len(floors) >= 20
    assert "arkansas" not in floors and "missouri" not in floors and "colorado" not in floors
