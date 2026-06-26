"""Price selection, overlay drawing, the analytics export, and the engine."""
from __future__ import annotations

import numpy as np
import pytest

import cv2

from _synth import FakeClient, synth_card

from packcapture.overlay import (
    LayoutDrag,
    OverlayEngine,
    OverlayState,
    _build_report,
    draw_overlay,
    load_layout,
    save_layout,
)
from packcapture.pipeline.confidence import ConfidenceGate, GateConfig
from packcapture.pipeline.session import Session
from packcapture.recognize.orb_matcher import Matcher
from packcapture.setbuild.builder import build_set
from packcapture.setbuild.prices import select_raw_price
from packcapture.storage.bundle import load_bundle


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


def test_layout_round_trip(tmp_path, monkeypatch):
    import packcapture.overlay as ov

    monkeypatch.setattr(ov, "set_dir", lambda code: tmp_path / code)
    assert load_layout("me2") == (None, None)
    save_layout("me2", [10, 20], [30, 40])
    assert load_layout("me2") == ([10, 20], [30, 40])


def test_layout_drag_moves_panel_clamped():
    drag = LayoutDrag()
    drag.ensure(1280, 720)
    # Grab the analytics panel (bottom-right) by its top-left and drag off-screen.
    ax, ay = drag.analytics
    drag.on_mouse(cv2.EVENT_LBUTTONDOWN, ax + 2, ay + 2, 0, None)
    drag.on_mouse(cv2.EVENT_MOUSEMOVE, 100, 100, 0, None)
    assert drag.dirty
    assert drag.analytics[0] < ax and drag.analytics[1] < ay
    # Drag far past the right edge: origin clamps so the box stays in-frame.
    drag.on_mouse(cv2.EVENT_MOUSEMOVE, 5000, 5000, 0, None)
    drag.on_mouse(cv2.EVENT_LBUTTONUP, 5000, 5000, 0, None)
    assert 0 <= drag.analytics[0] <= 1280
    assert 0 <= drag.analytics[1] <= 720


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


# --- OverlayEngine (the recognition step shared by serial + threaded paths) ---

PACK_RARITIES = (
    ["Common"] * 4 + ["Uncommon"] * 3 + ["Common", "Uncommon"] + ["Double Rare"]
)


class _FullFrameROI:
    """ROI stub: box the whole frame, no motion -- isolates the logging logic
    from the MOG2 auto-ROI (which is exercised separately in test_roi)."""

    last_motion = 0.0

    def detect(self, frame):
        h, w = frame.shape[:2]
        return (0, 0, w, h)


class _Identity:
    def update(self, roi):
        return roi


@pytest.fixture()
def pack_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    build_set("fake", force=True, client=FakeClient(n=10, rarities=PACK_RARITIES))
    return load_bundle("fake")


def _engine(bundle, **kw):
    eng = OverlayEngine(
        Matcher(bundle),
        # Low floor: synthetic descriptors are dense; keep the gate logic
        # exercised without depending on exact synthetic inlier counts.
        ConfidenceGate(GateConfig(min_inliers=10, margin_ratio=1.2, noise_floor=5)),
        Session("fake"),
        {f"fake-{i}": (round(0.10 * (i + 1), 2), "normal") for i in range(10)},
        OverlayState(set_name="Fake"),
        boundary_fps=3.0, stable_frames=2, evidence_inliers=5, **kw,
    )
    eng.roi_detector = _FullFrameROI()
    eng.smoother = _Identity()
    return eng


def test_engine_logs_cards_from_recognition(pack_bundle):
    eng = _engine(pack_bundle)
    clock = 0
    for i in range(10):
        img = synth_card(i + 1)
        for _ in range(eng.stable_frames):  # hold each card long enough to log
            clock += 1
            eng.process(img, lambda: clock)
    assert eng.st.count == 10
    assert round(eng.st.total, 2) == round(sum(0.10 * (i + 1) for i in range(10)), 2)
    pack = eng.session.close_pack()
    assert [c.card_id for c in pack.cards] == [f"fake-{i}" for i in range(10)]


def test_engine_clock_drives_last_log_frame(pack_bundle):
    # last_log_frame must be the clock value at the logging tick, so the ticker
    # slide animation reads correctly whatever drives the clock.
    eng = _engine(pack_bundle)
    img = synth_card(1)
    eng.process(img, lambda: 100)     # cur_n = 1
    eng.process(img, lambda: 12345)   # cur_n = 2 == stable_frames -> logs, clock sampled now
    assert eng.st.count == 1
    assert eng.st.last_log_frame == 12345


def test_engine_snapshot_is_independent_copy(pack_bundle):
    eng = _engine(pack_bundle)
    snap = eng.snapshot()
    assert snap is not eng.st
    eng.st.count = 99
    assert snap.count != 99  # snapshot is a point-in-time copy


def test_engine_does_not_double_log_a_held_card(pack_bundle):
    # A card left in frame for a long time must log exactly once.
    eng = _engine(pack_bundle)
    img = synth_card(1)
    clock = 0
    for _ in range(40):
        clock += 1
        eng.process(img, lambda: clock)
    assert eng.st.count == 1
    assert [c.card_id for c in eng.session._current] == ["fake-0"]  # synth_card(1) -> fake-0


def test_engine_dedupes_held_card_across_stray_recognition(pack_bundle):
    # The reported bug: a long hold of card 1 with a brief different card 2 in
    # the middle must not let card 1 log twice (a single "last id" guard would).
    eng = _engine(pack_bundle)
    a, b = synth_card(1), synth_card(2)
    clock = 0

    def step(img):
        nonlocal clock
        clock += 1
        eng.process(img, lambda: clock)

    for _ in range(3):
        step(a)            # logs fake-1
    for _ in range(2):
        step(b)            # logs fake-2 (different card flips the old guard)
    for _ in range(5):
        step(a)            # card 1 back -> must NOT re-log
    assert [c.card_id for c in eng.session._current] == ["fake-0", "fake-1"]


def test_engine_can_relog_a_card_after_delete(pack_bundle):
    # Deleting a card frees its id from the per-pack dedupe set so it can be
    # re-scanned (e.g. after fixing a mis-scan).
    eng = _engine(pack_bundle)
    img = synth_card(1)
    for _ in range(2):
        eng.process(img, lambda: 0)
    assert eng.st.count == 1
    assert eng.remove_card(0) is True
    assert eng.st.count == 0
    eng._cur_id, eng._cur_n = None, 0          # card left and came back
    for _ in range(2):
        eng.process(img, lambda: 0)
    assert eng.st.count == 1                    # re-logged
