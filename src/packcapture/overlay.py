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
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Union

import cv2
import numpy as np

from .capture.source import FrameSource
from .capture.threaded import RecognitionWorker, ThreadedFrameSource
from .config import set_dir
from .mediautil import to_h264
from .pipeline.boundary import PACK_END, BoundaryConfig, BoundaryDetector
from .pipeline.confidence import ConfidenceGate, GateConfig
from .pipeline.roi import BoxSmoother, MotionFeatureROI
from .pipeline.session import RARITY_RARE_PLUS, Session, is_tracked_supertype, rarity_class
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
# Red -> orange accent-stripe gradient (BGR), top to bottom.
GRAD_TOP = (45, 45, 235)    # red
GRAD_BOT = (50, 150, 255)   # orange

# Per-tier rarity colors (BGR), grey commons -> gold chase cards.
_RARITY_COLORS = {
    "common": (165, 165, 165),
    "uncommon": (170, 190, 150),
    "rare": (220, 190, 120),
    "double rare": (235, 180, 80),
    "ultra rare": (215, 130, 215),
    "illustration rare": (205, 210, 90),
    "special illustration rare": (60, 200, 250),
    "mega hyper rare": (190, 90, 235),
}


def _rarity_color(rarity: str):
    return _RARITY_COLORS.get((rarity or "").strip().lower(), INK)

TICKER_ANIM_S = 0.40        # seconds for a new card to slide up into place
HIT_PRICE = 1.50            # a rare+ only earns the gold HIT tag above this raw price

# Session slot-variant -> ticker display text.
_VARIANT_LABELS = {"reverse": "reverse holo", "normal": "normal", "unknown": ""}


def _variant_label(variant: str) -> str:
    return _VARIANT_LABELS.get(variant, variant or "")


@dataclass
class OverlayState:
    set_name: str
    # Price ticker (current card).
    card_name: str = ""
    card_number: str = ""
    price: Optional[float] = None
    variant: str = ""
    rarity: str = ""
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


def _gradient_rect(img, x, y, w, h, top_color, bottom_color):
    """Fill a rectangle with a vertical top->bottom color gradient (in place)."""
    x2, y2 = min(x + w, img.shape[1]), min(y + h, img.shape[0])
    x, y = max(x, 0), max(y, 0)
    if x2 <= x or y2 <= y:
        return
    ramp = np.linspace(0.0, 1.0, y2 - y, dtype=np.float32)[:, None]
    top = np.array(top_color, np.float32)
    bot = np.array(bottom_color, np.float32)
    col = (top * (1 - ramp) + bot * ramp).astype(np.uint8)   # (h, 3)
    img[y:y2, x:x2] = col[:, None, :]


def _ticker_geom(W, H, facecam_frac, origin):
    """Resting box of the price ticker -> (x, y, bw, bh, scale).

    `origin` overrides the auto top-left (used when the user has dragged it).
    """
    s = H / 720.0
    bw = int(W * 0.32)
    # Height must enclose the four lines laid out in _render_ticker:
    # name (pad+26) -> rarity (+24) -> sub (+22) -> price (+46) -> bottom pad.
    bh = int(160 * s)
    margin = int(16 * s)
    if origin is not None:
        x, y = int(origin[0]), int(origin[1])
    else:
        x = W - bw - margin
        y = int(H * facecam_frac) + int(10 * s)
    return x, y, bw, bh, s


def _analytics_geom(W, H, origin):
    """Box of the pack-analytics panel -> (x, y, bw, bh, scale)."""
    s = H / 720.0
    bw = int(W * 0.30)
    bh = int(228 * s)   # encloses title/set/value/counts/status/last-pack; keep in sync
    margin = int(16 * s)
    if origin is not None:
        x, y = int(origin[0]), int(origin[1])
    else:
        x = W - bw - margin
        y = H - bh - margin
    return x, y, bw, bh, s


def _render_ticker(img, x, y, bw, bh, st, s):
    """Draw the price ticker panel at top-left (x, y)."""
    price_color = GOLD if st.is_hit else PRICE
    pad = int(16 * s)

    _blend_rect(img, x, y, bw, bh, PANEL, 0.82)
    cv2.rectangle(img, (x, y), (x + bw, y + bh), (70, 70, 70), max(1, int(s)))
    _gradient_rect(img, x, y, int(6 * s), bh, GRAD_TOP, GRAD_BOT)  # red->orange accent stripe

    cx = x + pad + int(10 * s)

    # HIT chip (top-right), measured so the name can be clipped clear of it.
    name_limit = x + bw - pad
    if st.is_hit:
        tag, ts, tt = "HIT", 0.5 * s, max(1, int(1.5 * s))
        (tw, th), _ = cv2.getTextSize(tag, FONT, ts, tt)
        chip_w, chip_h = tw + int(16 * s), th + int(12 * s)
        chip_x, chip_y = x + bw - pad - chip_w, y + pad
        _blend_rect(img, chip_x, chip_y, chip_w, chip_h, GOLD, 0.95)
        _put(img, tag, (chip_x + int(8 * s), chip_y + th + int(6 * s)), ts, (20, 20, 20), tt)
        name_limit = chip_x - int(10 * s)

    # Line 1: card name, clipped to the space left of the HIT chip.
    y_name = y + pad + int(26 * s)
    name = st.card_name or "scanning…"
    name_scale, name_thick = 0.64 * s, max(1, int(1.6 * s))
    while name and cv2.getTextSize(name, FONT, name_scale, name_thick)[0][0] > name_limit - cx:
        name = name[:-1]
    _put(img, name, (cx, y_name), name_scale, INK, name_thick)

    # Line 2: rarity, color-coded by tier.
    y_rar = y_name + int(24 * s)
    if st.rarity:
        _put(img, st.rarity, (cx, y_rar), 0.5 * s, _rarity_color(st.rarity), max(1, int(1.2 * s)))

    # Line 3: number · variant.
    y_sub = y_rar + int(22 * s)
    if st.card_number:
        sub = f"#{st.card_number}" + (f"   {st.variant}" if st.variant else "")
        _put(img, sub, (cx, y_sub), 0.44 * s, MUTED, max(1, int(s)))

    # Line 4: price (big) with a small RAW label trailing it.
    y_price = y_sub + int(46 * s)
    price_txt = _money(st.price)
    price_scale, price_thick = 1.05 * s, max(2, int(2.3 * s))
    _put(img, price_txt, (cx, y_price), price_scale, price_color, price_thick)
    (pw, _), _ = cv2.getTextSize(price_txt, FONT, price_scale, price_thick)
    _put(img, "RAW", (cx + pw + int(12 * s), y_price), 0.42 * s, MUTED, max(1, int(s)))


def draw_price_ticker(img, st, frame_idx, fps, facecam_h_frac=0.30, origin=None):
    """Blend the (animated) price ticker into `img` in place.

    `origin` (top-left x, y) overrides the auto position when the user has
    dragged the panel; otherwise it sits top-right under the facecam.
    """
    if not st.card_name:
        return
    H, W = img.shape[:2]
    x, y_rest, bw, bh, s = _ticker_geom(W, H, facecam_h_frac, origin)
    x = max(x, 0)

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


def draw_analytics(img, st, origin=None):
    """Blend the pack-analytics panel into `img` (bottom-right, or `origin`)."""
    H, W = img.shape[:2]
    x, y, bw, bh, s = _analytics_geom(W, H, origin)
    pad = int(16 * s)

    _blend_rect(img, x, y, bw, bh, PANEL, 0.82)
    cv2.rectangle(img, (x, y), (x + bw, y + bh), (70, 70, 70), max(1, int(s)))
    _gradient_rect(img, x, y, int(6 * s), bh, GRAD_TOP, GRAD_BOT)  # red->orange stripe (matches ticker)
    cx = x + pad + int(10 * s)
    _put(img, "PACK ANALYTICS", (cx, y + pad + int(20 * s)), 0.52 * s, INK, max(1, int(1.4 * s)))
    _put(img, st.set_name.upper()[:24], (cx, y + pad + int(40 * s)), 0.42 * s, MUTED)

    # Big session value — label sits well clear above the tall figure.
    _put(img, "SESSION VALUE", (cx, y + pad + int(66 * s)), 0.42 * s, MUTED)
    vy = y + pad + int(102 * s)
    _put(img, _money(st.total), (cx, vy), 1.0 * s, PRICE, max(2, int(2 * s)))

    ly = vy + int(16 * s)
    cv2.line(img, (cx, ly), (x + bw - pad, ly), (70, 70, 70), max(1, int(s)))

    # Counts row.
    ry = ly + int(28 * s)
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
        _put(img, st.last_pack_label[:32], (cx, sy + int(26 * s)), 0.42 * s, MUTED)


def draw_overlay(frame, st, frame_idx=0, fps=30.0, facecam_h_frac=0.30,
                 ticker_origin=None, analytics_origin=None):
    """Draw both overlays onto a copy of `frame` and return it (used in tests/probes)."""
    out = frame.copy()
    draw_price_ticker(out, st, frame_idx, fps, facecam_h_frac, origin=ticker_origin)
    draw_analytics(out, st, origin=analytics_origin)
    return out


class LayoutDrag:
    """Mouse-drag controller for positioning the two overlay panels (live mode).

    Holds the current top-left origins of the ticker and analytics panels and
    relocates whichever one is grabbed. Origins are clamped inside the frame.
    Set ``dirty`` when the user has moved something (so the layout is saved).
    """

    def __init__(self, ticker_origin=None, analytics_origin=None, facecam_frac=0.30):
        self.ticker = list(ticker_origin) if ticker_origin else None
        self.analytics = list(analytics_origin) if analytics_origin else None
        self.facecam = facecam_frac
        self.W = self.H = 0
        self._drag = None  # (panel_name, grab_dx, grab_dy)
        self.dirty = False

    def ensure(self, W, H):
        """Fill in default origins once the frame size is known."""
        self.W, self.H = W, H
        if self.ticker is None:
            x, y, _, _, _ = _ticker_geom(W, H, self.facecam, None)
            self.ticker = [x, y]
        if self.analytics is None:
            x, y, _, _, _ = _analytics_geom(W, H, None)
            self.analytics = [x, y]

    def _boxes(self):
        t = _ticker_geom(self.W, self.H, self.facecam, tuple(self.ticker))
        a = _analytics_geom(self.W, self.H, tuple(self.analytics))
        return t, a  # each (x, y, bw, bh, s)

    def on_mouse(self, event, x, y, flags, param):
        (tx, ty, tw, th, _), (ax, ay, aw, ah, _) = self._boxes()
        if event == cv2.EVENT_LBUTTONDOWN:
            if ax <= x <= ax + aw and ay <= y <= ay + ah:
                self._drag = ("analytics", x - ax, y - ay)
            elif tx <= x <= tx + tw and ty <= y <= ty + th:
                self._drag = ("ticker", x - tx, y - ty)
        elif event == cv2.EVENT_MOUSEMOVE and self._drag is not None:
            name, gdx, gdy = self._drag
            origin = self.ticker if name == "ticker" else self.analytics
            bw, bh = (tw, th) if name == "ticker" else (aw, ah)
            origin[0] = max(0, min(x - gdx, self.W - bw))
            origin[1] = max(0, min(y - gdy, self.H - bh))
            self.dirty = True
        elif event == cv2.EVENT_LBUTTONUP:
            self._drag = None


def _layout_path(code):
    return set_dir(code) / "overlay_layout.json"


def load_layout(code):
    """Return (ticker_origin, analytics_origin) from disk, or (None, None)."""
    p = _layout_path(code)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("ticker"), d.get("analytics")
        except (ValueError, OSError):
            pass
    return None, None


def save_layout(code, ticker_origin, analytics_origin):
    p = _layout_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"ticker": list(ticker_origin), "analytics": list(analytics_origin)}, indent=2),
        encoding="utf-8",
    )
    return p


class OverlayEngine:
    """The per-frame recognition + overlay-state update, independent of the loop.

    Pulled out of ``run`` so the same logic drives both the serial render path
    (one thread, a frame counter as the clock) and the live threaded path (a
    recognition worker, a wall-clock tick as the clock). It owns the recognition
    state and the :class:`OverlayState`; ``process`` mutates them, and
    ``snapshot`` returns a lock-guarded copy for a display thread to draw.

    ``last_log_frame`` and the ``clock`` passed to ``process`` must come from the
    same monotonically-increasing units measured at ``boundary_fps``/draw fps, so
    the ticker slide animation reads the same in either path.
    """

    def __init__(
        self,
        matcher: "Matcher",
        gate: ConfidenceGate,
        session: Session,
        price_map: dict,
        state: OverlayState,
        *,
        boundary_fps: float,
        top: int = 5,
        stable_frames: int = 5,
        evidence_inliers: int = 15,
    ):
        self.matcher = matcher
        self.gate = gate
        self.session = session
        self.price_map = price_map
        self.st = state
        self.top = top
        self.stable_frames = stable_frames
        self.evidence_inliers = evidence_inliers
        self.roi_detector = MotionFeatureROI()
        self.smoother = BoxSmoother()
        self.boundary = BoundaryDetector(BoundaryConfig(fps=boundary_fps))
        self._cur_id: Optional[str] = None
        self._cur_n = 0
        self._last_logged: Optional[str] = None
        self._lock = threading.Lock()

    def process(self, frame: np.ndarray, clock: Callable[[], int]) -> None:
        """Recognize one frame and fold the result into the overlay state.

        ``clock`` is sampled *at the moment a card is logged* (not at call time),
        so in the threaded path the ~300 ms recognition latency doesn't eat the
        ticker's slide window — ``last_log_frame`` reflects when the card became
        visible, and the display's next draw sees the slide from its start.
        """
        st = self.st
        roi = self.smoother.update(self.roi_detector.detect(frame))
        motion = self.roi_detector.last_motion

        card_seen = False
        if roi is not None:
            x, y, w, h = roi
            res = self.matcher.match_array(frame[y:y + h, x:x + w], top=self.top)
            decision = self.gate.evaluate(res)
            if res:
                r = res[0]
                card_seen = r.inliers >= self.evidence_inliers
                # The inserted basic energy false-matches the set's energy card;
                # it's a real card in frame (keeps card_seen) but is never logged.
                if decision.accepted and not is_tracked_supertype(r.supertype):
                    self._cur_id, self._cur_n = None, 0
                elif decision.accepted:
                    self._cur_n = self._cur_n + 1 if r.card_id == self._cur_id else 1
                    self._cur_id = r.card_id
                    if self._cur_n == self.stable_frames and r.card_id != self._last_logged:
                        self._last_logged = r.card_id
                        card = self.session.add(
                            card_id=r.card_id, name=r.name, number=r.number,
                            base_rarity=r.rarity, inliers=r.inliers,
                        )
                        price, _ = self.price_map.get(r.card_id, (None, ""))
                        with self._lock:
                            st.card_name, st.card_number = r.name, r.number
                            st.price = price
                            st.rarity = r.rarity
                            # Show the slot variant (reverse holo by position).
                            st.variant = _variant_label(card.variant)
                            st.is_hit = (rarity_class(r.rarity) == RARITY_RARE_PLUS
                                         and price is not None and price > HIT_PRICE)
                            st.last_log_frame = clock()
                            st.count += 1
                            if price is not None:
                                st.total += price
                else:
                    self._cur_id, self._cur_n = None, 0
        else:
            self._cur_id, self._cur_n = None, 0

        ev = self.boundary.update(card_seen, motion)
        with self._lock:
            if ev == PACK_END:
                pack = self.session.close_pack()
                self._last_logged = None
                if pack is not None:
                    st.last_pack_label = f"Pack {pack.index}: {pack.status.upper()} ({len(pack.cards)})"
            st.packs = len(self.session.packs)
            st.by_status = self.session.stats()["by_status"]

    def snapshot(self) -> OverlayState:
        """A consistent copy of the overlay state for a display thread to draw."""
        with self._lock:
            return replace(self.st)


def build_engine(
    set_code: str,
    *,
    boundary_fps: float,
    min_inliers: int = 25,
    stable_frames: int = 5,
    evidence_inliers: int = 15,
    top: int = 5,
):
    """Load a bundle and wire up an :class:`OverlayEngine`.

    Returns ``(engine, price_map, set_name)``; the engine owns the session
    (``engine.session``) and overlay state (``engine.st``). Shared by the live
    window and the browser-overlay server so they stay in lockstep.
    """
    bundle = load_bundle(set_code)
    matcher = Matcher(bundle)
    gate = ConfidenceGate(GateConfig(min_inliers=min_inliers))
    price_map = {r["card_id"]: (r.get("price"), r.get("price_variant") or "") for r in bundle.rows}
    if not any(p is not None for p, _ in price_map.values()):
        print(f"warning: bundle '{set_code}' has no prices — run `packcapture fetch-prices {set_code}` first.")
    set_name = bundle.manifest.get("set_name") or set_code.upper()
    engine = OverlayEngine(
        matcher, gate, Session(set_code), price_map, OverlayState(set_name=set_name),
        boundary_fps=boundary_fps, top=top, stable_frames=stable_frames,
        evidence_inliers=evidence_inliers,
    )
    return engine, price_map, set_name


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
    reset_layout: bool = False,
) -> int:
    bundle = load_bundle(set_code)
    matcher = Matcher(bundle)
    gate = ConfidenceGate(GateConfig(min_inliers=min_inliers))
    session = Session(set_code)

    price_map = {r["card_id"]: (r.get("price"), r.get("price_variant") or "") for r in bundle.rows}
    priced = sum(1 for p, _ in price_map.values() if p is not None)
    if priced == 0:
        print(f"warning: bundle '{set_code}' has no prices — run `packcapture fetch-prices {set_code}` first.")
    set_name = bundle.manifest.get("set_name") or set_code.upper()
    st = OverlayState(set_name=set_name)

    writer = None
    show = save is None
    frame_no = 0
    fps = 30.0

    # Overlay positions: saved per-set layout drives both live and headless renders.
    # In live mode the two panels are mouse-draggable and the new layout is saved.
    ticker_origin, analytics_origin = (None, None) if reset_layout else load_layout(set_code)
    drag = LayoutDrag(ticker_origin, analytics_origin, facecam_frac) if show else None
    win = "packcapture overlay"
    if show:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(win, drag.on_mouse)
        print("live overlay: drag either panel to reposition; 's' saves the layout, "
              "'r' resets it, 'q'/Esc quits.")

    with FrameSource(source).open() as src:
        fps = src.fps or 30.0
        engine = OverlayEngine(
            matcher, gate, session, price_map, st, boundary_fps=fps,
            top=top, stable_frames=stable_frames, evidence_inliers=evidence_inliers,
        )
        for frame in src.frames():
            frame_no += 1
            # Serial path: the frame counter is the clock. The card is drawn on
            # the same frame it's logged, so last_log_frame == frame_idx and the
            # slide starts from zero (offline behavior unchanged).
            engine.process(frame, lambda: frame_no)

            if show:
                drag.ensure(frame.shape[1], frame.shape[0])
            canvas = draw_overlay(
                frame, st, frame_idx=frame_no, fps=fps, facecam_h_frac=facecam_frac,
                ticker_origin=drag.ticker if show else ticker_origin,
                analytics_origin=drag.analytics if show else analytics_origin,
            )

            if show:
                cv2.imshow(win, canvas)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
                if key == ord("s"):
                    path = save_layout(set_code, drag.ticker, drag.analytics)
                    drag.dirty = False
                    print(f"saved overlay layout: {path}")
                elif key == ord("r"):
                    drag.ticker = drag.analytics = None
                    drag.ensure(frame.shape[1], frame.shape[0])
                    drag.dirty = True
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
        if drag is not None and drag.dirty:
            path = save_layout(set_code, drag.ticker, drag.analytics)
            print(f"saved overlay layout: {path}")
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


# Recognition runs ~3x/sec on this machine (see Phase 3.5 benchmark); the
# boundary detector's time-based hysteresis is converted to ticks at this rate,
# and dwell is a couple of ticks rather than several video frames. Tune on real
# live footage.
LIVE_RECOG_FPS = 3.0


def run_live_threaded(
    source: Union[int, str],
    set_code: str,
    export: Optional[str] = None,
    stable_frames: int = 2,
    min_inliers: int = 25,
    top: int = 5,
    evidence_inliers: int = 15,
    facecam_frac: float = 0.30,
    reset_layout: bool = False,
    recog_fps: float = LIVE_RECOG_FPS,
    max_seconds: Optional[float] = None,
) -> int:
    """Live overlay with recognition on a worker thread and a smooth display loop.

    Capture and recognition run in the background (``ThreadedFrameSource`` +
    ``RecognitionWorker``); the main thread just blits the freshest frame plus a
    snapshot of the overlay state at display rate, so the video stays smooth even
    though one recognition takes ~300 ms. ``max_seconds`` bounds the run for
    tests/headless use.
    """
    # Boundary cadence is the recognition rate, not the camera rate, because the
    # engine ticks the detector once per recognition (not once per frame).
    engine, price_map, set_name = build_engine(
        set_code, boundary_fps=recog_fps, min_inliers=min_inliers,
        stable_frames=stable_frames, evidence_inliers=evidence_inliers, top=top,
    )
    session, st = engine.session, engine.st

    fsrc = FrameSource(source)
    # Live camera: real-time, drop stale frames. File: pace to its fps so the
    # threaded recognizer replays it like a live feed rather than racing through.
    tfs = ThreadedFrameSource(fsrc, pace=None if fsrc.is_device else "source").start()
    fps = fsrc.fps or 30.0
    # Wall-clock tick shared by recognition (last_log_frame) and drawing
    # (frame_idx) so the ticker slide reads a steady ~0.4 s regardless of either
    # thread's rate.
    clock = lambda: int(time.monotonic() * fps)
    # Pass the clock itself (not clock()) so last_log_frame is stamped when the
    # card is logged, after recognition — keeping the slide window intact.
    worker = RecognitionWorker(
        tfs.latest, process=lambda f: engine.process(f, clock), on_result=lambda r: None,
    ).start()

    ticker_origin, analytics_origin = (None, None) if reset_layout else load_layout(set_code)
    drag = LayoutDrag(ticker_origin, analytics_origin, facecam_frac)
    win = "packcapture overlay (live)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, drag.on_mouse)
    print("live overlay (threaded): drag either panel to reposition; 's' saves "
          "the layout, 'r' resets it, 'q'/Esc quits.")

    start = time.monotonic()
    try:
        while not tfs.stopped:
            seq, frame = tfs.latest()
            if frame is None:
                if time.monotonic() - start > 5.0:
                    print("error: no frames from source within 5s.", file=__import__("sys").stderr)
                    break
                time.sleep(0.01)
                continue
            drag.ensure(frame.shape[1], frame.shape[0])
            canvas = draw_overlay(
                frame, engine.snapshot(), frame_idx=clock(), fps=fps,
                facecam_h_frac=facecam_frac,
                ticker_origin=drag.ticker, analytics_origin=drag.analytics,
            )
            cv2.imshow(win, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("s"):
                path = save_layout(set_code, drag.ticker, drag.analytics)
                drag.dirty = False
                print(f"saved overlay layout: {path}")
            elif key == ord("r"):
                drag.ticker = drag.analytics = None
                drag.ensure(frame.shape[1], frame.shape[0])
                drag.dirty = True
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
    finally:
        worker.stop()
        tfs.stop()
        if drag.dirty:
            path = save_layout(set_code, drag.ticker, drag.analytics)
            print(f"saved overlay layout: {path}")
        cv2.destroyAllWindows()

    session.finalize()
    report = _build_report(session, price_map, set_code, set_name, source)
    if export:
        with open(export, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote analytics export: {export}")
    t = report["totals"]
    print(f"live overlay done: {worker.ticks} recognitions, {t['cards']} cards, "
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
