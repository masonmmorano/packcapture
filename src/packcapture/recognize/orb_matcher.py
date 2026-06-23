"""Set-locked ORB matcher.

Given a query card image and a built set bundle (~100-400 candidates), rank
candidates by Lowe-ratio-filtered good matches, then refine the top few with a
RANSAC homography to count geometric inliers. Inlier count is the final score.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np

from ..storage.bundle import Bundle
from .features import create_orb, detect, load_gray, prep


@dataclass
class MatchResult:
    card_id: str
    name: str
    number: str
    rarity: str
    good: int          # Lowe-ratio good matches
    inliers: int       # RANSAC homography inliers (== good if not refined)
    score: float       # ranking score (inliers)
    supertype: str = ""  # Pokémon / Trainer / Energy ("" if the bundle predates the column)


class Matcher:
    def __init__(
        self,
        bundle: Bundle,
        ratio: float = 0.75,
        refine_top: int = 15,
        use_homography: bool = True,
    ):
        self.bundle = bundle
        self.ratio = ratio
        self.refine_top = refine_top
        self.use_homography = use_homography
        self.orb = create_orb(bundle.orb_nfeatures)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    def match_image(self, image_path: Union[str, Path], top: int = 5) -> list[MatchResult]:
        return self.match_array(load_gray(image_path), top=top)

    def match_array(self, image: np.ndarray, top: int = 5) -> list[MatchResult]:
        """Match an in-memory image (BGR or grayscale) — used by the live pipeline."""
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = prep(gray)
        qkp, qdesc = detect(self.orb, gray)
        return self.match_descriptors(qdesc, qkp, top=top)

    def match_descriptors(
        self,
        qdesc: Optional[np.ndarray],
        qkp: np.ndarray,
        top: int = 5,
    ) -> list[MatchResult]:
        if qdesc is None or len(qdesc) == 0:
            return []

        # Stage 1: ratio-test good-match count against every candidate.
        scored: list[tuple[int, int, list]] = []
        for i, cdesc in enumerate(self.bundle.descriptors):
            if cdesc is None or len(cdesc) < 2:
                scored.append((i, 0, []))
                continue
            knn = self.bf.knnMatch(qdesc, cdesc, k=2)
            good = [
                m for m, n in (p for p in knn if len(p) == 2)
                if m.distance < self.ratio * n.distance
            ]
            scored.append((i, len(good), good))

        scored.sort(key=lambda t: t[1], reverse=True)

        # Stage 2: RANSAC homography refinement on the strongest candidates.
        results: list[MatchResult] = []
        consider = max(top, self.refine_top)
        for rank, (i, good_count, good) in enumerate(scored[:consider]):
            inliers = good_count
            if self.use_homography and rank < self.refine_top and good_count >= 4:
                ckp = self.bundle.keypoints[i]
                src = np.float32([qkp[m.queryIdx][:2] for m in good]).reshape(-1, 1, 2)
                dst = np.float32([ckp[m.trainIdx][:2] for m in good]).reshape(-1, 1, 2)
                _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                if mask is not None:
                    inliers = int(mask.sum())
            row = self.bundle.rows[i]
            results.append(
                MatchResult(
                    card_id=row["card_id"],
                    name=row["name"],
                    number=row["number"],
                    rarity=row["rarity"],
                    good=good_count,
                    inliers=inliers,
                    score=float(inliers),
                    supertype=row.get("supertype", "") or "",
                )
            )

        results.sort(key=lambda r: (r.score, r.good), reverse=True)
        return results[:top]
