"""Auto-ROI detector for the held-cards region (rip mode).

For the target setup — a fixed camera recording a pack-opening, where the
background (posters, desk, sealed stacks) is static and only the hands and cards
move — the cards are the moving, feature-rich region. We keep an online
background model (MOG2), retain only the ORB keypoints that fall on moving
foreground, and take the bounding box of the densest connected cluster, padded
top/bottom. That removes the static feature-rich clutter which otherwise defeats
whole-frame matching, so a user can frame the entire camera and just rip — no
manual zone to drag.

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
    cell: int = 28               # keypoint-density grid cell size (px)
    hot: int = 2                 # min keypoints in a cell to count as occupied
    pad_tb: float = 0.18         # extra padding top/bottom (fraction of blob height)
    pad_lr: float = 0.06         # padding left/right
    min_moving_kp: int = 40      # below this many moving keypoints, emit no ROI
    warmup: int = 8              # frames to let the background model settle first
    nfeatures: int = 1500
    mog_history: int = 250
    mog_var_threshold: float = 40.0


def density_bbox(pts: np.ndarray, w: int, h: int, cfg: ROIConfig) -> Optional[ROI]:
    """Bounding box of the densest connected cluster of points, padded."""
    if len(pts) == 0:
        return None
    cols, rows = max(1, w // cfg.cell), max(1, h // cfg.cell)
    gx = np.clip((pts[:, 0] / w * cols).astype(int), 0, cols - 1)
    gy = np.clip((pts[:, 1] / h * rows).astype(int), 0, rows - 1)
    counts = np.zeros((rows, cols), np.int32)
    np.add.at(counts, (gy, gx), 1)

    mask = (counts >= cfg.hot).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None
    best, best_kp = 0, -1
    for lbl in range(1, n):
        kp_in = int(counts[labels == lbl].sum())
        if kp_in > best_kp:
            best, best_kp = lbl, kp_in

    x = int(stats[best, cv2.CC_STAT_LEFT]) * cfg.cell
    y = int(stats[best, cv2.CC_STAT_TOP]) * cfg.cell
    x1 = (int(stats[best, cv2.CC_STAT_LEFT]) + int(stats[best, cv2.CC_STAT_WIDTH])) * cfg.cell
    y1 = (int(stats[best, cv2.CC_STAT_TOP]) + int(stats[best, cv2.CC_STAT_HEIGHT])) * cfg.cell
    pad_y, pad_x = int((y1 - y) * cfg.pad_tb), int((x1 - x) * cfg.pad_lr)
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
    return (x0, y0, x1 - x0, y1 - y0)


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
        return density_bbox(moving, w, h, self.cfg)
