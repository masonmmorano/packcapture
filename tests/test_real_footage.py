"""Recognition regression on a real card crop (beyond the synthetic-card tests).

Uses a local, git-ignored fixture (tests/assets/murkrow_57.png) plus the
committed me2 bundle. Skips cleanly when either is absent — e.g. on a fresh
clone or in CI where the third-party footage crop isn't present — so the suite
stays green without it. See tests/assets/README.md for how to regenerate.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from packcapture.pipeline.confidence import ConfidenceGate
from packcapture.recognize.orb_matcher import Matcher
from packcapture.storage.bundle import load_bundle

ASSET = Path(__file__).parent / "assets" / "murkrow_57.png"


@pytest.fixture()
def me2_matcher():
    try:
        bundle = load_bundle("me2")
    except FileNotFoundError:
        pytest.skip("me2 bundle not built")
    return Matcher(bundle)


def test_murkrow_crop_recognized_and_accepted(me2_matcher):
    if not ASSET.exists():
        pytest.skip(f"local fixture missing: {ASSET} (see tests/assets/README.md)")
    img = cv2.imread(str(ASSET))
    assert img is not None, f"could not read {ASSET}"

    results = me2_matcher.match_array(img, top=5)
    decision = ConfidenceGate().evaluate(results)

    assert decision.accepted, f"gate rejected real Murkrow crop: {decision.reason}"
    top = decision.result
    assert top.number == "57", f"expected #57, got #{top.number} ({top.name})"
    assert "Murkrow" in top.name, f"expected Murkrow, got {top.name}"
    # Real hit should clear the validated floor with a wide margin over the runner-up.
    assert top.inliers >= 25
    if decision.runner_up is not None:
        assert top.inliers > 2 * decision.runner_up.inliers
