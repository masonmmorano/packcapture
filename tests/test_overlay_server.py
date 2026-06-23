"""The browser-overlay server: state serialization, page serving, SSE push."""
from __future__ import annotations

import json
import urllib.request

from packcapture.overlay import OverlayState
from packcapture.overlay_server import (
    OverlayServer,
    _bgr_to_hex,
    state_to_payload,
)


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
