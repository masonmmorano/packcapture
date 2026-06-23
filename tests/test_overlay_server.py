"""The browser-overlay server: state serialization, page serving, SSE push."""
from __future__ import annotations

import json
import urllib.request

from packcapture.overlay import OverlayState
from packcapture.overlay_server import (
    OverlayServer,
    RecognitionController,
    _bgr_to_hex,
    session_csv,
    state_to_payload,
)


def _control_server():
    server = OverlayServer(port=0)
    server.controller = RecognitionController(server)
    return server.start()


def _post(server, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{server._port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def _get(server, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{server._port}{path}", timeout=3) as r:
        return r.read().decode()


def test_bgr_to_hex():
    assert _bgr_to_hex((0, 0, 255)) == "#ff0000"      # pure red (BGR -> RGB)
    assert _bgr_to_hex((255, 0, 0)) == "#0000ff"      # pure blue
    assert _bgr_to_hex((236, 236, 236)) == "#ececec"


def test_state_to_payload_formats_and_colors():
    st = OverlayState(set_name="Phantasmal Flames", card_name="Mega Lopunny ex",
                      card_number="128", price=19.1, variant="holofoil", rarity="Ultra Rare",
                      is_hit=True, total=23.62, count=8, packs=3,
                      by_status={"complete": 1, "speed_ripped": 2, "no_hit": 0})
    p = state_to_payload(st)
    assert p["card_name"] == "Mega Lopunny ex"
    assert p["price_str"] == "$19.10"
    assert p["total_str"] == "$23.62"
    assert p["count"] == 8 and p["packs"] == 3
    assert p["is_hit"] is True
    assert p["rarity_color"].startswith("#") and len(p["rarity_color"]) == 7
    assert p["by_status"]["speed_ripped"] == 2


def test_state_to_payload_missing_price():
    p = state_to_payload(OverlayState(set_name="Set"))
    assert p["price_str"] == "—"
    assert p["total_str"] == "$0.00"


def test_server_serves_overlay_page():
    server = OverlayServer(port=0).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server._port}/overlay", timeout=2) as r:
            assert r.status == 200
            body = r.read().decode()
        assert "PACK ANALYTICS" in body
        assert "EventSource(\"/events\")" in body
    finally:
        server.stop()


def test_publish_dedupes_and_bumps_seq():
    server = OverlayServer(port=0)
    st = OverlayState(set_name="Set", card_name="Oddish", count=1)
    server.publish(st)
    seq1, _ = server._latest()
    server.publish(st)                       # identical -> no bump
    seq2, _ = server._latest()
    server.publish(OverlayState(set_name="Set", card_name="Aipom", count=2))
    seq3, _ = server._latest()
    assert seq1 == 1 and seq2 == 1 and seq3 == 2


def test_sse_stream_emits_current_payload():
    server = OverlayServer(port=0).start()
    try:
        server.publish(OverlayState(set_name="Set", card_name="Snubbull",
                                    card_number="37", price=0.2, count=1))
        with urllib.request.urlopen(f"http://127.0.0.1:{server._port}/events", timeout=3) as r:
            # Read the first SSE event line.
            line = b""
            for _ in range(50):
                chunk = r.readline()
                line += chunk
                if chunk.startswith(b"data:"):
                    break
        payload = json.loads(line.split(b"data:", 1)[1].strip())
        assert payload["card_name"] == "Snubbull"
        assert payload["price_str"] == "$0.20"
    finally:
        server.stop()


# --- operator control page + controller ---

def test_control_page_served():
    server = _control_server()
    try:
        assert "PACKCAPTURE" in _get(server, "/control")
        sets = json.loads(_get(server, "/api/sets"))
        assert isinstance(sets, list)
        state = json.loads(_get(server, "/api/state"))
        assert state["running"] is False
    finally:
        server.stop()


def test_controller_rejects_bad_set():
    ctl = RecognitionController(OverlayServer(port=0))
    ok, msg = ctl.start("0", "no_such_set_zzz")   # fails on the bundle, never touches a camera
    assert ok is False
    assert "no_such_set_zzz" in msg
    assert ctl.running is False


def test_api_start_bad_set_and_stop_when_idle():
    server = _control_server()
    try:
        res = _post(server, "/api/start", {"source": "0", "set": "no_such_set_zzz"})
        assert res["ok"] is False and "no_such_set_zzz" in res["message"]
        assert _post(server, "/api/stop")["ok"] is False   # nothing running
    finally:
        server.stop()


def test_operator_state_idle_shape():
    ctl = RecognitionController(OverlayServer(port=0))
    s = ctl.operator_state()
    assert s["running"] is False
    assert s["cards"] == []
    assert s["totals"]["cards"] == 0


def test_card_row_has_rarity_color_and_hit_flag():
    from types import SimpleNamespace
    from packcapture.overlay_server import _card_row
    rare = SimpleNamespace(card_id="me2-1", name="Mega Lopunny ex", number="128",
                           base_rarity="Ultra Rare", variant="holofoil")
    common = SimpleNamespace(card_id="me2-2", name="Oddish", number="2",
                             base_rarity="Common", variant="normal")
    pm = {"me2-1": (19.1, "holofoil"), "me2-2": (0.10, "normal")}
    hit = _card_row(rare, pm, 1, "complete")
    base = _card_row(common, pm, 1, "complete")
    assert hit["is_hit"] is True            # rare+ AND price > $1.50
    assert base["is_hit"] is False
    assert hit["rarity_color"].startswith("#") and len(hit["rarity_color"]) == 7


def test_session_csv_header_rows_and_missing_price():
    cards = [
        {"name": "Prinplup", "number": "1", "rarity": "Common", "variant": "normal",
         "pack": 1, "status": "complete", "price": 0.17, "card_id": "me2-1"},
        {"name": "Sableye", "number": "2", "rarity": "Rare", "variant": "reverse holo",
         "pack": None, "status": "open", "price": None, "card_id": "me2-2"},
    ]
    lines = session_csv(cards).strip().splitlines()
    assert lines[0].startswith("#,name,number")
    assert "Prinplup" in lines[1] and "0.17" in lines[1]   # numeric price, not "$0.17"
    assert "Sableye" in lines[2]                            # missing price -> empty, no crash
    assert len(lines) == 3


def test_export_csv_endpoint_serves_downloadable_csv():
    server = _control_server()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server._port}/api/export.csv", timeout=3) as r:
            ctype = r.headers.get("Content-Type")
            disp = r.headers.get("Content-Disposition")
            body = r.read().decode()
        assert "text/csv" in ctype
        assert "attachment" in disp and ".csv" in disp
        assert body.splitlines()[0].startswith("#,name")   # header even when idle/empty
    finally:
        server.stop()
