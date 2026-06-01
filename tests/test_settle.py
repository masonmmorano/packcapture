"""Capture -> settle -> recognize, end to end on a synthetic 'thrown stack' stream.

Simulates a ROI video: still background, then for each placement a burst of
motion (a hand / sliding card) followed by the card resting still. Asserts the
settle detector emits exactly one recognition per placement, in order, even when
the placement order is not the set order — i.e. we never assume ordering, we
observe it.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from _synth import FakeClient, synth_card

from packcapture.pipeline.settle import SettleConfig, SettleDetector
from packcapture.recognize.orb_matcher import Matcher
from packcapture.setbuild.builder import build_set
from packcapture.storage.bundle import load_bundle


def _placement_stream(card_imgs, bg, settle_frames):
    """Frames: calm background, then [motion burst, card resting] per placement."""
    rng = np.random.default_rng(0)
    frames = [bg.copy() for _ in range(settle_frames + 2)]  # initial calm: no emit
    for img in card_imgs:
        for _ in range(3):  # motion: noisy frames (hand passing / card sliding in)
            frames.append(rng.integers(0, 255, bg.shape, dtype=np.uint8))
        for _ in range(settle_frames + 3):  # card resting still -> one settle event
            frames.append(img.copy())
    return frames


@pytest.fixture()
def synth_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    build_set("fake", force=True, client=FakeClient(n=6))
    return load_bundle("fake")


def test_emits_once_per_placement_in_observed_order(synth_bundle):
    matcher = Matcher(synth_bundle)
    cfg = SettleConfig(settle_frames=4)
    detector = SettleDetector(cfg)

    # Deliberately not in set order, to prove we observe order rather than assume it.
    placement_order = [2, 0, 4, 1, 3]
    card_imgs = [synth_card(i + 1) for i in placement_order]
    bg = np.full((600, 430, 3), 127, np.uint8)

    emitted: list[str] = []
    for frame in _placement_stream(card_imgs, bg, cfg.settle_frames):
        if detector.update(frame):
            results = matcher.match_array(frame, top=1)
            assert results, "settle event produced no recognition"
            emitted.append(results[0].card_id)

    assert emitted == [f"fake-{i}" for i in placement_order]


def test_lingering_card_emits_only_once(synth_bundle):
    """A single card sitting in frame for many frames must emit exactly once."""
    cfg = SettleConfig(settle_frames=4)
    detector = SettleDetector(cfg)
    bg = np.full((600, 430, 3), 127, np.uint8)
    rng = np.random.default_rng(1)

    frames = [bg.copy() for _ in range(6)]
    frames += [rng.integers(0, 255, bg.shape, dtype=np.uint8) for _ in range(3)]  # motion
    frames += [synth_card(1) for _ in range(200)]  # lingers a long time

    emits = sum(1 for f in frames if detector.update(f))
    assert emits == 1
