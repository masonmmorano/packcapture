"""Overlay (rip-mode) render: a price ticker + pack analytics on clean footage.

This is the "point the camera, fan the cards" front end — the same recognition
core as dev mode, but instead of a side-by-side debug panel it draws two
distinct overlays on the footage itself:

* **Price ticker** (top-right, under the facecam): the card currently
  recognized and its raw (ungraded) market price. Each new card *slides up* into
  place — the lightweight "what's this worth" read, mirroring a stream price
  overlay.
* **Pack analytics** (fixed, bottom-right): PackCapture's own running tally —
  session value, pack count and status breakdown, cards logged. This is the part
  the competitor doesn't have.

A per-card / per-pack analytics JSON is written alongside the render (``--export``).

Run on a recorded clip or a live webcam/OBS index. ``--save`` renders headless
to a video file (re-encoded to H.264); otherwise a window shows the live overlay.
Prices come from the bundle — run ``packcapture fetch-prices <code>`` first.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Optional, Union

import cv2
import numpy as np

from .capture.source import FrameSource
from .mediautil import to_h264
from .pipeline.boundary import PACK_END, BoundaryConfig, BoundaryDetector
from .pipeline.confidence import ConfidenceGate, GateConfig
from .pipeline.roi import BoxSmoother, MotionFeatureROI
from .pipeline.session import RARITY_RARE_PLUS, Session, rarity_class
from .recognize.orb_matcher import Matcher
from .storage.bundle import load_bundle

FONT = cv2.FONT_HERSHEY_SIMPLEX
# Palette (BGR).
INK = (236, 236, 236)
MUTED = (150, 150, 150)
PRICE = (90, 220, 120)      # green dollar figure
GOLD = (60, 200, 250)       # rare+ "hit" highlight
ACCENT = (60, 180, 250)     # amber accent bar (set badge)
PANEL = (18, 18, 18)

TICKER_ANIM_S = 0.40        # seconds for a new card to slide up into place


@dataclass
class OverlayState:
    set_name: str
    # Price ticker (current card).
    card_name: str = ""
    card_number: str = ""
    price: Optional[float] = None
    variant: str = ""
    is_hit: bool = False
    last_log_frame: int = -1            # frame the current card was logged (drives the slide)
    # Pack analytics.
    total: float = 0.0
    count: int = 0
    packs: int = 0
    by_status: dict = field(default_factory=dict)
    last_pack_label: str = ""


def _money(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _put(img, text, org, scale, color, thick=1):
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def _blend_rect(img, x, y, w, h, color, alpha):
    x2, y2 = min(x + w, img.shape[1]), min(y + h, img.shape[0])
    x, y = max(x, 0), max(y, 0)
    if x2 <= x or y2 <= y:
        return
    sub = img[y:y2, x:x2]
    rect = np.full_like(sub, color)
    cv2.addWeighted(rect, alpha, sub, 1 - alpha, 0, sub)
    img[y:y2, x:x2] = sub


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _render_ticker(img, x, y, bw, bh, st, s):
    """Draw the price ticker panel at top-left (x, y)."""
    accent = GOLD if st.is_hit else ACCENT
    price_color = GOLD if st.is_hit else PRICE
    pad = int(18 * s)

    _blend_rect(img, x, y, bw, bh, PANEL, 0.82)
    cv2.rectangle(img, (x, y), (x + bw, y + bh), (70, 70, 70), max(1, int(s)))
    cv2.rectangle(img, (x, y), (x + int(6 * s), y + bh), accent, -1)  # accent stripe

    cx = x + pad + int(6 * s)
    cy = y + pad + int(22 * s)

    _put(img, (st.card_name or "scanning…")[:24], (cx, cy), 0.62 * s, INK, max(1, int(1.6 * s)))
    if st.is_hit:
        tag = "HIT"
        (tw, _), _ = cv2.getTextSize(tag, FONT, 0.45 * s, max(1, int(1.4 * s)))
        _blend_rect(img, x + bw - pad - tw - int(10 * s), y + pad - int(4 * s),
                    tw + int(12 * s), int(22 * s), GOLD, 0.9)
        _put(img, tag, (x + bw - pad - tw - int(4 * s), y + pad + int(13 * s)),
             0.45 * s, (20, 20, 20), max(1, int(1.4 * s)))
    if st.card_number:
        _put(img, f"#{st.card_number}  {st.variant}", (cx, cy + int(20 * s)), 0.5 * s, MUTED)

    py = cy + int(46 * s)
    _put(img, _money(st.price), (cx, py), 1.15 * s, price_color, max(2, int(2.4 * s)))
    _put(img, "RAW", (cx + int(2 * s), py + int(18 * s)), 0.4 * s, MUTED)


def draw_price_ticker(img, st, frame_idx, fps, facecam_h_frac=0.30):
    """Blend the (animated) price ticker into `img` in place."""
    if not st.card_name:
        return
    H, W = img.shape[:2]
    s = H / 720.0
    pad = int(18 * s)
    bw = int(W * 0.32)
    bh = pad + int(34 * s) + int(46 * s) + int(20 * s) + pad
    margin = int(16 * s)
    x = W - bw - margin
    y_rest = int(H * facecam_h_frac) + int(10 * s)

    # Slide-up + fade-in for each newly logged card.
    dur = max(1.0, fps * TICKER_ANIM_S)
    p = 1.0 if st.last_log_frame < 0 else _clamp01((frame_idx - st.last_log_frame) / dur)
    eased = 1 - (1 - p) ** 3
    slide = int(bh * 0.6)
    y_draw = y_rest + int((1 - eased) * slide)

    layer = img.copy()
    _render_ticker(layer, x, y_draw, bw, bh, st, s)
    y0, y1 = y_rest, min(y_rest + bh + slide, H)
    x1 = min(x + bw, W)
    reg_img, reg_layer = img[y0:y1, x:x1], layer[y0:y1, x:x1]
    cv2.addWeighted(reg_layer, eased, reg_img, 1 - eased, 0, reg_img)


def draw_analytics(img, st):
    """Blend the fixed pack-analytics panel into the bottom-right of `img`."""
    H, W = img.shape[:2]
    s = H / 720.0
    pad = int(16 * s)
    bw = int(W * 0.30)
    bh = int(196 * s)
    margin = int(16 * s)
    x = W - bw - margin
    y = H - bh - margin

    _blend_rect(img, x, y, bw, bh, PANEL, 0.82)
    cv2.rectangle(img, (x, y), (x + bw, y + bh), (70, 70, 70), max(1, int(s)))
    cx = x + pad
    _put(img, "PACK ANALYTICS", (cx, y + pad + int(16 * s)), 0.52 * s, INK, max(1, int(1.4 * s)))
    _put(img, st.set_name.upper()[:24], (cx, y + pad + int(36 * s)), 0.42 * s, MUTED)

    # Big session value.
    vy = y + pad + int(78 * s)
    _put(img, "SESSION VALUE", (cx, vy - int(20 * s)), 0.42 * s, MUTED)
    _put(img, _money(st.total), (cx, vy), 1.0 * s, PRICE, max(2, int(2 * s)))

    cv2.line(img, (cx, vy + int(14 * s)), (x + bw - pad, vy + int(14 * s)), (70, 70, 70), max(1, int(s)))

    # Counts row.
    ry = vy + int(40 * s)
    _put(img, f"{st.packs} packs", (cx, ry), 0.5 * s, INK, max(1, int(1.3 * s)))
    _put(img, f"{st.count} cards", (cx + int(bw * 0.42), ry), 0.5 * s, INK, max(1, int(1.3 * s)))

    # Status breakdown.
    bs = st.by_status or {}
    sy = ry + int(28 * s)
    parts = [
        (f"COMPLETE {bs.get('complete', 0)}", (90, 220, 120)),
        (f"SPEED {bs.get('speed_ripped', 0)}", (60, 200, 250)),
        (f"NOHIT {bs.get('no_hit', 0)}", MUTED),
    ]
    px = cx
    for text, color in parts:
        _put(img, text, (px, sy), 0.42 * s, color, max(1, int(s)))
        (tw, _), _ = cv2.getTextSize(text, FONT, 0.42 * s, max(1, int(s)))
        px += tw + int(16 * s)

    if st.last_pack_label:
        _put(img, st.last_pack_label[:32], (cx, sy + int(24 * s)), 0.42 * s, MUTED)


def draw_overlay(frame, st, frame_idx=0, fps=30.0, facecam_h_frac=0.30):
    """Draw both overlays onto a copy of `frame` and return it (used in tests/probes)."""
    out = frame.copy()
    draw_price_ticker(out, st, frame_idx, fps, facecam_h_frac)
    draw_analytics(out, st)
    return out


def run(
    source: Union[int, str],
    set_code: str,
    save: Optional[str] = None,
    export: Optional[str] = None,
    stable_frames: int = 5,
    min_inliers: int = 25,
    top: int = 5,
    evidence_inliers: int = 15,
    facecam_frac: float = 0.30,
) -> int:
    bundle = load_bundle(set_code)
    matcher = Matcher(bundle)
    gate = ConfidenceGate(GateConfig(min_inliers=min_inliers))
    roi_detector = MotionFeatureROI()
    smoother = BoxSmoother()
    session = Session(set_code)

    price_map = {r["card_id"]: (r.get("price"), r.get("price_variant") or "") for r in bundle.rows}
    priced = sum(1 for p, _ in price_map.values() if p is not None)
    if priced == 0:
        print(f"warning: bundle '{set_code}' has no prices — run `packcapture fetch-prices {set_code}` first.")
    set_name = bundle.manifest.get("set_name") or set_code.upper()
    st = OverlayState(set_name=set_name)

    cur_id: Optional[str] = None
    cur_n = 0
    last_logged: Optional[str] = None

    writer = None
    show = save is None
    frame_no = 0
    fps = 30.0
    boundary: Optional[BoundaryDetector] = None

    with FrameSource(source).open() as src:
        fps = src.fps or 30.0
        for frame in src.frames():
            if boundary is None:
                boundary = BoundaryDetector(BoundaryConfig(fps=fps))
            frame_no += 1
            roi = smoother.update(roi_detector.detect(frame))
            motion = roi_detector.last_motion

            card_seen = False
            if roi is not None:
                x, y, w, h = roi
                res = matcher.match_array(frame[y:y + h, x:x + w], top=top)
                decision = gate.evaluate(res)
                if res:
                    r = res[0]
                    card_seen = r.inliers >= evidence_inliers
                    if decision.accepted:
                        cur_n = cur_n + 1 if r.card_id == cur_id else 1
                        cur_id = r.card_id
                        if cur_n == stable_frames and r.card_id != last_logged:
                            last_logged = r.card_id
                            session.add(
                                card_id=r.card_id, name=r.name, number=r.number,
                                base_rarity=r.rarity, inliers=r.inliers,
                            )
                            price, variant = price_map.get(r.card_id, (None, ""))
                            st.card_name, st.card_number = r.name, r.number
                            st.price, st.variant = price, variant
                            st.is_hit = rarity_class(r.rarity) == RARITY_RARE_PLUS
                            st.last_log_frame = frame_no
                            st.count += 1
                            if price is not None:
                                st.total += price
                    else:
                        cur_id, cur_n = None, 0
            else:
                cur_id, cur_n = None, 0

            ev = boundary.update(card_seen, motion)
            if ev == PACK_END:
                pack = session.close_pack()
                last_logged = None
                if pack is not None:
                    st.last_pack_label = f"Pack {pack.index}: {pack.status.upper()} ({len(pack.cards)})"
            st.packs = len(session.packs)
            st.by_status = session.stats()["by_status"]

            canvas = draw_overlay(frame, st, frame_idx=frame_no, fps=fps, facecam_h_frac=facecam_frac)

            if show:
                cv2.imshow("packcapture overlay", canvas)
                if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                    break
            else:
                if writer is None:
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

    session.finalize()
    report = _build_report(session, price_map, set_code, set_name, source)
    if export:
        with open(export, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote analytics export: {export}")

    t = report["totals"]
    print(f"overlay run done: {frame_no} frames, {t['cards']} cards, "
          f"{t['packs']} pack(s), total raw value {_money(t['total_raw_value'])}.")
    return 0


def _build_report(session: Session, price_map, set_code, set_name, source) -> dict:
    packs = []
    grand_total = 0.0
    card_count = 0
    for pack in session.packs:
        cards = []
        pack_total = 0.0
        for c in pack.cards:
            price, variant = price_map.get(c.card_id, (None, ""))
            if price is not None:
                pack_total += price
            cards.append({
                "name": c.name, "number": c.number, "card_id": c.card_id,
                "base_rarity": c.base_rarity, "rarity_class": rarity_class(c.base_rarity),
                "variant": c.variant, "slot": c.slot, "inliers": c.inliers,
                "price": price, "price_variant": variant,
            })
        card_count += len(cards)
        grand_total += pack_total
        packs.append({
            "index": pack.index, "status": pack.status, "reconciled": pack.reconciled,
            "raw_value": round(pack_total, 2), "card_count": len(cards),
            "issues": pack.issues, "cards": cards,
        })

    st = session.stats()
    return {
        "set_code": set_code,
        "set_name": set_name,
        "source": str(source),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "cards": card_count,
            "packs": len(packs),
            "total_raw_value": round(grand_total, 2),
            "avg_pack_value": round(grand_total / len(packs), 2) if packs else 0.0,
            "by_status": st["by_status"],
        },
        "packs": packs,
    }
