"""End-to-end recognition test with synthetic cards (no network).

Builds a bundle through build_set() using a fake API client that serves
generated images, then checks each source image matches itself as top-1.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from packcapture.recognize.orb_matcher import Matcher
from packcapture.setbuild.builder import build_set
from packcapture.storage.bundle import load_bundle


def _synth_card(seed: int, h: int = 600, w: int = 430) -> np.ndarray:
    """A deterministic, feature-rich synthetic card image."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), (seed * 37 % 200) + 30, np.uint8)
    for _ in range(60):
        p1 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        p2 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            cv2.rectangle(img, p1, p2, color, thickness=int(rng.integers(1, 6)))
        else:
            cv2.circle(img, p1, int(rng.integers(5, 60)), color, thickness=int(rng.integers(1, 6)))
    cv2.putText(img, f"CARD {seed}", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5,
                (255, 255, 255), 3, cv2.LINE_AA)
    return img


class FakeClient:
    """Stands in for PokemonTCGClient; serves synthetic card images."""

    def __init__(self, n: int = 5):
        self.n = n
        self._images: dict[str, bytes] = {}
        self._cards = []
        for i in range(n):
            url = f"http://fake/card_{i}.png"
            ok, buf = cv2.imencode(".png", _synth_card(i + 1))
            assert ok
            self._images[url] = buf.tobytes()
            self._cards.append(
                {
                    "id": f"fake-{i}",
                    "number": str(i + 1),
                    "name": f"Card {i + 1}",
                    "rarity": "Common",
                    "images": {"large": url, "small": url},
                }
            )

    def get_set(self, code):
        return {"name": "Fake Set"}

    def get_cards(self, code, page_size=250):
        return self._cards

    def download(self, url):
        return self._images[url]


@pytest.fixture()
def built_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    client = FakeClient(n=5)
    manifest, _ = build_set("fake", force=True, client=client)
    assert manifest["card_count"] == 5
    assert manifest["feature_count"] > 0
    assert manifest["cards_without_features"] == 0
    return client


def test_each_card_matches_itself(built_bundle, tmp_path):
    bundle = load_bundle("fake")
    matcher = Matcher(bundle)
    for i in range(5):
        img_path = tmp_path / f"query_{i}.png"
        cv2.imwrite(str(img_path), _synth_card(i + 1))
        results = matcher.match_image(str(img_path), top=3)
        assert results, f"no results for card {i}"
        assert results[0].card_id == f"fake-{i}", (
            f"card {i} mismatched: got {results[0].card_id} "
            f"(inliers={results[0].inliers})"
        )
        # Top match should be clearly ahead of the runner-up.
        if len(results) > 1:
            assert results[0].inliers > results[1].inliers


@pytest.fixture()
def big_bundle(tmp_path, monkeypatch):
    """A bundle with more candidates than the prefilter keeps, so the prefilter
    path (narrow-then-fully-score) is actually exercised."""
    monkeypatch.setenv("PACKCAPTURE_DATA_DIR", str(tmp_path / "sets"))
    client = FakeClient(n=40)
    build_set("fakebig", force=True, client=client)
    return client


def test_prefilter_preserves_top1(big_bundle, tmp_path):
    bundle = load_bundle("fakebig")
    full = Matcher(bundle)                      # exhaustive (default)
    fast = Matcher(bundle, prefilter_top=10)    # beta prefilter, 10 of 40 survive
    for i in range(40):
        img = _synth_card(i + 1)
        rf = full.match_array(img, top=1)
        rk = fast.match_array(img, top=1)
        assert rf and rk
        assert rf[0].card_id == f"fake-{i}"             # exhaustive is correct
        assert rk[0].card_id == rf[0].card_id           # prefilter keeps the winner


def test_empty_query_returns_no_results(built_bundle):
    bundle = load_bundle("fake")
    matcher = Matcher(bundle)
    blank = np.zeros((600, 430, 3), np.uint8)
    import tempfile, os

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, blank)
    try:
        results = matcher.match_image(path, top=3)
    finally:
        os.remove(path)
    assert results == []
