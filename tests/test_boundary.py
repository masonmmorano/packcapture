"""Boundary detector: visual card-present/absent segmentation with hysteresis."""
from __future__ import annotations

from packcapture.pipeline.boundary import (
    DETECTING_PACK,
    PACK_END,
    PACK_START,
    WAITING_FOR_PACK,
    BoundaryConfig,
    BoundaryDetector,
)

# 10 fps test config: absent hysteresis = 2.5s -> 25 frames,
# burst accelerator after 1s (10 frames) of absence.
CFG = BoundaryConfig(fps=10.0)


def _run(det, seq):
    """Feed (card_seen, motion) pairs; return [(frame_idx, event), ...]."""
    events = []
    for i, (seen, motion) in enumerate(seq):
        ev = det.update(seen, motion)
        if ev:
            events.append((i, ev))
    return events


def test_pack_start_needs_sustained_evidence():
    det = BoundaryDetector(CFG)
    # One spurious match during idle must not open a pack.
    assert det.update(True) is None
    for _ in range(20):
        assert det.update(False) is None
    assert det.state == WAITING_FOR_PACK
    # Three matches inside the window do.
    seq = [(True, 0.0), (False, 0.0), (True, 0.0), (True, 0.0)]
    events = _run(det, seq)
    assert events == [(3, PACK_START)]
    assert det.state == DETECTING_PACK


def test_within_pack_pause_does_not_cut():
    det = BoundaryDetector(CFG)
    _run(det, [(True, 0.0)] * 3)  # opens the pack
    # A 1.5s pause (15 frames < 25-frame hysteresis) then the next card.
    events = _run(det, [(False, 0.0)] * 15 + [(True, 0.0)])
    assert events == []
    assert det.state == DETECTING_PACK


def test_sustained_absence_cuts_pack():
    det = BoundaryDetector(CFG)
    _run(det, [(True, 0.0)] * 3)
    events = _run(det, [(False, 0.0)] * 30)
    assert events == [(24, PACK_END)]  # 25th absent frame (2.5s at 10fps)
    assert det.state == WAITING_FOR_PACK


def test_motion_burst_cuts_early():
    det = BoundaryDetector(CFG)
    _run(det, [(True, 0.0)] * 3)
    # Cards gone for 1s (10 frames), then the next wrapper tear (big motion):
    # cut on the burst instead of waiting out the full 2.5s.
    seq = [(False, 0.0)] * 10 + [(False, 0.5)]
    events = _run(det, seq)
    assert events == [(10, PACK_END)]


def test_full_two_pack_cycle():
    det = BoundaryDetector(CFG)
    seq = (
        [(False, 0.0)] * 5            # idle
        + [(True, 0.0)] * 20          # pack 1 cards
        + [(False, 0.1)] * 30         # set down + grab next (gap > hysteresis)
        + [(True, 0.0)] * 20          # pack 2 cards
        + [(False, 0.0)] * 30         # session tail
    )
    events = _run(det, seq)
    kinds = [e for _, e in events]
    assert kinds == [PACK_START, PACK_END, PACK_START, PACK_END]


def test_reset():
    det = BoundaryDetector(CFG)
    _run(det, [(True, 0.0)] * 3)
    det.reset()
    assert det.state == WAITING_FOR_PACK
