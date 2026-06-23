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


def _card_row(c, price_map, pack, status) -> dict:
    price, _ = price_map.get(c.card_id, (None, ""))
    return {
        "name": c.name, "number": c.number, "rarity": c.base_rarity,
        "variant": c.variant, "price": price, "price_str": _money(price),
        "pack": pack, "status": status,
    }


def _operator_cards(engine, price_map) -> list:
    """Every logged card so far, closed packs first then the open segment."""
    rows = []
    for p in engine.session.packs:
        for c in p.cards:
            rows.append(_card_row(c, price_map, p.index, p.status))
    for c in getattr(engine.session, "_current", []):
        rows.append(_card_row(c, price_map, None, "open"))
    return rows


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
        self._ended = False

    @property
    def running(self) -> bool:
        return self._worker is not None

    def start(self, source, set_code, min_inliers: int = 25, stable_frames: int = 2):
        with self._lock:
            if self._worker is not None:
                return False, "already running"
            self.error = None
            self._ended = False
            try:
                engine, price_map, set_name = build_engine(
                    set_code, boundary_fps=LIVE_RECOG_FPS,
                    min_inliers=min_inliers, stable_frames=stable_frames,
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
            self.set_code, self.source = set_code, str(source)
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
            cards = _operator_cards(eng, self._price_map) if eng else []
            value = sum(c["price"] for c in cards if c["price"] is not None)
            return {
                "running": running,
                "ended": self._ended,
                "set_code": self.set_code,
                "set_name": self.set_name,
                "source": self.source,
                "error": self.error,
                "totals": {
                    "cards": len(cards),
                    "packs": len(eng.session.packs) if eng else 0,
                    "value_str": _money(round(value, 2)),
                    "by_status": eng.session.stats()["by_status"] if eng else {},
                },
                "cards": cards,
            }

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
  main { padding: 20px 24px; }
  .totals { display: flex; gap: 28px; font-size: 17px; margin-bottom: 16px; flex-wrap: wrap; }
  .totals b { font-size: 22px; }
  .val { color: #5adc78; }
  table { width: 100%; border-collapse: collapse; font-size: 15px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #262932; }
  th { color: #9a9a9a; font-weight: 600; font-size: 13px; letter-spacing: .5px; }
  td.price { color: #5adc78; text-align: right; }
  .err { color: #ff7a7a; margin: 8px 0; min-height: 16px; }
  .hint { color: #8a8a8a; font-size: 13px; }
  a { color: #8ab4ff; }
</style>
</head>
<body>
<header>
  <h1>PACKCAPTURE</h1>
  <span><span class="dot" id="dot"></span> <span id="status">idle</span></span>
  <label>Set <select id="set"></select></label>
  <label>Source <input id="source" size="10" value="0" title="camera index (0) or a video file path"></label>
  <button class="go" id="start">Start</button>
  <button class="stop" id="stop" disabled>Stop</button>
  <span class="hint">overlay for OBS: <a href="/overlay" target="_blank">/overlay</a></span>
</header>
<main>
  <div class="err" id="err"></div>
  <div class="totals">
    <span><b id="t-cards">0</b> cards</span>
    <span><b id="t-packs">0</b> packs</span>
    <span>value <b class="val" id="t-value">$0.00</b></span>
    <span class="hint" id="t-status"></span>
  </div>
  <table>
    <thead><tr><th>#</th><th>Card</th><th>No.</th><th>Rarity</th><th>Variant</th><th>Pack</th><th>Price</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
  function el(id){ return document.getElementById(id); }
  fetch("/api/sets").then(r=>r.json()).then(function(sets){
    var s = el("set");
    sets.forEach(function(code){ var o=document.createElement("option"); o.value=o.textContent=code; s.appendChild(o); });
  });
  el("start").onclick = function(){
    el("err").textContent = "";
    fetch("/api/start", { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ set: el("set").value, source: el("source").value }) })
      .then(r=>r.json()).then(function(res){ if(!res.ok) el("err").textContent = res.message; });
  };
  el("stop").onclick = function(){ fetch("/api/stop", { method:"POST" }); };
  function rarityCount(bs){ return ["COMPLETE "+(bs.complete||0), "SPEED "+(bs.speed_ripped||0), "NOHIT "+(bs.no_hit||0)].join("   "); }
  function poll(){
    fetch("/api/state").then(r=>r.json()).then(function(s){
      var on = s.running;
      el("dot").className = "dot" + (on ? " on" : "");
      el("status").textContent = on ? ("running — " + (s.set_name||s.set_code||"") + " @ " + (s.source||""))
                                     : (s.ended ? "finished" : "idle");
      el("start").disabled = on; el("stop").disabled = !on;
      if (s.error) el("err").textContent = s.error;
      var t = s.totals || {};
      el("t-cards").textContent = t.cards||0;
      el("t-packs").textContent = t.packs||0;
      el("t-value").textContent = t.value_str||"$0.00";
      el("t-status").textContent = rarityCount(t.by_status||{});
      var rows = (s.cards||[]).map(function(c, i){
        return "<tr><td>"+(i+1)+"</td><td>"+c.name+"</td><td>"+(c.number||"")+"</td><td>"+(c.rarity||"")+
               "</td><td>"+(c.variant||"")+"</td><td>"+(c.pack==null?"open":c.pack)+
               "</td><td class='price'>"+c.price_str+"</td></tr>";
      }).reverse().join("");
      el("rows").innerHTML = rows;
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
                                        min_inliers=int(data.get("min_inliers", 25)))
                    self._json({"ok": ok, "message": msg})
                elif self.path == "/api/stop":
                    ok, msg = ctl.stop()
                    self._json({"ok": ok, "message": msg})
                else:
                    self.send_error(404)

            def do_GET(self):
                if self.path == "/control":
                    self._html(control_page)
                elif self.path == "/api/state":
                    ctl = server.controller
                    self._json(ctl.operator_state() if ctl else {"running": False})
                elif self.path == "/api/sets":
                    from .config import data_dir
                    d = data_dir()
                    sets = (sorted(p.name for p in d.iterdir() if (p / "manifest.json").exists())
                            if d.exists() else [])
                    self._json(sets)
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
    stable_frames: int = 2,
    export: Optional[str] = None,
    max_seconds: Optional[float] = None,
) -> int:
    """Auto-start recognition on a fixed source and serve the overlay for OBS.

    The headless quick path; the operator GUI lives in :func:`gui`.
    """
    server = _make_server(host, port)
    ctl = server.controller
    ok, msg = ctl.start(source, set_code, min_inliers=min_inliers, stable_frames=stable_frames)
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
