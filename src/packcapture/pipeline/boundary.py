"""Visual pack-boundary detector: WAITING_FOR_PACK <-> DETECTING_PACK.

Segments a rip session into packs so the session layer can close and label
each one (see session.py). The cut is driven by what is VISIBLE, not by a
fixed inter-pack time gap — ripping cadence is not constant, so the primary
cue is the card-present -> card-absent transition itself; duration is used
only as hysteresis so brief within-pack pauses (flipping to the next card,
adjusting grip) don't cut a pack in half.

Measured on real footage (rip_long.mp4, user ground truth 2026-06-10): the
cards-absent window between packs is ~4-6s (set cards down, grab + tear the
next wrapper), while within-pack pauses are well under that. The default
2.5s hysteresis sits between the two.

Two per-frame inputs, both cheap and already computed upstream:

- ``card_seen``: caller-defined card evidence. In practice "the matcher's top
  candidate clears the noise floor (~15 inliers)" — deliberately softer than
  the logging gate, so a fast-fanned card still counts as presence even when
  it isn't confidently identified.
- ``motion``: fraction of the frame that is moving foreground (MOG2). Used as
  an accelerator: when cards are already absent and a large motion burst is
  underway (grabbing/tearing the next wrapper), the boundary cuts early
  instead of waiting out the full hysteresis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# States.
WAITING_FOR_PACK = "waiting_for_pack"
DETECTING_PACK = "detecting_pack"

# Events emitted by update().
PACK_START = "pack_start"
PACK_END = "pack_end"


@dataclass
class BoundaryConfig:
    fps: float = 30.0
    # Card-absent hysteresis before declaring the pack over. Must sit between
    # within-pack pauses (<~1-2s) and the real inter-pack gap (~4-6s measured).
    absent_seconds: float = 2.5
    # Accelerator: with cards absent at least this long AND motion above
    # burst_motion (the next wrapper being grabbed/torn), cut immediately.
    burst_after_seconds: float = 1.0
    burst_motion: float = 0.25
    # Debounce on entry: require card evidence on this many frames within the
    # last evidence_window frames before declaring a pack started, so a single
    # spurious match during idle time doesn't open a phantom pack.
    start_evidence_frames: int = 3
    evidence_window: int = 12


class BoundaryDetector:
    """Per-frame state machine emitting PACK_START / PACK_END events."""

    def __init__(self, config: Optional[BoundaryConfig] = None):
        self.cfg = config or BoundaryConfig()
        self.state = WAITING_FOR_PACK
        self._absent = 0          # consecutive card-absent frames while DETECTING
        self._recent: list[bool] = []  # rolling card_seen history while WAITING

    @property
    def _absent_frames(self) -> int:
        return max(1, int(round(self.cfg.absent_seconds * self.cfg.fps)))

    @property
    def _burst_after_frames(self) -> int:
        return max(1, int(round(self.cfg.burst_after_seconds * self.cfg.fps)))

    def update(self, card_seen: bool, motion: float = 0.0) -> Optional[str]:
        """Advance one frame. Returns PACK_START, PACK_END, or None."""
        if self.state == WAITING_FOR_PACK:
            self._recent.append(card_seen)
            if len(self._recent) > self.cfg.evidence_window:
                self._recent.pop(0)
            if sum(self._recent) >= self.cfg.start_evidence_frames:
                self.state = DETECTING_PACK
                self._absent = 0
                self._recent = []
                return PACK_START
            return None

        # DETECTING_PACK
        if card_seen:
            self._absent = 0
            return None
        self._absent += 1
        timed_out = self._absent >= self._absent_frames
        burst_cut = (
            self._absent >= self._burst_after_frames
            and motion >= self.cfg.burst_motion
        )
        if timed_out or burst_cut:
            self.state = WAITING_FOR_PACK
            self._absent = 0
            return PACK_END
        return None

    def reset(self) -> None:
        self.state = WAITING_FOR_PACK
        self._absent = 0
        self._recent = []
