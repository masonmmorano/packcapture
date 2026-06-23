"""Frame source: a webcam/OBS device index or a video file, behind one interface.

OBS virtual cam shows up to OpenCV as a normal capture device, so live webcam,
OBS, and a recorded video clip all run through the same code path. That lets us
replay YouTube pack-opening clips frame-for-frame for testing and tuning.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator, Optional, Union

import cv2
import numpy as np


class FrameSource:
    def __init__(
        self,
        src: Union[int, str],
        backend: Optional[int] = None,
        request_size: Optional[tuple] = (1920, 1080),
    ):
        self.src = src
        self.is_device = isinstance(src, int) or (isinstance(src, str) and str(src).isdigit())
        self.backend = backend
        # Cameras (incl. the OBS Virtual Camera) often default to 640x480 over
        # DirectShow unless a resolution is requested; ask for full HD so the
        # recognizer gets the device's real detail. The driver clamps to the
        # nearest supported mode. Ignored for file sources.
        self.request_size = request_size
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> "FrameSource":
        if self.is_device:
            index = int(self.src)
            backend = self.backend
            # CAP_DSHOW tends to enumerate faster and with lower latency on Windows.
            if backend is None and sys.platform.startswith("win"):
                backend = cv2.CAP_DSHOW
            self._cap = (
                cv2.VideoCapture(index, backend) if backend is not None
                else cv2.VideoCapture(index)
            )
            if self._cap is not None and self.request_size:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.request_size[0])
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.request_size[1])
        else:
            path = str(self.src)
            if not Path(path).exists():
                raise FileNotFoundError(f"Video file not found: {path}")
            self._cap = cv2.VideoCapture(path)

        if self._cap is None or not self._cap.isOpened():
            raise RuntimeError(f"Could not open frame source: {self.src!r}")
        return self

    @property
    def fps(self) -> float:
        if self._cap is None:
            return 0.0
        return float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)

    def frames(self) -> Iterator[np.ndarray]:
        """Yield BGR frames until the source is exhausted (files) or stopped."""
        if self._cap is None:
            self.open()
        assert self._cap is not None
        while True:
            ok, frame = self._cap.read()
            if not ok:
                break
            yield frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> "FrameSource":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.release()
