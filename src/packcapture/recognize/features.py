"""ORB feature extraction and keypoint (de)serialization.

Keypoints are stored as plain float32 arrays with one row per keypoint and
columns [x, y, size, angle, response, octave, class_id] so they survive a
round-trip through .npy (cv2.KeyPoint objects do not pickle cleanly).
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import cv2
import numpy as np

from ..config import ORB_NFEATURES, WORK_HEIGHT

KP_COLUMNS = 7  # x, y, size, angle, response, octave, class_id


def create_orb(nfeatures: int = ORB_NFEATURES) -> cv2.ORB:
    return cv2.ORB_create(nfeatures=nfeatures)


def load_gray(path: Union[str, Path]) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return img


def prep(img_gray: np.ndarray, work_height: int = WORK_HEIGHT) -> np.ndarray:
    """Resize to a consistent working height so scale matches across images."""
    h, w = img_gray.shape[:2]
    if h == 0 or w == 0:
        return img_gray
    if work_height and h != work_height:
        scale = work_height / h
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        img_gray = cv2.resize(img_gray, (max(1, int(w * scale)), work_height), interpolation=interp)
    return img_gray


def keypoints_to_array(keypoints) -> np.ndarray:
    if not keypoints:
        return np.zeros((0, KP_COLUMNS), np.float32)
    return np.array(
        [
            [k.pt[0], k.pt[1], k.size, k.angle, k.response, k.octave, k.class_id]
            for k in keypoints
        ],
        dtype=np.float32,
    )


def detect(orb: cv2.ORB, img_gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (keypoints_array Nx7, descriptors Nx32 uint8)."""
    keypoints, descriptors = orb.detectAndCompute(img_gray, None)
    if descriptors is None or len(keypoints) == 0:
        return np.zeros((0, KP_COLUMNS), np.float32), np.zeros((0, 32), np.uint8)
    return keypoints_to_array(keypoints), descriptors
