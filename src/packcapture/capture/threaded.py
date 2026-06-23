"""Real-time plumbing for live capture: decouple frame grabbing and recognition.

A single ORB recognition over a built set takes ~300+ ms, while the camera
delivers frames every ~16-33 ms. Running recognition inline (read -> match ->
draw -> read) drags the whole loop down to recognition rate, so the displayed
video crawls and lags the action.

The fix here is two background threads over the existing ``FrameSource``:

- :class:`ThreadedFrameSource` grabs frames continuously into a *single-slot*
  buffer (newest wins, stale frames are dropped). The display loop and the
  recognizer both read the freshest available frame instead of draining a queue
  that would only grow.
- :class:`RecognitionWorker` runs a user-supplied ``process(frame)`` on the
  latest frame as fast as it can (~recognition rate) and hands each result back,
  so the display thread can stay smooth at camera rate while reading the most
  recent recognition state.

Neither class imports the matcher or overlay -- they take plain callables -- so
they unit-test against synthetic frame sources with no camera or bundle.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional, Tuple, Union

import numpy as np

from .source import FrameSource


class LatestSlot:
    """A thread-safe single-slot buffer: writers overwrite, readers get newest.

    Carries a monotonically increasing sequence number so a reader can tell
    whether the value changed since it last looked (and skip redundant work).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: Any = None
        self._seq = 0

    def set(self, value: Any) -> None:
        with self._lock:
            self._value = value
            self._seq += 1

    def get(self) -> Tuple[int, Any]:
        """Return ``(seq, value)``. ``seq == 0`` means nothing written yet."""
        with self._lock:
            return self._seq, self._value


class ThreadedFrameSource:
    """Continuously read a :class:`FrameSource` into a newest-wins slot.

    ``source`` may be a device index / path (wrapped in a ``FrameSource``) or an
    already-constructed ``FrameSource``-like object exposing ``open()``,
    ``frames()`` and ``release()`` -- the latter lets tests inject a fake.
    """

    def __init__(
        self,
        source: Union[int, str, FrameSource],
        *,
        low_latency: bool = True,
        pace: Union[None, float, str] = None,
    ):
        """``pace`` throttles the reader: a float fps, or ``"source"`` to match the
        source's own fps. Left ``None`` (live cameras) the reader grabs as fast as
        the device delivers; pacing a *file* makes it replay at real time so the
        recognizer samples it like a live feed instead of seeing dropped frames."""
        self._src = source if hasattr(source, "frames") else FrameSource(source)
        self._low_latency = low_latency
        self._pace = pace
        self._pace_dt = 0.0
        self._slot = LatestSlot()
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "ThreadedFrameSource":
        self._src.open()
        if self._pace == "source":
            fps = float(getattr(self._src, "fps", 0.0) or 0.0)
            self._pace_dt = 1.0 / fps if fps > 0 else 0.0
        elif isinstance(self._pace, (int, float)) and self._pace:
            self._pace_dt = 1.0 / float(self._pace)
        if self._low_latency:
            # Best-effort: keep the driver buffer at one frame so we read the
            # newest grab, not a backlog. Not all backends honor it; ignore.
            cap = getattr(self._src, "_cap", None)
            if cap is not None:
                try:
                    import cv2

                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
        self._thread = threading.Thread(target=self._run, name="frame-reader", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            next_t = time.monotonic()
            for frame in self._src.frames():
                if self._stopped.is_set():
                    break
                self._slot.set(frame)
                if self._pace_dt:
                    next_t += self._pace_dt
                    delay = next_t - time.monotonic()
                    if delay > 0 and self._stopped.wait(delay):
                        break
        finally:
            self._stopped.set()

    def latest(self) -> Tuple[int, Optional[np.ndarray]]:
        """Newest ``(seq, frame)``; ``seq == 0`` until the first frame arrives."""
        return self._slot.get()

    @property
    def stopped(self) -> bool:
        """True once the source is exhausted (file ended) or ``stop()`` ran."""
        return self._stopped.is_set()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._src.release()

    def __enter__(self) -> "ThreadedFrameSource":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class RecognitionWorker:
    """Run ``process`` on the freshest frame in a background thread.

    ``get_frame`` returns ``(seq, frame)`` (e.g. ``ThreadedFrameSource.latest``);
    the worker only processes a frame whose ``seq`` is newer than the last one it
    handled, so it never re-recognizes a stale frame and never blocks waiting for
    a fresh one. Each non-``None`` ``process(frame)`` result is passed to
    ``on_result`` (called on the worker thread -- keep it cheap / lock-guarded).
    """

    def __init__(
        self,
        get_frame: Callable[[], Tuple[int, Optional[np.ndarray]]],
        process: Callable[[np.ndarray], Any],
        on_result: Callable[[Any], None],
        *,
        idle_sleep: float = 0.005,
    ):
        self._get_frame = get_frame
        self._process = process
        self._on_result = on_result
        self._idle_sleep = idle_sleep
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_seq = 0
        self._ticks = 0

    def start(self) -> "RecognitionWorker":
        self._thread = threading.Thread(target=self._run, name="recognizer", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stopped.is_set():
            seq, frame = self._get_frame()
            if seq == self._last_seq or frame is None:
                # No new frame yet -- yield instead of busy-spinning.
                self._stopped.wait(self._idle_sleep)
                continue
            self._last_seq = seq
            result = self._process(frame)
            self._ticks += 1
            if result is not None:
                self._on_result(result)

    @property
    def ticks(self) -> int:
        """How many frames have been recognized (its effective frame rate)."""
        return self._ticks

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
