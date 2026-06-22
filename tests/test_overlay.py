"""Price selection, overlay drawing, and the analytics export report."""
from __future__ import annotations

import numpy as np

from packcapture.overlay import OverlayState, _build_report, draw_overlay
from packcapture.pipeline.session import Session
from packcapture.setbuild.prices import select_raw_price


def test_select_raw_price_prefers_normal_then_market():
    block = {
        "prices": {
            "normal": {"low": 0.01, "mid": 0.14, "market": 0.08},
            "reverseHolofoil": {"low": 0.01, "mid": 0.23, "market": 0.21},
        }
    }
    price, variant = select_raw_price(block)
    assert price == 0.08
    assert variant == "normal"


def test_select_raw_price_falls_back_to_foil_and_mid():
    # No normal printing; only a reverse holo with no market price.
    block = {"prices": {"reverseHolofoil": {"low": 0.0, "mid": 0.5}}}
    price, variant = select_raw_price(block)
    assert price == 0.5
    assert variant == "reverseHolofoil"


def test_select_raw_price_none_when_empty_or_zero():
    assert select_raw_price(None) == (None, None)
    assert select_raw_price({"prices": {}}) == (None, None)
    assert select_raw_price({"prices": {"normal": {"market": 0.0}}}) == (None, None)


def _price_map(pairs):
    return {cid: (price, "normal") for cid, price in pairs}


def test_build_report_totals_and_pack_values():
    session = Session("me2")
    session.add(card_id="a", name="Oddish", number="1", base_rarity="Common")
    session.add(card_id="b", name="Zacian", number="45", base_rarity="Rare")
    session.close_pack()
    session.add(card_id="c", name="Aipom", number="78", base_rarity="Common")
    session.finalize()

    price_map = _price_map([("a", 0.10), ("b", 1.40), ("c", 0.25)])
    report = _build_report(session, price_map, "me2", "Phantasmal Flames", "clip.mp4")

    assert report["totals"]["cards"] == 3
    assert report["totals"]["packs"] == 2
    assert report["totals"]["total_raw_value"] == 1.75
    assert report["packs"][0]["raw_value"] == 1.50
    assert report["packs"][1]["raw_value"] == 0.25
    # A rare+ in pack 1 makes it a speed-rip; the card carries its price through.
    assert report["packs"][0]["cards"][1]["price"] == 1.40


def test_build_report_handles_missing_price():
    session = Session("me2")
    session.add(card_id="x", name="NoPrice", number="9", base_rarity="Common")
    session.finalize()
    report = _build_report(session, {}, "me2", "Set", "clip.mp4")
    assert report["totals"]["total_raw_value"] == 0.0
    assert report["packs"][0]["cards"][0]["price"] is None


def test_draw_overlay_smoke():
    frame = np.zeros((720, 1280, 3), np.uint8)
    st = OverlayState(set_name="Phantasmal Flames", card_name="Mega Lopunny ex",
                      card_number="128", price=19.1, variant="holofoil", is_hit=True,
                      last_log_frame=0, total=23.62, count=8, packs=3,
                      by_status={"complete": 0, "speed_ripped": 2, "no_hit": 1})
    out = draw_overlay(frame, st, frame_idx=60, fps=30.0)
    assert out.shape == frame.shape
    # Something was drawn (overlays are not all-black).
    assert out.sum() > 0
    # The original frame is untouched (draw_overlay copies).
    assert frame.sum() == 0
