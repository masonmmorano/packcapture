"""Card price data: fetch raw (ungraded) market prices and store them in a bundle.

Prices change daily, so they are decoupled from the heavy ORB rebuild: ``build-set``
captures the recognition features once, and ``fetch-prices`` refreshes the
price columns on the existing bundle's ``metadata.db`` whenever needed.

"Raw price" mirrors what a single-number overlay (e.g. a stream price ticker)
shows: the ungraded TCGPlayer market value. We pick one representative number
per card — preferring the non-foil printing, falling back to foil variants, and
within a variant preferring ``market`` over ``mid``/``low``.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Optional

from ..api.pokemontcg import PokemonTCGClient
from ..storage.bundle import bundle_paths

# Variant printings in order of preference for the "raw" single price.
_VARIANT_ORDER = ("normal", "holofoil", "reverseHolofoil", "1stEditionNormal", "1stEditionHolofoil")
# Within a chosen variant, the price field to use, in order of preference.
_FIELD_ORDER = ("market", "mid", "low", "directLow")


def select_raw_price(tcgplayer: Optional[dict[str, Any]]) -> tuple[Optional[float], Optional[str]]:
    """Pick a single representative raw price and its variant from a tcgplayer block.

    Returns ``(price, variant)``; ``(None, None)`` when no usable price exists.
    """
    prices = (tcgplayer or {}).get("prices") or {}
    if not prices:
        return None, None
    # Honour the preferred variant order, then fall back to any remaining variant.
    ordered = [v for v in _VARIANT_ORDER if v in prices]
    ordered += [v for v in prices if v not in ordered]
    for variant in ordered:
        block = prices.get(variant) or {}
        for field in _FIELD_ORDER:
            val = block.get(field)
            if isinstance(val, (int, float)) and val > 0:
                return float(val), variant
    return None, None


def fetch_prices(
    code: str, client: Optional[PokemonTCGClient] = None
) -> dict[str, tuple[Optional[float], Optional[str]]]:
    """Fetch raw prices for every card in a set, keyed by card id.

    Only the lightweight price metadata is requested (no images), so this works
    even where the media CDN is blocked.
    """
    client = client or PokemonTCGClient()
    cards = client.get_cards(code)
    out: dict[str, tuple[Optional[float], Optional[str]]] = {}
    for card in cards:
        price, variant = select_raw_price(card.get("tcgplayer"))
        out[card["id"]] = (price, variant)
    return out


def _ensure_price_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
    for col, decl in (("price", "REAL"), ("price_variant", "TEXT"), ("price_updated", "TEXT")):
        if col not in existing:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col} {decl}")


def update_bundle_prices(
    code: str, client: Optional[PokemonTCGClient] = None
) -> dict[str, int]:
    """Refresh the price columns on an existing bundle's metadata.db.

    Returns a small summary: cards seen, priced, and missing a price.
    """
    paths = bundle_paths(code)
    if not paths["metadata"].exists():
        raise FileNotFoundError(
            f"No bundle for set '{code}' at {paths['dir']}. "
            f"Build it first with: packcapture build-set {code}"
        )

    prices = fetch_prices(code, client=client)
    updated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    conn = sqlite3.connect(str(paths["metadata"]))
    try:
        _ensure_price_columns(conn)
        priced = 0
        for card_id, (price, variant) in prices.items():
            conn.execute(
                "UPDATE cards SET price = ?, price_variant = ?, price_updated = ? "
                "WHERE card_id = ?",
                (price, variant, updated_at if price is not None else None, card_id),
            )
            if price is not None:
                priced += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    finally:
        conn.close()

    return {"cards": total, "priced": priced, "missing": total - priced}
