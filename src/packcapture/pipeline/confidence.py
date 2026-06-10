"""Confidence gate over ranked match results.

Validated on real me2 footage (see CLAUDE.md): the noise floor sits around 15
inliers and genuine hits land at 25-50+. We accept a recognition only when the
top candidate clears an absolute inlier floor *and* stands clear of the
runner-up. That second condition is the important one: the characteristic
failure mode (a card that isn't isolated — hands, desk, sealed packs all in
frame) produces several weak, near-tied candidates, and we want to reject that
rather than log a guess. A frame that matches one card decisively is logged; a
frame that "sort of" matches several is excluded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..recognize.orb_matcher import MatchResult


@dataclass
class GateConfig:
    # Absolute inlier floor for the top candidate (real hits are 25-50+).
    min_inliers: int = 25
    # The top candidate must beat the runner-up by at least this factor.
    margin_ratio: float = 1.5
    # A runner-up below this is treated as noise and never blocks acceptance,
    # so a lone strong match isn't rejected just because a weak rival exists.
    noise_floor: int = 15


@dataclass
class GateDecision:
    accepted: bool
    result: Optional[MatchResult]      # winning card if accepted, else top candidate (or None)
    reason: str
    runner_up: Optional[MatchResult] = None


class ConfidenceGate:
    def __init__(self, config: Optional[GateConfig] = None):
        self.cfg = config or GateConfig()

    def evaluate(self, results: Sequence[MatchResult]) -> GateDecision:
        if not results:
            return GateDecision(False, None, "no match (no features / empty bundle)")

        top = results[0]
        runner = results[1] if len(results) > 1 else None

        if top.inliers < self.cfg.min_inliers:
            return GateDecision(
                False, top,
                f"top inliers {top.inliers} < min {self.cfg.min_inliers}",
                runner,
            )

        if runner is not None and runner.inliers >= self.cfg.noise_floor:
            if top.inliers < self.cfg.margin_ratio * runner.inliers:
                return GateDecision(
                    False, top,
                    f"ambiguous: top {top.inliers} vs runner-up {runner.inliers} "
                    f"(need {self.cfg.margin_ratio}x margin)",
                    runner,
                )

        return GateDecision(True, top, "accepted", runner)
