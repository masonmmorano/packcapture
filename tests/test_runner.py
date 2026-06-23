"""Headless pipeline integration: a synthetic 'thrown stack' stream of one pack.

Builds a 10-card synthetic bundle with rarities matching the standard slot
template, replays a frame stream that throws each card into the ROI in factory
order (plus a non-matching frame that must be excluded, not logged), and asserts
the pipeline logs exactly one clean, reconciled pack.
"""
from __future__ import annotations

import numpy as np
import pytest

from _synth import FakeClient, synth_card

from packcapture.pipeline.confidence import ConfidenceGate, GateConfig
from packcapture.pipeline.runner import run_stream
from packcapture.pipeline.session import STATUS_COMPLETE, Session, is_tracked_supertype
from packcapture.pipeline.settle import SettleConfig, SettleDetector
from packcapture.recognize.orb_matcher import Matcher
from packcapture.setbuild.builder import build_set
from packcapture.storage.bundle import load_bundle

# Rarities aligned to the 10 slot positions: 4 common, 3 uncommon,
# 2 reverse (any base rarity), 1 rare+.
PACK_RARITIES = (
    ["Common"] * 4 + ["Uncommon"] * 3 + ["Common", "Uncommon"] + ["Double Rare"]
)


@pytest.fixture()
def pack_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    build_set("fake", force=True, client=FakeClient(n=10, rarities=PACK_RARITIES))
    return load_bundle("fake")


def _stream(card_imgs, bg, settle_frames):
    rng = np.random.default_rng(0)
    frames = [bg.copy() for _ in range(settle_frames + 2)]
    for img in card_imgs:
        for _ in range(3):  # motion burst
            frames.append(rng.integers(0, 255, bg.shape, dtype=np.uint8))
        for _ in range(settle_frames + 3):  # card resting still
            frames.append(img.copy())
    return frames


def test_one_clean_pack_logged_and_reconciled(pack_bundle):
    matcher = Matcher(pack_bundle)
    session = Session("fake")
    cfg = SettleConfig(settle_frames=4)
    bg = np.full((600, 430, 3), 127, np.uint8)

    # Cards 0..9 in factory order, then a blank frame (no features) that the
    # gate must exclude rather than log.
    card_imgs = [synth_card(i + 1) for i in range(10)] + [bg.copy()]
    frames = _stream(card_imgs, bg, cfg.settle_frames)

    events = run_stream(
        frames,
        matcher=matcher,
        session=session,
        settle=SettleDetector(cfg),
        # Synthetic descriptors are dense; a low floor keeps the gate's logic
        # exercised without depending on exact synthetic inlier counts.
        gate=ConfidenceGate(GateConfig(min_inliers=10, margin_ratio=1.2, noise_floor=5)),
    )

    logged = [e for e in events if e.kind == "logged"]
    excluded = [e for e in events if e.kind == "excluded"]
    assert len(logged) == 10, [e.decision.reason for e in events]
    assert len(excluded) == 1  # the blank frame

    # The stream never closes packs itself — that's the boundary's job.
    assert session.packs == [] and session.pending == 10
    pack = session.close_pack()
    assert pack.status == STATUS_COMPLETE, pack.issues
    assert pack.reconciled
    assert [c.card_id for c in pack.cards] == [f"fake-{i}" for i in range(10)]


def test_is_tracked_supertype():
    assert is_tracked_supertype("Pokémon")
    assert is_tracked_supertype("Trainer")
    assert is_tracked_supertype("")          # unknown (old bundle) -> tracked, no regression
    assert not is_tracked_supertype("Energy")
    assert not is_tracked_supertype(" energy ")  # case/space-insensitive


def test_energy_card_excluded_not_logged(tmp_path, monkeypatch):
    # 11 cards in factory order PLUS an inserted energy that false-matches the
    # set's own energy card: it must be recognized-but-excluded, leaving a clean
    # 10-card reconciled pack (mirrors the real me2 Ignition Energy case).
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    rarities = PACK_RARITIES + ["Ultra Rare"]            # card 10 = the energy
    supertypes = ["Pokémon"] * 10 + ["Energy"]
    build_set("fake", force=True,
              client=FakeClient(n=11, rarities=rarities, supertypes=supertypes))
    bundle = load_bundle("fake")

    matcher = Matcher(bundle)
    session = Session("fake")
    cfg = SettleConfig(settle_frames=4)
    bg = np.full((600, 430, 3), 127, np.uint8)

    # Throw the 10 real cards, then the energy (card index 10) last.
    card_imgs = [synth_card(i + 1) for i in range(11)]
    frames = _stream(card_imgs, bg, cfg.settle_frames)

    events = run_stream(
        frames, matcher=matcher, session=session, settle=SettleDetector(cfg),
        gate=ConfidenceGate(GateConfig(min_inliers=10, margin_ratio=1.2, noise_floor=5)),
    )

    logged = [e for e in events if e.kind == "logged"]
    excluded = [e for e in events if e.kind == "excluded"]
    assert [e.card.card_id for e in logged] == [f"fake-{i}" for i in range(10)]
    # The energy was recognized (accepted by the gate) but excluded from the count.
    assert any(e.decision.accepted and e.decision.result.card_id == "fake-10"
               for e in excluded)

    pack = session.close_pack()
    assert pack.status == STATUS_COMPLETE, pack.issues
    assert pack.reconciled
    assert len(pack.cards) == 10
