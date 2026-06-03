"""Confidence gate: accept decisive matches, reject weak or ambiguous ones."""
from __future__ import annotations

from packcapture.pipeline.confidence import ConfidenceGate, GateConfig
from packcapture.recognize.orb_matcher import MatchResult


def _r(card_id: str, inliers: int) -> MatchResult:
    return MatchResult(
        card_id=card_id, name=card_id, number="1", rarity="Common",
        good=inliers, inliers=inliers, score=float(inliers),
    )


def test_empty_results_rejected():
    d = ConfidenceGate().evaluate([])
    assert not d.accepted and d.result is None


def test_strong_lone_match_accepted():
    d = ConfidenceGate().evaluate([_r("a", 51)])
    assert d.accepted and d.result.card_id == "a"


def test_below_floor_rejected():
    # 20 inliers is above the noise floor but below the 25 acceptance floor.
    d = ConfidenceGate().evaluate([_r("a", 20), _r("b", 5)])
    assert not d.accepted
    assert "min" in d.reason


def test_clear_margin_over_runner_up_accepted():
    # Mirrors the validated Murkrow case: 51 vs 9.
    d = ConfidenceGate().evaluate([_r("murkrow", 51), _r("other", 9)])
    assert d.accepted and d.result.card_id == "murkrow"


def test_ambiguous_pair_rejected():
    # Both above the floor and too close together -> the "not isolated" failure mode.
    d = ConfidenceGate().evaluate([_r("a", 30), _r("b", 28)])
    assert not d.accepted
    assert "ambiguous" in d.reason


def test_noise_floor_runner_up_does_not_block():
    # A weak rival below the noise floor must not veto an otherwise-strong match.
    cfg = GateConfig(min_inliers=25, margin_ratio=1.5, noise_floor=15)
    d = ConfidenceGate(cfg).evaluate([_r("a", 26), _r("b", 14)])
    assert d.accepted and d.result.card_id == "a"
