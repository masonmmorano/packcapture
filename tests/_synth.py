"""Shared synthetic-card helpers for tests (no network)."""
from __future__ import annotations

import cv2
import numpy as np


def synth_card(seed: int, h: int = 600, w: int = 430) -> np.ndarray:
    """A deterministic, feature-rich synthetic card image (BGR)."""
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
            ok, buf = cv2.imencode(".png", synth_card(i + 1))
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
