"""Auto-ROI detector for the held-cards region (rip mode).

For the target setup — a fixed camera recording a pack-opening, where the
background (posters, desk, sealed stacks) is static and only the hands and cards
move — the cards are the moving, feature-rich region. We keep an online
background model (MOG2), retain only the ORB keypoints that fall on moving
foreground, and take a robust percentile bounding box of those points, grown to
the card's known aspect ratio. That removes the static feature-rich clutter which
otherwise defeats whole-frame matching, so a user can frame the entire camera and
just rip — no manual zone to drag.

Validated on real me2 footage: whole-frame matching was noise, but this
auto-ROI recovers a tight box that recognizes Murkrow #57 at ~49 inliers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from ..recognize.features import create_orb

ROI = tuple[int, int, int, int]  # (x, y, w, h)


@dataclass
class ROIConfig:
    pct_lo: float = 5.0          # robust extent: low percentile of moving keypoints
    pct_hi: float = 95.0         # robust extent: high percentile (rejects stray points)
    card_aspect: float = 0.72    # card w/h (2.5x3.5in); box is grown to this, never cropped
    pad_tb: float = 0.10         # extra padding top/bottom (fraction of box height)
    pad_lr: float = 0.08         # padding left/right
    min_moving_kp: int = 40      # below this many moving keypoints, emit no ROI
    warmup: int = 8              # frames to let the background model settle first
    nfeatures: int = 1500
    mog_history: int = 250
    mog_var_threshold: float = 40.0


def card_bbox(pts: np.ndarray, w: int, h: int, cfg: ROIConfig) -> Optional[ROI]:
    """Whole-card box from moving keypoints.

    The densest *connected* keypoint cluster only covers a card's textured core
    (art + text), so it under-frames badly. Since MOG2 already restricts these to
    moving foreground (static clutter gone, the hand is feature-poor), a robust
    percentile bounding box of the moving keypoints recovers the card's full
    extent. We then grow that box to the card's known aspect ratio — expanding the
    deficient dimension, never shrinking — so the box stays card-shaped and biases
    toward over-framing (ORB tolerates surrounding context; cropping kills it).
    """
    if len(pts) == 0:
        return None
    x0, x1 = np.percentile(pts[:, 0], [cfg.pct_lo, cfg.pct_hi])
    y0, y1 = np.percentile(pts[:, 1], [cfg.pct_lo, cfg.pct_hi])
    bw, bh = float(x1 - x0), float(y1 - y0)
    if bw <= 0 or bh <= 0:
        return None
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    # Grow to card aspect: a box wider than a card gains height; a tall box gains width.
    bh = max(bh, bw / cfg.card_aspect)
    bw = max(bw, bh * cfg.card_aspect)
    bw *= 1.0 + 2.0 * cfg.pad_lr
    bh *= 1.0 + 2.0 * cfg.pad_tb

    nx0 = max(0, int(round(cx - bw / 2.0)))
    ny0 = max(0, int(round(cy - bh / 2.0)))
    nx1 = min(w, int(round(cx + bw / 2.0)))
    ny1 = min(h, int(round(cy + bh / 2.0)))
    return (nx0, ny0, nx1 - nx0, ny1 - ny0)


@dataclass
class SmootherConfig:
    # EMA weight on each new box. Lower = smoother but laggier; higher = snappier.
    alpha: float = 0.35
    # When detection drops out, keep showing the last box for up to this many frames.
    max_misses: int = 8
    # Ignore total box movements below this (px) so a near-stationary box doesn't shimmer.
    deadband: float = 6.0


class BoxSmoother:
    """Temporal low-pass on an ROI to stop the per-frame box from jittering.

    Combines an EMA on (x, y, w, h), a hold-last-box grace period when a frame
    yields no ROI, and a deadband that suppresses sub-threshold movement.
    """

    def __init__(self, config: Optional[SmootherConfig] = None):
        self.cfg = config or SmootherConfig()
        self._box: Optional[np.ndarray] = None  # float [x, y, w, h]
        self._misses = 0

    def reset(self) -> None:
        self._box = None
        self._misses = 0

    def update(self, box: Optional[ROI]) -> Optional[ROI]:
        if box is None:
            if self._box is None:
                return None
            self._misses += 1
            if self._misses > self.cfg.max_misses:
                self._box = None
                return None
            return self._as_int(self._box)

        self._misses = 0
        b = np.asarray(box, dtype=float)
        if self._box is None:
            self._box = b
        else:
            candidate = self.cfg.alpha * b + (1.0 - self.cfg.alpha) * self._box
            if float(np.abs(candidate - self._box).sum()) >= self.cfg.deadband:
                self._box = candidate
        return self._as_int(self._box)

    @staticmethod
    def _as_int(b: np.ndarray) -> ROI:
        return (int(round(b[0])), int(round(b[1])), int(round(b[2])), int(round(b[3])))


class MotionFeatureROI:
    """Online (per-frame) auto-ROI: moving foreground ∩ ORB feature density."""

    def __init__(self, config: Optional[ROIConfig] = None, orb: Optional[cv2.ORB] = None):
        self.cfg = config or ROIConfig()
        self.orb = orb or create_orb(self.cfg.nfeatures)
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=self.cfg.mog_history,
            varThreshold=self.cfg.mog_var_threshold,
            detectShadows=False,
        )
        self._seen = 0

    def detect(self, frame_bgr: np.ndarray) -> Optional[ROI]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        h, w = gray.shape[:2]

        fg = self.bg.apply(frame_bgr)
        self._seen += 1
        if self._seen <= self.cfg.warmup:
            return None

        fg = (fg > 0).astype(np.uint8)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

        kps = self.orb.detect(gray, None)
        if not kps:
            return None
        pts = np.array([k.pt for k in kps], dtype=np.float32)
        xs = np.clip(pts[:, 0].astype(int), 0, w - 1)
        ys = np.clip(pts[:, 1].astype(int), 0, h - 1)
        moving = pts[fg[ys, xs] > 0]
        if len(moving) < self.cfg.min_moving_kp:
            return None
        return card_bbox(moving, w, h, self.cfg)
