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


def _bgr_to_hex(bgr) -> str:
    """OpenCV palette colors are BGR tuples; the web wants ``#rrggbb``."""
    b, g, r = (int(c) for c in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


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
    position: fixed; right: 40px; width: 520px;
    background: rgba(18,18,18,0.82); border: 1px solid #464646;
    border-radius: 10px; padding: 22px 26px 22px 30px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.45); overflow: hidden;
  }
  .panel::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0;
    width: 7px; background: var(--grad);
  }
  /* Ticker (top-right, under the facecam) */
  #ticker { top: 150px; opacity: 0; }
  #ticker.show { opacity: 1; }
  #ticker.bump { animation: slideup 0.4s cubic-bezier(.22,1,.36,1); }
  @keyframes slideup {
    from { transform: translateY(90px); opacity: 0; }
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
  /* Pack analytics (bottom-right) */
  #analytics { bottom: 40px; }
  #analytics h2 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: .5px; }
  #analytics .setname { color: #9a9a9a; font-size: 15px; margin: 2px 0 14px; letter-spacing: 1px; }
  #analytics .label { color: #9a9a9a; font-size: 15px; letter-spacing: 1px; }
  #value { font-size: 46px; font-weight: 800; color: #5adc78; }
  #counts { display: flex; justify-content: space-between; font-size: 20px; margin: 14px 0 4px;
            border-top: 1px solid #333; padding-top: 12px; }
  #status { display: flex; gap: 22px; font-size: 17px; font-weight: 600; }
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
    <div class="label">SESSION VALUE</div>
    <div id="value">$0.00</div>
    <div id="counts"><span><b id="packs">0</b> packs</span><span><b id="cards">0</b> cards</span></div>
    <div id="status">
      <span class="complete">COMPLETE <b id="s-complete">0</b></span>
      <span class="speed">SPEED <b id="s-speed">0</b></span>
      <span class="nohit">NOHIT <b id="s-nohit">0</b></span>
    </div>
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
  var es = new EventSource("/events");
  es.onmessage = function (e) { apply(JSON.parse(e.data)); };
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

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence per-request stderr spam
                pass

            def do_GET(self):
                if self.path in ("/", "/overlay"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
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


def serve(
    source: Union[int, str],
    set_code: str,
    host: str = "127.0.0.1",
    port: int = 8770,
    min_inliers: int = 25,
    stable_frames: int = 2,
    recog_fps: float = LIVE_RECOG_FPS,
    export: Optional[str] = None,
    max_seconds: Optional[float] = None,
) -> int:
    """Run recognition headless and serve the overlay for an OBS Browser Source."""
    engine, price_map, set_name = build_engine(
        set_code, boundary_fps=recog_fps, min_inliers=min_inliers, stable_frames=stable_frames,
    )
    server = OverlayServer(host=host, port=port).start()

    fsrc = FrameSource(source)
    # A live camera streams in real time (drop stale frames); a file would race
    # through, so pace it to its own fps to replay like a live feed.
    tfs = ThreadedFrameSource(fsrc, pace=None if fsrc.is_device else "source").start()
    fps = fsrc.fps or 30.0
    clock = lambda: int(time.monotonic() * fps)
    worker = RecognitionWorker(
        tfs.latest, process=lambda f: engine.process(f, clock), on_result=lambda r: None,
    ).start()

    print(f"overlay server running — add an OBS Browser Source at:\n    {server.url}")
    print("  size it to your canvas (e.g. 1920x1080), background transparent.")
    print("  OBS scene routing: recognize from the CLEAN cam (Virtual Cam scene =")
    print("  camera only); put this Browser Source only in your Record/Stream scene.")
    print("Ctrl-C to stop.")

    start = time.monotonic()
    try:
        while not tfs.stopped:
            server.publish(engine.snapshot())
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
            time.sleep(0.05)   # ~20 Hz publish; SSE only emits on real changes
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        tfs.stop()
        server.stop()

    engine.session.finalize()
    if export:
        from .overlay import _build_report
        report = _build_report(engine.session, price_map, set_code, set_name, source)
        with open(export, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"wrote analytics export: {export}")
    stats = engine.session.stats()
    print(f"served {worker.ticks} recognitions, {stats['cards_logged']} cards, "
          f"{stats['packs']} pack(s).")
    return 0
