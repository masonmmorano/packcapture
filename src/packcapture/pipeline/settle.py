"""Motion-settle / debounce state machine for the detection zone (ROI).

In zone mode the user throws each card onto a growing stack inside a fixed box,
so the box always shows the current top card. Recognition should fire exactly
once per placement. We model that as: watch the ROI for motion, then emit a
single "settled" event on each motion -> still transition.

- A card thrown in = a burst of motion, then stillness -> one emit.
- A card lingering in frame produces no new motion -> still transition -> no
  re-emit, which is exactly the debounce behavior we want.

The detector is recognizer-agnostic: callers run recognition only on the frames
where `update()` returns True.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class SettleConfig:
    # Mean absolute pixel difference (0-255 scale) above which the ROI is "moving".
    motion_thresh: float = 8.0
    # Consecutive still frames required to confirm a placement.
    settle_frames: int = 4
    # ROI is downscaled to this square before differencing, so the threshold is
    # independent of camera resolution and a bit robust to noise.
    probe_size: int = 64


class SettleDetector:
    def __init__(self, config: Optional[SettleConfig] = None):
        self.cfg = config or SettleConfig()
        self._prev: Optional[np.ndarray] = None
        self._still = 0
        self._armed = False  # have we seen motion since the last emit?

    def reset(self) -> None:
        self._prev = None
        self._still = 0
        self._armed = False

    def _probe(self, roi: np.ndarray) -> np.ndarray:
        gray = roi if roi.ndim == 2 else cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(
            gray, (self.cfg.probe_size, self.cfg.probe_size), interpolation=cv2.INTER_AREA
        ).astype(np.int16)

    def update(self, roi: np.ndarray) -> bool:
        """Feed one ROI frame. Returns True on the frame a new card settles."""
        probe = self._probe(roi)
        if self._prev is None:
            self._prev = probe
            return False

        diff = float(np.mean(np.abs(probe - self._prev)))
        self._prev = probe

        if diff > self.cfg.motion_thresh:
            self._armed = True
            self._still = 0
            return False

        # Still frame.
        self._still += 1
        if self._armed and self._still >= self.cfg.settle_frames:
            self._armed = False  # require fresh motion before the next emit
            return True
        return False
