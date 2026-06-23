"""Probe which capture device indices are usable, to find the OBS Virtual Cam.

OpenCV addresses cameras by integer index, not name, and offers no portable
enumeration. So we brute-force open indices ``0..max_index`` and report the ones
that yield a frame, with their resolution/fps. An index that fails to open is
often one another app already holds (e.g. OBS owning the physical cam) -- which
is itself a useful signal when setting up the shared-camera flow.

Note this momentarily opens each camera, so it can be slow and may briefly wake
a device's indicator light.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CameraInfo:
    index: int
    width: int
    height: int
    fps: float


def enumerate_cameras(max_index: int = 10, backend: Optional[int] = None) -> List[CameraInfo]:
    """Return info for every device index in ``0..max_index`` that delivers a frame."""
    import cv2

    if backend is None and sys.platform.startswith("win"):
        # CAP_DSHOW enumerates faster and avoids long MSMF open stalls on Windows.
        backend = cv2.CAP_DSHOW

    found: List[CameraInfo] = []
    for index in range(max_index + 1):
        cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            found.append(CameraInfo(index=index, width=w, height=h, fps=fps))
        finally:
            cap.release()
    return found
