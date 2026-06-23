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
from .mediautil import to_h264
from .pipeline.boundary import DETECTING_PACK, PACK_END, PACK_START, BoundaryConfig, BoundaryDetector
from .pipeline.confidence import ConfidenceGate, GateConfig
from .pipeline.roi import BoxSmoother, MotionFeatureROI
from .pipeline.session import Session, is_tracked_supertype, rarity_class
from .recognize.orb_matcher import Matcher
from .storage.bundle import load_bundle

PANEL_W = 520
VIEW_H = 540
BG = (24, 24, 24)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _put(img, text, org, scale=0.5, color=(230, 230, 230), thick=1):
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def _panel(session: Session, log: deque, live: str, live_color, frame_no: int,
           state: str, motion: float) -> np.ndarray:
    p = np.full((VIEW_H, PANEL_W, 3), BG, np.uint8)
    _put(p, "PackCapture - DEV MODE", (16, 32), 0.7, (255, 255, 255), 2)
    _put(p, f"set {session.set_code}   frame {frame_no}", (16, 58), 0.5, (160, 160, 160))

    # Pack-boundary state machine: DETECTING_PACK (cards in play) vs WAITING_FOR_PACK.
    detecting = state == DETECTING_PACK
    st_color = (90, 220, 90) if detecting else (160, 160, 160)
    _put(p, ("DETECTING PACK" if detecting else "WAITING FOR PACK") + f"   motion {motion:.2f}",
         (220, 58), 0.5, st_color, 2 if detecting else 1)

    _put(p, "LIVE:", (16, 92), 0.55, (160, 160, 160))
    _put(p, live, (70, 92), 0.55, live_color, 2)

    st = session.stats()
    bs = st["by_status"]
    _put(p, f"packs {st['packs']}  (C{bs['complete']}/S{bs['speed_ripped']}/N{bs['no_hit']}"
            f" flag {st['packs_flagged']})   pending {session.pending}/{session.pack_size}",
         (16, 122), 0.5, (200, 200, 120))
    cv2.line(p, (16, 138), (PANEL_W - 16, 138), (70, 70, 70), 1)

    y = 162
    for line, color in log:
        _put(p, line, (16, y), 0.48, color)
        y += 22
    return p


def _compose(frame: np.ndarray, roi, live: str, live_color,
             session: Session, log: deque, frame_no: int,
             state: str, motion: float) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = VIEW_H / h
    left = cv2.resize(frame, (int(w * scale), VIEW_H))
    if roi is not None:
        x, y, rw, rh = (int(v * scale) for v in roi)
        cv2.rectangle(left, (x, y), (x + rw, y + rh), live_color, 2)
    # State tag on the video itself so it reads even in a cropped screenshot.
    tag_color = (90, 220, 90) if state == DETECTING_PACK else (140, 140, 140)
    _put(left, "DETECTING" if state == DETECTING_PACK else "WAITING",
         (12, 28), 0.65, tag_color, 2)
    panel = _panel(session, log, live, live_color, frame_no, state, motion)
    return np.hstack([left, panel])


def run(
    source: Union[int, str],
    set_code: str,
    save: Optional[str] = None,
    stable_frames: int = 5,
    min_inliers: int = 25,
    top: int = 5,
    evidence_inliers: int = 15,
) -> int:
    matcher = Matcher(load_bundle(set_code))
    gate = ConfidenceGate(GateConfig(min_inliers=min_inliers))
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
    boundary: Optional[BoundaryDetector] = None

    with FrameSource(source).open() as src:
        for frame in src.frames():
            if boundary is None:
                boundary = BoundaryDetector(BoundaryConfig(fps=src.fps or 30.0))
            frame_no += 1
            roi = smoother.update(roi_detector.detect(frame))
            motion = roi_detector.last_motion

            live, live_color = "(no card)", (120, 120, 120)
            card_seen = False
            if roi is not None:
                x, y, w, h = roi
                res = matcher.match_array(frame[y:y + h, x:x + w], top=top)
                decision = gate.evaluate(res)
                if res:
                    r = res[0]
                    # Presence evidence for the boundary machine: softer than the
                    # logging gate, so fast-fanned cards still count as "a card
                    # is in play" even when not confidently identified.
                    card_seen = r.inliers >= evidence_inliers
                    if decision.accepted and not is_tracked_supertype(r.supertype):
                        # Inserted basic energy false-matches the set's energy
                        # card: present, but never logged toward the pack.
                        live = f"{r.name} #{r.number}  i{r.inliers}  energy/skip"
                        live_color = (90, 160, 230)
                        if r.card_id != cur_id:
                            log.appendleft((
                                f"~ {r.name} #{r.number}  energy (excluded)", (150, 150, 150),
                            ))
                        cur_id, cur_n = r.card_id, 0
                    elif decision.accepted:
                        live = f"{r.name} #{r.number}  i{r.inliers}  ACCEPT"
                        live_color = (90, 220, 90)
                        cur_n = cur_n + 1 if r.card_id == cur_id else 1
                        cur_id = r.card_id
                        if cur_n == stable_frames and r.card_id != last_logged:
                            last_logged = r.card_id
                            card = session.add(
                                card_id=r.card_id, name=r.name, number=r.number,
                                base_rarity=r.rarity, inliers=r.inliers,
                            )
                            log.appendleft((
                                f"+ {card.name} #{card.number}  {rarity_class(card.base_rarity)[:4]}"
                                f"  slot{card.slot} {card.variant[:3]}  i{card.inliers}",
                                (90, 220, 90),
                            ))
                    else:
                        live = f"{r.name} #{r.number}  i{r.inliers}  reject"
                        live_color = (90, 160, 230)
                        cur_id, cur_n = None, 0
            else:
                cur_id, cur_n = None, 0

            ev = boundary.update(card_seen, motion)
            if ev == PACK_START:
                log.appendleft((">> pack started", (200, 200, 120)))
            elif ev == PACK_END:
                pack = session.close_pack()
                last_logged = None  # next pack may open on the same card art
                if pack is not None:
                    col = (90, 220, 90) if pack.status == "complete" else (200, 200, 120)
                    log.appendleft((
                        f"---- Pack {pack.index}: {pack.status.upper()}"
                        f" ({len(pack.cards)} cards) ----", col,
                    ))

            canvas = _compose(frame, roi, live, live_color, session, log, frame_no,
                              boundary.state, motion)

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
        to_h264(save)
    if show:
        cv2.destroyAllWindows()

    # No boundary detector wired in yet, so the whole run closes as one segment.
    pack = session.finalize()
    if pack is not None:
        print(f"final pack {pack.index}: {pack.status}"
              + (f" issues: {pack.issues}" if pack.issues else ""))
    st = session.stats()
    bs = st["by_status"]
    print(f"dev run done: {frame_no} frames, {st['cards_logged']} cards logged, "
          f"{st['packs']} pack(s) "
          f"(complete {bs['complete']} / speed {bs['speed_ripped']} / no-hit {bs['no_hit']}).")
    return 0
