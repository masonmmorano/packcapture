"""Unit tests for the real-time capture plumbing (no camera, no bundle).

A fake ``FrameSource`` yields synthetic frames so the threading behavior --
newest-wins buffering, frame dropping under a slow consumer, and clean
shutdown -- is exercised deterministically in CI.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from packcapture.capture.threaded import (
    LatestSlot,
    RecognitionWorker,
    ThreadedFrameSource,
)


class FakeSource:
    """FrameSource-like: yields ``count`` frames (each tagged by value) with a gap."""

    def __init__(self, count: int, interval: float = 0.005, hold: bool = False):
        self.count = count
        self.interval = interval
        self.hold = hold  # keep yielding the last frame forever (simulates a live cam)
        self.opened = False
        self.released = False

    def open(self):
        self.opened = True
        return self

    def frames(self):
        i = 0
        while i < self.count:
            time.sleep(self.interval)
            # A frame whose first pixel encodes its index, so consumers can
            # assert which frame they saw.
            f = np.full((4, 4, 3), i % 256, dtype=np.uint8)
            yield f
            i += 1
        while self.hold:
            time.sleep(self.interval)
            yield np.full((4, 4, 3), (self.count - 1) % 256, dtype=np.uint8)

    def release(self):
        self.released = True


def _wait(predicate, timeout=2.0, interval=0.005):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_latest_slot_overwrites_and_tracks_seq():
    slot = LatestSlot()
    assert slot.get() == (0, None)
    slot.set("a")
    slot.set("b")
    seq, val = slot.get()
    assert val == "b" and seq == 2  # both writes counted, newest wins


def test_threaded_source_yields_newest_and_stops_cleanly():
    src = FakeSource(count=5, interval=0.005)
    tfs = ThreadedFrameSource(src).start()
    assert _wait(lambda: tfs.latest()[0] > 0), "no frame ever arrived"
    assert _wait(lambda: tfs.stopped), "source did not finish"
    seq, frame = tfs.latest()
    assert seq == 5  # all five frames passed through the slot
    assert int(frame[0, 0, 0]) == 4  # last frame retained
    tfs.stop()
    assert src.released


def test_threaded_source_drops_stale_frames_for_slow_consumer():
    # Producer emits 20 frames quickly; a slow consumer sampling occasionally
    # must see far fewer than 20 -- frames are dropped, not queued.
    src = FakeSource(count=20, interval=0.002)
    tfs = ThreadedFrameSource(src).start()
    seen = set()
    while not tfs.stopped:
        seq, frame = tfs.latest()
        if frame is not None:
            seen.add(seq)
        time.sleep(0.01)  # slower than the producer
    tfs.stop()
    assert len(seen) < 20  # dropped intermediate frames rather than buffering them


def test_recognition_worker_processes_only_new_frames():
    src = FakeSource(count=8, interval=0.004)
    tfs = ThreadedFrameSource(src).start()
    results = []
    lock = threading.Lock()

    def process(frame):
        return int(frame[0, 0, 0])  # "recognize" = read the index tag

    def on_result(r):
        with lock:
            results.append(r)

    worker = RecognitionWorker(tfs.latest, process, on_result, idle_sleep=0.002).start()
    _wait(lambda: tfs.stopped)
    _wait(lambda: worker.ticks > 0)
    worker.stop()
    tfs.stop()
    # Never recognized more frames than were produced (no re-processing stale).
    assert worker.ticks <= 8
    assert results, "worker produced no results"
    assert results == sorted(results)  # frames handled in arrival order


def test_recognition_worker_skips_none_results():
    slot = LatestSlot()
    slot.set(np.zeros((2, 2, 3), np.uint8))
    calls = []
    worker = RecognitionWorker(
        slot.get, process=lambda f: None, on_result=lambda r: calls.append(r),
        idle_sleep=0.002,
    ).start()
    time.sleep(0.05)
    worker.stop()
    assert calls == []  # None means "nothing to show" -> on_result not called
