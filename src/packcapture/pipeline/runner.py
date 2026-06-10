"""Headless pipeline: frames -> settle -> recognize -> gate -> session.

This is the engine the UI drives, kept deliberately camera- and display-free so
it runs in CI and on replayed video. It takes any iterable of BGR frames (a
:class:`FrameSource`, or a plain list in tests) and a fixed ROI, runs the settle
detector, and on each settle event recognizes the ROI, applies the confidence
gate, and logs accepted cards to the session.

Rejected or no-match recognitions are surfaced as "excluded" events (energy,
code card, or low-confidence) but never logged, so the count-to-10 checksum in
the session stays honest.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from ..recognize.orb_matcher import Matcher
from .confidence import ConfidenceGate, GateDecision
from .session import LoggedCard, Pack, Session
from .settle import SettleDetector

# ROI as (x, y, w, h), matching cv2.selectROI's output.
ROI = tuple[int, int, int, int]


@dataclass
class PipelineEvent:
    kind: str                       # "logged" or "excluded"
    decision: GateDecision
    card: Optional[LoggedCard] = None   # set when kind == "logged"
    pack: Optional[Pack] = None         # set when this card closed a pack


def crop(frame: np.ndarray, roi: Optional[ROI]) -> np.ndarray:
    if roi is None:
        return frame
    x, y, w, h = roi
    return frame[y:y + h, x:x + w]


def run_stream(
    frames: Iterable[np.ndarray],
    *,
    matcher: Matcher,
    session: Session,
    roi: Optional[ROI] = None,
    settle: Optional[SettleDetector] = None,
    gate: Optional[ConfidenceGate] = None,
    top: int = 5,
) -> list[PipelineEvent]:
    """Drive a frame stream through the pipeline, returning the events emitted.

    One settle event (one card thrown and resting) yields exactly one PipelineEvent.
    """
    settle = settle or SettleDetector()
    gate = gate or ConfidenceGate()
    events: list[PipelineEvent] = []

    for frame in frames:
        region = crop(frame, roi)
        if not settle.update(region):
            continue
        decision = gate.evaluate(matcher.match_array(region, top=top))
        if not decision.accepted:
            events.append(PipelineEvent("excluded", decision))
            continue
        r = decision.result
        card, pack = session.add(
            card_id=r.card_id,
            name=r.name,
            number=r.number,
            base_rarity=r.rarity,
            inliers=r.inliers,
        )
        events.append(PipelineEvent("logged", decision, card=card, pack=pack))

    return events
