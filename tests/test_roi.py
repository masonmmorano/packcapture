"""Auto-ROI detector: a moving feature-rich card on a static background is boxed."""
from __future__ import annotations

import numpy as np

from _synth import synth_card

from packcapture.pipeline.roi import BoxSmoother, MotionFeatureROI, ROIConfig, SmootherConfig


def _gradient_bg(h=480, w=640):
    # A smooth gradient: bright but nearly featureless, so ORB fires on the card only.
    col = np.linspace(40, 200, w, dtype=np.uint8)
    return np.repeat(col[None, :], h, axis=0)[:, :, None].repeat(3, axis=2)


def test_moving_card_is_localized():
    cfg = ROIConfig(warmup=6, min_moving_kp=20)
    det = MotionFeatureROI(cfg)
    bg = _gradient_bg()
    card = synth_card(7, h=280, w=200)

    # Warm up the background model on the static scene.
    for _ in range(8):
        assert det.detect(bg.copy()) is None

    # Card slides through a known region; collect the boxes once it's moving.
    boxes = []
    for i in range(10):
        frame = bg.copy()
        x0, y0 = 240 + i * 4, 120 + i * 2
        frame[y0:y0 + 280, x0:x0 + 200] = card
        roi = det.detect(frame)
        if roi is not None:
            boxes.append(roi)

    assert boxes, "no ROI detected for a moving, feature-rich card"
    x, y, w, h = boxes[-1]
    cx, cy = x + w / 2, y + h / 2
    # Center should land on the card region, not the whole frame.
    assert 240 <= cx <= 540, f"roi center x {cx} off the card"
    assert 80 <= cy <= 460, f"roi center y {cy} off the card"
    assert w < 600 and h < 460, f"roi {w}x{h} is basically whole-frame"


def test_static_scene_emits_no_roi():
    det = MotionFeatureROI(ROIConfig(warmup=4))
    bg = _gradient_bg()
    rois = [det.detect(bg.copy()) for _ in range(12)]
    assert all(r is None for r in rois), "static scene should not produce an ROI"


def test_smoother_reduces_jitter():
    # A box jittering around a fixed center should come out far calmer.
    rng = np.random.default_rng(0)
    base = np.array([300, 200, 250, 320], float)
    sm = BoxSmoother(SmootherConfig(alpha=0.3, deadband=0.0))
    raw, smoothed = [], []
    for _ in range(60):
        b = base + rng.normal(0, 25, 4)
        raw.append(b[:2].copy())
        out = sm.update(tuple(b.astype(int)))
        smoothed.append(np.array(out[:2], float))
    raw_var = np.var(np.diff(np.array(raw), axis=0))
    smooth_var = np.var(np.diff(np.array(smoothed), axis=0))
    assert smooth_var < raw_var / 2, f"smoothing barely helped: {smooth_var} vs {raw_var}"


def test_smoother_holds_then_drops_on_misses():
    sm = BoxSmoother(SmootherConfig(max_misses=3))
    sm.update((10, 10, 100, 120))
    held = [sm.update(None) for _ in range(3)]
    assert all(h is not None for h in held), "should hold the last box during grace"
    assert sm.update(None) is None, "should drop the box after max_misses"
