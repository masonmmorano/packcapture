"""In-stream overlay: serve the price ticker + pack analytics as a web page.

Phase 2 of the live work. Instead of drawing the overlay into a cv2 window
(operator-only, OpenCV's vector font), this runs recognition headless and serves
a **transparent HTML/CSS page** that OBS adds as a **Browser Source** — so the
overlay reaches the stream, with real web typography and CSS animation.

Transport is **Server-Sent Events** (one-way server -> browser), which needs no
third-party dependency and no asyncio: the page opens an ``EventSource`` on
``/events`` and the server streams the current overlay state as JSON whenever it
changes. The same :class:`~packcapture.overlay.OverlayState` drives both this web
view and the cv2 window — one source of truth, two renderers.

No feedback loop: recognition reads the *clean* camera (the OBS Virtual Cam scene
= camera only), while this overlay is a separate Browser Source that lives only
in the Record/Stream scene. See the OBS setup printed by ``serve``.
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Union

from .capture.source import FrameSource
from .capture.threaded import RecognitionWorker, ThreadedFrameSource
from .overlay import (
    HIT_PRICE,
    LIVE_RECOG_FPS,
    OverlayState,
    _money,
    _rarity_color,
    build_engine,
)
from .pipeline.session import RARITY_RARE_PLUS, rarity_class


def _bgr_to_hex(bgr) -> str:
    """OpenCV palette colors are BGR tuples; the web wants ``#rrggbb``."""
    b, g, r = (int(c) for c in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def _demo_state() -> OverlayState:
    """A sample card for confirming the overlay renders/positions in OBS without
    a live feed (the 'Send test card' button)."""
    return OverlayState(
        set_name="Phantasmal Flames", card_name="Mega Lopunny ex", card_number="128",
        price=19.10, variant="holofoil", rarity="Ultra Rare", is_hit=True,
        last_log_frame=0, total=19.10, count=1, packs=0,
        by_status={"complete": 0, "speed_ripped": 0, "no_hit": 0},
    )


def state_to_payload(st: OverlayState) -> dict:
    """Serialize the overlay state for the browser (formatted + colors resolved)."""
    return {
        "set_name": st.set_name,
        "card_name": st.card_name,
        "card_number": st.card_number,
        "price_str": _money(st.price),
        "variant": st.variant,
        "rarity": st.rarity,
        "rarity_color": _bgr_to_hex(_rarity_color(st.rarity)),
        "is_hit": st.is_hit,
        "total_str": _money(st.total),
        "count": st.count,            # increments per logged card -> drives the slide
        "packs": st.packs,
        "by_status": st.by_status or {},
        "last_pack_label": st.last_pack_label,
    }


# The overlay page. Transparent body so OBS keys it out; panels in the corners,
# styled to mirror the cv2 overlay (dark glass, red->orange stripe, tier-colored
# rarity, gold HIT). A new card (count change) restarts the slide-up animation.
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PackCapture overlay</title>
<style>
  :root { --grad: linear-gradient(#eb2d2d, #ff9632); }
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  body {
    font-family: "Segoe UI", "Inter", system-ui, Arial, sans-serif;
    color: #ececec; -webkit-font-smoothing: antialiased;
  }
  .panel {
    position: fixed; cursor: move;
    background: rgba(18,18,18,0.82); border: 1px solid #464646;
    border-radius: 10px; padding: 22px 26px 22px 30px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.45); overflow: hidden;
  }
  .panel.dragging { outline: 2px dashed rgba(255,210,60,0.65); }
  .panel::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0;
    width: 7px; background: var(--grad);
  }
  /* Ticker (top-right, under the facecam) */
  #ticker { top: 150px; right: 40px; width: 520px; opacity: 0; }
  #ticker.show { opacity: 1; }
  #ticker.bump { animation: slideup 0.30s cubic-bezier(.22,1,.36,1); }
  @keyframes slideup {
    from { transform: translateY(55px); opacity: 0; }
    to   { transform: translateY(0);    opacity: 1; }
  }
  #name { font-size: 34px; font-weight: 700; letter-spacing: .2px; }
  #rarity { font-size: 19px; font-weight: 600; margin-top: 2px; }
  #sub { font-size: 17px; color: #9a9a9a; margin-top: 2px; }
  #sub .num { margin-right: 14px; }
  #price { font-size: 52px; font-weight: 800; margin-top: 10px; color: #5adc78; }
  #price .raw { font-size: 18px; font-weight: 600; color: #9a9a9a; margin-left: 10px; }
  #ticker.hit #price { color: #ffd23c; }
  #hit-tag {
    display: none; float: right; margin-top: 6px; padding: 3px 12px;
    background: #ffd23c; color: #1a1a1a; font-weight: 800; font-size: 16px;
    border-radius: 5px; letter-spacing: 1px;
  }
  #ticker.hit #hit-tag { display: inline-block; }
  /* Pack analytics — session value + per-pack stats in ONE draggable panel */
  #analytics { right: 40px; bottom: 40px; width: 460px; }
  #analytics h2 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: .5px; }
  #analytics .setname { color: #9a9a9a; font-size: 15px; margin: 2px 0 14px; letter-spacing: 1px; }
  #analytics .label { color: #9a9a9a; font-size: 15px; letter-spacing: 1px; }
  #value { font-size: 46px; font-weight: 800; color: #5adc78; margin-top: 4px; }
  #counts { display: flex; justify-content: space-between; font-size: 20px; margin: 14px 0 4px;
            border-top: 1px solid #333; padding-top: 12px; }
  #status { display: flex; gap: 26px; font-size: 16px; font-weight: 600; margin: 0 0 16px; }
  #status .complete { color: #5adc78; }
  #status .speed { color: #ffd23c; }
  #status .nohit { color: #9a9a9a; }
  #pack-label { color: #c9c9c9; font-size: 15px; margin-top: 10px; min-height: 18px; }
</style>
</head>
<body>
  <div class="panel" id="ticker">
    <span id="hit-tag">HIT</span>
    <div id="name"></div>
    <div id="rarity"></div>
    <div id="sub"><span class="num"></span><span class="var"></span></div>
    <div id="price"><span class="amt"></span><span class="raw">RAW</span></div>
  </div>

  <div class="panel" id="analytics">
    <h2>PACK ANALYTICS</h2>
    <div class="setname"></div>
    <div id="status">
      <span class="complete">COMPLETE <b id="s-complete">0</b></span>
      <span class="speed">SPEED <b id="s-speed">0</b></span>
      <span class="nohit">NOHIT <b id="s-nohit">0</b></span>
    </div>
    <div class="label">SESSION VALUE</div>
    <div id="value">$0.00</div>
    <div id="counts"><span><b id="packs">0</b> packs</span><span><b id="cards">0</b> cards</span></div>
    <div id="pack-label"></div>
  </div>

<script>
  var ticker = document.getElementById("ticker");
  var lastCount = -1;
  function set(id, v) { document.getElementById(id).textContent = v; }
  function apply(s) {
    // Ticker
    if (s.card_name) {
      ticker.classList.add("show");
      set("name", s.card_name);
      var rar = document.getElementById("rarity");
      rar.textContent = s.rarity || "";
      rar.style.color = s.rarity_color || "#ececec";
      document.querySelector("#sub .num").textContent = s.card_number ? "#" + s.card_number : "";
      document.querySelector("#sub .var").textContent = s.variant || "";
      document.querySelector("#price .amt").textContent = s.price_str;
      ticker.classList.toggle("hit", !!s.is_hit);
      // Restart the slide only when a NEW card is logged.
      if (s.count !== lastCount) {
        lastCount = s.count;
        ticker.classList.remove("bump");
        void ticker.offsetWidth;   // force reflow so the animation replays
        ticker.classList.add("bump");
      }
    }
    // Analytics
    document.querySelector("#analytics .setname").textContent = (s.set_name || "").toUpperCase();
    set("value", s.total_str);
    set("packs", s.packs);
    set("cards", s.count);
    var bs = s.by_status || {};
    set("s-complete", bs.complete || 0);
    set("s-speed", bs.speed_ripped || 0);
    set("s-nohit", bs.no_hit || 0);
    set("pack-label", s.last_pack_label || "");
  }
  // Each panel (ticker / total / per-pack) is independently draggable; the
  // operator positions them once and the spot is remembered per browser source.
  var LS = window.localStorage;
  function makeDraggable(p, key) {
    var saved = null; try { saved = JSON.parse(LS.getItem(key)); } catch (e) {}
    if (saved) {
      p.style.left = saved.x + "px"; p.style.top = saved.y + "px";
      p.style.right = "auto"; p.style.bottom = "auto";
    }
    var grab = null;
    p.addEventListener("mousedown", function (e) {
      var r = p.getBoundingClientRect();
      grab = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      p.style.left = r.left + "px"; p.style.top = r.top + "px";
      p.style.right = "auto"; p.style.bottom = "auto";
      p.classList.add("dragging"); e.preventDefault();
    });
    document.addEventListener("mousemove", function (e) {
      if (!grab) return;
      var x = Math.max(0, Math.min(e.clientX - grab.dx, window.innerWidth - p.offsetWidth));
      var y = Math.max(0, Math.min(e.clientY - grab.dy, window.innerHeight - p.offsetHeight));
      p.style.left = x + "px"; p.style.top = y + "px";
    });
    document.addEventListener("mouseup", function () {
      if (!grab) return;
      grab = null; p.classList.remove("dragging");
      LS.setItem(key, JSON.stringify({ x: parseInt(p.style.left, 10), y: parseInt(p.style.top, 10) }));
    });
  }
  ["ticker", "analytics"].forEach(function (id) {
    makeDraggable(document.getElementById(id), "pc_ov_" + id);
  });

  var es = new EventSource("/events");
  es.onmessage = function (e) { apply(JSON.parse(e.data)); };
</script>
</body>
</html>
"""


def _card_row(c, price_map, pack, status) -> dict:
    price, _ = price_map.get(c.card_id, (None, ""))
    is_hit = (rarity_class(c.base_rarity) == RARITY_RARE_PLUS
              and price is not None and price > HIT_PRICE)
    return {
        "name": c.name, "number": c.number, "rarity": c.base_rarity,
        "rarity_color": _bgr_to_hex(_rarity_color(c.base_rarity)),
        "variant": c.variant, "price": price, "price_str": _money(price),
        "is_hit": is_hit, "pack": pack, "status": status, "card_id": c.card_id,
    }


# Export columns: one row per logged card, numeric price so a spreadsheet can sum
# it. CSV imports straight into Google Sheets / Excel / Numbers.
_CSV_COLUMNS = ["#", "name", "number", "rarity", "variant", "pack", "status", "price", "card_id"]


def session_csv(cards: list) -> str:
    """Serialize the operator card list (from ``operator_state``) to CSV text."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLUMNS)
    for i, c in enumerate(cards, 1):
        w.writerow([
            i, c["name"], c["number"], c["rarity"], c["variant"],
            "" if c["pack"] is None else c["pack"], c["status"],
            "" if c["price"] is None else c["price"], c.get("card_id", ""),
        ])
    return buf.getvalue()


# One row per pack — the summary view for a high-volume session (216+ packs),
# where a card-by-card scroll is unwieldy. Built from the analytics report so it
# carries per-pack status, value and reconciliation. Numeric value sums in Sheets.
_PACKS_CSV_COLUMNS = ["pack", "status", "reconciled", "cards", "raw_value", "issues"]


def session_packs_csv(report: Optional[dict]) -> str:
    """Serialize per-pack totals (from ``_build_report``) to CSV text."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_PACKS_CSV_COLUMNS)
    for p in (report or {}).get("packs", []):
        w.writerow([
            p["index"], p["status"], int(bool(p["reconciled"])),
            p["card_count"], p["raw_value"], "; ".join(p.get("issues") or []),
        ])
    return buf.getvalue()


class RecognitionController:
    """Owns the start/stop lifecycle of a live recognition run for the operator
    GUI. The overlay page and the control page both reflect whatever it's running;
    a file source tears down automatically when it ends."""

    def __init__(self, server: "OverlayServer"):
        self.server = server
        self._lock = threading.Lock()
        self._engine = None
        self._tfs = None
        self._worker = None
        self._price_map: dict = {}
        self._pub_stop = threading.Event()
        self._pub_thread: Optional[threading.Thread] = None
        self.set_code: Optional[str] = None
        self.set_name: Optional[str] = None
        self.source: Optional[str] = None
        self.error: Optional[str] = None
        self.fast: bool = False
        self._ended = False

    @property
    def running(self) -> bool:
        return self._worker is not None

    def start(self, source, set_code, min_inliers: int = 25, stable_frames: int = 1,
              fast: bool = False):
        with self._lock:
            if self._worker is not None:
                return False, "already running"
            self.error = None
            self._ended = False
            try:
                engine, price_map, set_name = build_engine(
                    set_code, boundary_fps=LIVE_RECOG_FPS,
                    min_inliers=min_inliers, stable_frames=stable_frames,
                    fast=fast,
                )
            except Exception as e:  # bad/missing bundle
                self.error = f"set '{set_code}': {e}"
                return False, self.error
            src = int(source) if str(source).isdigit() else source
            try:
                fsrc = FrameSource(src)
                tfs = ThreadedFrameSource(fsrc, pace=None if fsrc.is_device else "source").start()
            except Exception as e:  # camera busy / file missing
                self.error = f"source '{source}': {e}"
                return False, self.error
            fps = fsrc.fps or 30.0
            clock = lambda: int(time.monotonic() * fps)
            worker = RecognitionWorker(
                tfs.latest, process=lambda f: engine.process(f, clock), on_result=lambda r: None,
            ).start()
            self._engine, self._tfs, self._worker = engine, tfs, worker
            self._price_map, self.set_name = price_map, set_name
            self.set_code, self.source, self.fast = set_code, str(source), fast
            self._pub_stop.clear()
            self._pub_thread = threading.Thread(target=self._publish_loop, name="recog-publish", daemon=True)
            self._pub_thread.start()
            return True, "started"

    def _publish_loop(self):
        while not self._pub_stop.is_set():
            eng = self._engine
            if eng is not None:
                self.server.publish(eng.snapshot())
            tfs = self._tfs
            if tfs is not None and tfs.stopped:   # file ran out
                self._teardown(ended=True)
                return
            time.sleep(0.05)

    def stop(self):
        return self._teardown(ended=False)

    def _teardown(self, ended: bool):
        with self._lock:
            if self._worker is None:
                return False, "not running"
            self._pub_stop.set()
            self._worker.stop()
            self._tfs.stop()
            self._engine.session.finalize()   # close any open pack
            self._worker = None
            self._tfs = None
            self._ended = ended
            return True, "stopped"

    def operator_state(self) -> dict:
        with self._lock:
            eng = self._engine
            running = self._worker is not None
            meta = (self.set_code, self.set_name, self.source, self.error, self._ended, self.fast)
        if eng is None:
            cards, packs, by_status = [], 0, {}
        else:
            rows, packs, by_status = eng.read_cards()   # lock-safe snapshot
            cards = [_card_row(c, self._price_map, pk, status) for (pk, status, c) in rows]
        value = sum(c["price"] for c in cards if c["price"] is not None)
        set_code, set_name, source, error, ended, fast = meta
        return {
            "running": running, "ended": ended, "fast": fast,
            "set_code": set_code, "set_name": set_name, "source": source, "error": error,
            "totals": {
                "cards": len(cards), "packs": packs,
                "value_str": _money(round(value, 2)), "by_status": by_status,
            },
            "cards": cards,
        }

    def remove_card(self, index: int) -> bool:
        return self._engine.remove_card(index) if self._engine is not None else False

    def move_card(self, index: int, dest_pack: Optional[int]) -> bool:
        return self._engine.move_card(index, dest_pack) if self._engine is not None else False

    def clear(self) -> bool:
        if self._engine is None:
            return False
        self._engine.clear_session()
        return True

    def build_report(self, source) -> Optional[dict]:
        from .overlay import _build_report
        if self._engine is None:
            return None
        return _build_report(self._engine.session, self._price_map, self.set_code, self.set_name, source)


# Operator control page (separate from the clean viewer overlay): pick a set +
# source, start/stop, watch the live card log + totals. Polls /api/state.
CONTROL_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PackCapture — control</title>
<style>
  body { margin: 0; background: #15161a; color: #e8e8e8;
         font-family: "Segoe UI", system-ui, Arial, sans-serif; }
  header { padding: 16px 24px; background: #1e2026; border-bottom: 1px solid #2c2f37;
           display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  h1 { font-size: 18px; margin: 0; font-weight: 800; letter-spacing: .5px; }
  .dot { width: 11px; height: 11px; border-radius: 50%; background: #6b6b6b; display: inline-block; }
  .dot.on { background: #4ade80; box-shadow: 0 0 8px #4ade80; }
  select, input, button { font: inherit; padding: 7px 10px; border-radius: 6px;
           border: 1px solid #3a3d46; background: #23252c; color: #e8e8e8; }
  button { cursor: pointer; font-weight: 700; }
  button.go { background: #2f6f3f; border-color: #3c8c50; }
  button.stop { background: #6f2f2f; border-color: #8c3c3c; }
  button:disabled { opacity: .45; cursor: default; }
  a.btn { text-decoration: none; color: #e8e8e8; background: #23252c;
          border: 1px solid #3a3d46; border-radius: 6px; padding: 7px 12px; font-weight: 700; }
  a.btn:hover { background: #2b2e36; }
  main { padding: 20px 24px; }
  .totals { display: flex; gap: 28px; font-size: 17px; margin-bottom: 16px; flex-wrap: wrap; }
  .totals b { font-size: 22px; }
  .val { color: #5adc78; }
  table { width: 100%; border-collapse: collapse; font-size: 15px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #262932; }
  th { color: #9a9a9a; font-weight: 600; font-size: 13px; letter-spacing: .5px; }
  td.price { color: #5adc78; text-align: right; }
  tr.hit { background: rgba(255,210,60,0.10); }
  tr.hit td.price { color: #ffd23c; font-weight: 800; }
  tr.hit td.name::after { content: " HIT"; color: #ffd23c; font-weight: 800; font-size: 12px; }
  button.del { padding: 2px 8px; background: #3a2a2a; border-color: #6f3c3c; color: #ff9a9a; font-weight: 700; }
  button.del:hover { background: #5a2f2f; }
  tr[draggable="true"] { cursor: grab; }
  tr.dragging { opacity: .4; }
  tr.droptgt td { background: rgba(74,222,128,0.14); box-shadow: inset 2px 0 0 #4ade80; }
  td.grip { color: #5a5d66; width: 14px; text-align: center; cursor: grab; }
  tr.sep td { background: #1f2128; color: #c9c9c9; font-weight: 700; font-size: 13px;
              letter-spacing: .6px; padding: 6px 10px; border-top: 2px solid #3a3d46; }
  tr.sep .pv { color: #5adc78; margin-left: 10px; }
  tr.sep .st-complete { color: #5adc78; } tr.sep .st-speed { color: #ffd23c; } tr.sep .st-open { color: #8ab4ff; }
  .err { color: #ff7a7a; margin: 8px 0; min-height: 16px; }
  .hint { color: #8a8a8a; font-size: 13px; }
  .status-line { font-size: 15px; letter-spacing: .5px; margin: -4px 0 16px; min-height: 18px; }
  .status-line .complete { color: #5adc78; } .status-line .speed { color: #ffd23c; } .status-line .nohit { color: #9a9a9a; }
  .status-line b { font-weight: 800; }
  a { color: #8ab4ff; }
  label.beta { display: inline-flex; align-items: center; gap: 6px; font-size: 14px; cursor: pointer; }
  label.beta span { font-size: 10px; font-weight: 800; letter-spacing: .5px; color: #1a1a1a;
                    background: #ffd23c; border-radius: 4px; padding: 1px 5px; }
</style>
</head>
<body>
<header>
  <h1>PACKCAPTURE</h1>
  <span><span class="dot" id="dot"></span> <span id="status">idle</span></span>
  <label>Set <select id="set"></select></label>
  <label>Source <input id="source" list="cams" size="12" value="0"
         title="camera index (0) or a video file path"><datalist id="cams"></datalist></label>
  <button id="detect" title="Detect cameras">↻ cameras</button>
  <label class="beta" title="Beta: a faster matcher prefilter. May be noticeably faster to recognize each card; small chance of a missed or wrong card. Off = the proven exhaustive matcher."><input type="checkbox" id="fast"> ⚡ Fast <span>beta</span></label>
  <button class="go" id="start">Start</button>
  <button class="stop" id="stop" disabled>Stop</button>
  <span class="hint">OBS overlay: <a href="/overlay" target="_blank">/overlay</a>
    <button id="demo" title="Push a sample card to the overlay for OBS setup">Test card</button></span>
</header>
<main>
  <div class="err" id="err"></div>
  <div class="totals">
    <span><b id="t-cards">0</b> cards</span>
    <span><b id="t-packs">0</b> packs</span>
    <span>value <b class="val" id="t-value">$0.00</b></span>
    <span style="margin-left:auto">
      <button class="stop" id="clear">Clear all</button>
      <a class="btn" id="csv" href="/api/export.csv" title="One row per card">Cards CSV</a>
      <a class="btn" id="packscsv" href="/api/export_packs.csv" title="One row per pack: status, value, card count">Packs CSV</a>
      <a class="btn" id="jsonbtn" href="/api/export.json">JSON</a>
    </span>
  </div>
  <div class="status-line" id="t-status"></div>
  <div class="hint" style="margin-bottom:14px">Drag a card (⠿) onto another pack to move it; click ✕ to delete a mis-scan. CSV opens directly in Google Sheets (File → Import) / Excel.</div>
  <table>
    <thead><tr><th></th><th>#</th><th>Card</th><th>No.</th><th>Rarity</th><th>Variant</th><th>Price</th><th></th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
  function el(id){ return document.getElementById(id); }
  var LS = window.localStorage;
  if (LS.getItem("pc_source")) el("source").value = LS.getItem("pc_source");
  if (LS.getItem("pc_fast") === "1") el("fast").checked = true;
  fetch("/api/sets").then(r=>r.json()).then(function(sets){
    var s = el("set");
    sets.forEach(function(code){ var o=document.createElement("option"); o.value=o.textContent=code; s.appendChild(o); });
    var saved = LS.getItem("pc_set");
    if (saved && sets.indexOf(saved) >= 0) s.value = saved;
  });
  el("detect").onclick = function(){
    el("detect").textContent = "…"; el("err").textContent = "";
    fetch("/api/cameras").then(r=>r.json()).then(function(cams){
      var dl = el("cams"); dl.innerHTML = "";
      cams.forEach(function(c){ var o=document.createElement("option"); o.value=c.index; o.label=c.label; dl.appendChild(o); });
      el("detect").textContent = "↻ cameras";
      if (!cams.length) el("err").textContent = "No cameras found (start Iriun / OBS Virtual Cam).";
    });
  };
  el("start").onclick = function(){
    el("err").textContent = "";
    LS.setItem("pc_set", el("set").value); LS.setItem("pc_source", el("source").value);
    LS.setItem("pc_fast", el("fast").checked ? "1" : "0");
    fetch("/api/start", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ set: el("set").value, source: el("source").value, fast: el("fast").checked }) })
      .then(r=>r.json()).then(function(res){ if(!res.ok) el("err").textContent = res.message; });
  };
  el("stop").onclick = function(){ fetch("/api/stop", { method:"POST" }); };
  el("demo").onclick = function(){ fetch("/api/demo", { method:"POST" }); };
  el("clear").onclick = function(){ if (confirm("Clear all logged cards?")) fetch("/api/clear", { method:"POST" }); };
  el("rows").onclick = function(e){
    if (e.target.classList.contains("del")) {
      var idx = parseInt(e.target.getAttribute("data-i"), 10);
      fetch("/api/delete", { method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ index: idx }) }).then(poll);
    }
  };
  // Drag a card row onto another pack (or the Current-pack header) to move it.
  // Polling is paused while dragging so the table isn't rebuilt mid-drag.
  var rowsEl = el("rows"), dragging = false, dragIdx = null;
  function clearDrop(){ var a = rowsEl.querySelectorAll(".droptgt"); for (var i=0;i<a.length;i++) a[i].classList.remove("droptgt"); }
  rowsEl.addEventListener("dragstart", function(e){
    var tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    dragIdx = parseInt(tr.getAttribute("data-i"), 10);
    dragging = true; tr.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragIdx));   // Firefox needs data set
  });
  rowsEl.addEventListener("dragend", function(){
    dragging = false; dragIdx = null; clearDrop();
    var d = rowsEl.querySelector(".dragging"); if (d) d.classList.remove("dragging");
  });
  rowsEl.addEventListener("dragover", function(e){
    var tr = e.target.closest("tr[data-pack]");
    if (!tr || dragIdx === null) return;
    e.preventDefault(); e.dataTransfer.dropEffect = "move";
    clearDrop(); tr.classList.add("droptgt");
  });
  rowsEl.addEventListener("drop", function(e){
    var tr = e.target.closest("tr[data-pack]");
    if (!tr || dragIdx === null) return;
    e.preventDefault();
    var dest = tr.getAttribute("data-pack");   // "open" or a 1-based pack number
    var idx = dragIdx;
    dragging = false; dragIdx = null; clearDrop();
    fetch("/api/move", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ index: idx, dest_pack: dest }) }).then(poll);
  });
  function statusCounts(bs){
    return "<span class='complete'>COMPLETE <b>"+(bs.complete||0)+"</b></span>&nbsp;&nbsp;&nbsp;"+
           "<span class='speed'>SPEED <b>"+(bs.speed_ripped||0)+"</b></span>&nbsp;&nbsp;&nbsp;"+
           "<span class='nohit'>NOHIT <b>"+(bs.no_hit||0)+"</b></span>";
  }
  function poll(){
    if (dragging) return;                 // don't rebuild the table mid-drag
    fetch("/api/state").then(r=>r.json()).then(function(s){
      var on = s.running;
      el("dot").className = "dot" + (on ? " on" : "");
      el("status").textContent = on ? ("running — " + (s.set_name||s.set_code||"") + " @ " + (s.source||"")
                                       + (s.fast ? "  ⚡ fast (beta)" : ""))
                                     : (s.ended ? "finished" : "idle");
      el("start").disabled = on; el("stop").disabled = !on; el("fast").disabled = on;
      if (s.error) el("err").textContent = s.error;
      var t = s.totals || {};
      el("t-cards").textContent = t.cards||0;
      el("t-packs").textContent = t.packs||0;
      el("t-value").textContent = t.value_str||"$0.00";
      el("t-status").innerHTML = statusCounts(t.by_status||{});
      // Newest first, with a divider row between packs (instead of a pack column).
      var cards = s.cards || [];
      var html = "", lastKey;
      for (var k = cards.length - 1; k >= 0; k--) {
        var c = cards[k];
        var key = (c.pack == null) ? "open" : c.pack;
        if (key !== lastKey) {
          lastKey = key;
          var label, cls;
          if (c.pack == null) { label = "Current pack"; cls = "st-open"; }
          else { label = "Pack " + c.pack; cls = (c.status === "complete") ? "st-complete"
                       : (c.status === "speed_ripped") ? "st-speed" : ""; }
          var stx = (c.status && c.status !== "open") ? " · <span class='"+cls+"'>"+c.status.toUpperCase().replace("_"," ")+"</span>" : "";
          html += "<tr class='sep' data-pack='"+key+"'><td colspan='8'>"+label+stx+"</td></tr>";
        }
        var rc = c.rarity_color || "#e8e8e8";
        html += "<tr class='"+(c.is_hit?"hit":"")+"' draggable='true' data-i='"+k+"' data-pack='"+key+"'>"+
                "<td class='grip' title='Drag onto another pack'>⠿</td>"+
                "<td>"+(k+1)+"</td><td class='name'>"+c.name+
                "</td><td>"+(c.number||"")+"</td><td style='color:"+rc+"'>"+(c.rarity||"")+
                "</td><td>"+(c.variant||"")+"</td><td class='price'>"+c.price_str+"</td>"+
                "<td><button class='del' data-i='"+k+"' title='Delete this card'>✕</button></td></tr>";
      }
      el("rows").innerHTML = html;
    }).catch(function(){});
  }
  setInterval(poll, 600); poll();
</script>
</body>
</html>
"""


class OverlayServer:
    """Serves the overlay page and streams overlay-state updates over SSE."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8770):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._payload: Optional[dict] = None
        self._seq = 0
        self._stopped = threading.Event()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.controller: Optional["RecognitionController"] = None

    # --- state the SSE handler reads ---
    def publish(self, state: OverlayState) -> None:
        """Push a new state; only bumps the sequence if it actually changed."""
        payload = state_to_payload(state)
        with self._lock:
            if payload != self._payload:
                self._payload = payload
                self._seq += 1

    def _latest(self):
        with self._lock:
            return self._seq, self._payload

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}/overlay"

    def start(self) -> "OverlayServer":
        server = self
        page = PAGE.encode("utf-8")

        control_page = CONTROL_PAGE.encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence per-request stderr spam
                pass

            def _html(self, body: bytes):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                ctl = server.controller
                if ctl is None:
                    self._json({"ok": False, "message": "no controller"}, 400)
                    return
                if self.path == "/api/start":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        data = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        data = {}
                    ok, msg = ctl.start(data.get("source", 0), data.get("set", ""),
                                        min_inliers=int(data.get("min_inliers", 25)),
                                        fast=bool(data.get("fast", False)))
                    self._json({"ok": ok, "message": msg})
                elif self.path == "/api/stop":
                    ok, msg = ctl.stop()
                    self._json({"ok": ok, "message": msg})
                elif self.path == "/api/demo":
                    # Push a sample card to the overlay for OBS setup (no-op once a
                    # real session starts publishing over it).
                    server.publish(_demo_state())
                    self._json({"ok": True, "message": "demo card sent"})
                elif self.path == "/api/delete":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        data = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        data = {}
                    ok = ctl.remove_card(int(data.get("index", -1)))
                    self._json({"ok": ok})
                elif self.path == "/api/move":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        data = json.loads(self.rfile.read(length) or b"{}")
                    except ValueError:
                        data = {}
                    dest = data.get("dest_pack", None)
                    dest = None if dest in (None, "", "open") else int(dest)
                    ok = ctl.move_card(int(data.get("index", -1)), dest)
                    self._json({"ok": ok})
                elif self.path == "/api/clear":
                    self._json({"ok": ctl.clear()})
                else:
                    self.send_error(404)

            def _download(self, body: bytes, content_type: str, filename: str):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/control":
                    self._html(control_page)
                elif self.path == "/api/state":
                    ctl = server.controller
                    self._json(ctl.operator_state() if ctl else {"running": False})
                elif self.path.startswith("/api/export.csv"):
                    ctl = server.controller
                    cards = ctl.operator_state()["cards"] if ctl else []
                    code = (ctl.set_code if ctl and ctl.set_code else "session")
                    fn = f"packcapture_{code}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
                    self._download(session_csv(cards).encode("utf-8"), "text/csv; charset=utf-8", fn)
                elif self.path.startswith("/api/export_packs.csv"):
                    ctl = server.controller
                    report = ctl.build_report(ctl.source) if ctl else None
                    code = (ctl.set_code if ctl and ctl.set_code else "session")
                    fn = f"packcapture_{code}_packs_{time.strftime('%Y%m%d_%H%M%S')}.csv"
                    self._download(session_packs_csv(report).encode("utf-8"), "text/csv; charset=utf-8", fn)
                elif self.path.startswith("/api/export.json"):
                    ctl = server.controller
                    report = ctl.build_report(ctl.source) if ctl else None
                    code = (ctl.set_code if ctl and ctl.set_code else "session")
                    fn = f"packcapture_{code}_{time.strftime('%Y%m%d_%H%M%S')}.json"
                    body = json.dumps(report or {}, indent=2).encode("utf-8")
                    self._download(body, "application/json", fn)
                elif self.path == "/api/sets":
                    from .config import data_dir
                    d = data_dir()
                    sets = (sorted(p.name for p in d.iterdir() if (p / "manifest.json").exists())
                            if d.exists() else [])
                    self._json(sets)
                elif self.path == "/api/cameras":
                    from .capture.devices import enumerate_cameras
                    self._json([{"index": c.index, "label": f"{c.index}  ({c.width}x{c.height})"}
                                for c in enumerate_cameras()])
                elif self.path in ("/", "/overlay"):
                    self._html(page)
                elif self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    last = -1
                    try:
                        while not server.stopped:
                            seq, payload = server._latest()
                            if seq != last and payload is not None:
                                last = seq
                                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                                self.wfile.flush()
                            else:
                                time.sleep(0.03)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_error(404)

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._port = self._httpd.server_address[1]  # resolve if port 0 was used
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="overlay-http", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopped.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def _make_server(host: str, port: int):
    """A started server with a controller wired in (shared by serve + gui)."""
    server = OverlayServer(host=host, port=port)
    server.controller = RecognitionController(server)
    server.start()
    return server


def gui(set_code: Optional[str] = None, host: str = "127.0.0.1", port: int = 8770) -> int:
    """Serve the operator control page (+ the overlay) and idle until Ctrl-C.

    Recognition is started/stopped from the browser, not at launch — the operator
    picks the source + set on the control page. The overlay stays a clean,
    separate page for OBS.
    """
    server = _make_server(host, port)
    base = f"http://{host}:{port}"
    print(f"PackCapture control panel:  {base}/control")
    print(f"  clean overlay for OBS:    {base}/overlay")
    print("Open the control page, pick a set + source, and press Start. Ctrl-C to quit.")
    try:
        while not server.stopped:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server.controller is not None:
            server.controller.stop()
        server.stop()
    return 0


def serve(
    source: Union[int, str],
    set_code: str,
    host: str = "127.0.0.1",
    port: int = 8770,
    min_inliers: int = 25,
    stable_frames: int = 1,
    export: Optional[str] = None,
    max_seconds: Optional[float] = None,
    fast: bool = False,
) -> int:
    """Auto-start recognition on a fixed source and serve the overlay for OBS.

    The headless quick path; the operator GUI lives in :func:`gui`.
    """
    server = _make_server(host, port)
    ctl = server.controller
    ok, msg = ctl.start(source, set_code, min_inliers=min_inliers,
                        stable_frames=stable_frames, fast=fast)
    if not ok:
        print(f"error: {msg}", file=__import__("sys").stderr)
        server.stop()
        return 1

    print(f"overlay server running — add an OBS Browser Source at:\n    {server.url}")
    print("  size it to your canvas (e.g. 1920x1080), background transparent.")
    print("  OBS scene routing: recognize from the CLEAN cam (Virtual Cam scene =")
    print("  camera only); put this Browser Source only in your Record/Stream scene.")
    print(f"  operator control page:  http://{host}:{port}/control")
    print("Ctrl-C to stop.")

    start = time.monotonic()
    try:
        while ctl.running:
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.stop()

    if export:
        report = ctl.build_report(source)
        if report is not None:
            with open(export, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"wrote analytics export: {export}")
    st = ctl.operator_state()["totals"]
    server.stop()
    print(f"served {st['cards']} cards, {st['packs']} pack(s), value {st['value_str']}.")
    return 0
