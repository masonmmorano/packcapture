"""Dev-mode viewer: watch the whole pipeline run on a clip or live camera.

Left: the video with the live auto-ROI box and the current top match.
Right: a scrolling detection log plus the running pack/session tally.

This is the "watch the cards as they're tracked" tool — a debugging/tuning
harness, not a CI test (it opens a window). Run it on a recorded clip to replay
frame-for-frame, or on a webcam/OBS index for live. Use --save to render the
side-by-side to a video file instead of showing a window.

Dedupe here is a simple rip-mode heuristic: a card is logged once it has been
the accepted top match for `stable_frames` consecutive frames and differs from
the last logged card. (Zone mode's motion-settle dedupe lives in settle.py; this
viewer favors showing the live tracking.)
"""
from __future__ import annotations

from collections import deque
from typing import Optional, Union

import cv2
import numpy as np

from .capture.source import FrameSource
from .pipeline.confidence import ConfidenceGate
from .pipeline.roi import BoxSmoother, MotionFeatureROI
from .pipeline.session import Session, rarity_class
from .recognize.orb_matcher import Matcher
from .storage.bundle import load_bundle

PANEL_W = 520
VIEW_H = 540
BG = (24, 24, 24)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _put(img, text, org, scale=0.5, color=(230, 230, 230), thick=1):
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def _panel(session: Session, log: deque, live: str, live_color, frame_no: int) -> np.ndarray:
    p = np.full((VIEW_H, PANEL_W, 3), BG, np.uint8)
    _put(p, "PackCapture - DEV MODE", (16, 32), 0.7, (255, 255, 255), 2)
    _put(p, f"set {session.set_code}   frame {frame_no}", (16, 58), 0.5, (160, 160, 160))

    _put(p, "LIVE:", (16, 92), 0.55, (160, 160, 160))
    _put(p, live, (70, 92), 0.55, live_color, 2)

    st = session.stats()
    _put(p, f"packs {st['packs']}  (ok {st['packs_reconciled']} / flagged {st['packs_flagged']})"
            f"   pending {session.pending}/{session.pack_size}",
         (16, 122), 0.5, (200, 200, 120))
    cv2.line(p, (16, 138), (PANEL_W - 16, 138), (70, 70, 70), 1)

    y = 162
    for line, color in log:
        _put(p, line, (16, y), 0.48, color)
        y += 22
    return p


def _compose(frame: np.ndarray, roi, live: str, live_color,
             session: Session, log: deque, frame_no: int) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = VIEW_H / h
    left = cv2.resize(frame, (int(w * scale), VIEW_H))
    if roi is not None:
        x, y, rw, rh = (int(v * scale) for v in roi)
        cv2.rectangle(left, (x, y), (x + rw, y + rh), live_color, 2)
    panel = _panel(session, log, live, live_color, frame_no)
    return np.hstack([left, panel])


def run(
    source: Union[int, str],
    set_code: str,
    save: Optional[str] = None,
    stable_frames: int = 5,
    top: int = 5,
) -> int:
    matcher = Matcher(load_bundle(set_code))
    gate = ConfidenceGate()
    roi_detector = MotionFeatureROI()
    smoother = BoxSmoother()
    session = Session(set_code)
    log: deque = deque(maxlen=16)

    cur_id: Optional[str] = None
    cur_n = 0
    last_logged: Optional[str] = None

    writer = None
    show = save is None
    frame_no = 0

    with FrameSource(source).open() as src:
        for frame in src.frames():
            frame_no += 1
            roi = smoother.update(roi_detector.detect(frame))

            live, live_color = "(no card)", (120, 120, 120)
            if roi is not None:
                x, y, w, h = roi
                res = matcher.match_array(frame[y:y + h, x:x + w], top=top)
                decision = gate.evaluate(res)
                if res:
                    r = res[0]
                    if decision.accepted:
                        live = f"{r.name} #{r.number}  i{r.inliers}  ACCEPT"
                        live_color = (90, 220, 90)
                        cur_n = cur_n + 1 if r.card_id == cur_id else 1
                        cur_id = r.card_id
                        if cur_n == stable_frames and r.card_id != last_logged:
                            last_logged = r.card_id
                            card, pack = session.add(
                                card_id=r.card_id, name=r.name, number=r.number,
                                base_rarity=r.rarity, inliers=r.inliers,
                            )
                            log.appendleft((
                                f"+ {card.name} #{card.number}  {rarity_class(card.base_rarity)[:4]}"
                                f"  slot{card.slot} {card.variant[:3]}  i{card.inliers}",
                                (90, 220, 90),
                            ))
                            if pack is not None:
                                ok = "OK" if pack.reconciled else "FLAGGED"
                                col = (90, 220, 90) if pack.reconciled else (90, 90, 230)
                                log.appendleft((f"---- Pack {pack.index}: {ok} ----", col))
                    else:
                        live = f"{r.name} #{r.number}  i{r.inliers}  reject"
                        live_color = (90, 160, 230)
                        cur_id, cur_n = None, 0
            else:
                cur_id, cur_n = None, 0

            canvas = _compose(frame, roi, live, live_color, session, log, frame_no)

            if show:
                cv2.imshow("packcapture dev", canvas)
                if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                    break
            else:
                if writer is None:
                    fps = src.fps or 30.0
                    writer = cv2.VideoWriter(
                        save, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                        (canvas.shape[1], canvas.shape[0]),
                    )
                writer.write(canvas)

    if writer is not None:
        writer.release()
    if show:
        cv2.destroyAllWindows()

    pack = session.finalize()
    if pack is not None and not pack.reconciled:
        print(f"final partial pack flagged: {pack.issues}")
    st = session.stats()
    print(f"dev run done: {frame_no} frames, {st['cards_logged']} cards logged, "
          f"{st['packs']} pack(s).")
    return 0
