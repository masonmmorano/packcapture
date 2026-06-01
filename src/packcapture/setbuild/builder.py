"""`build-set` implementation: fetch a set, precompute ORB features, save a bundle."""
from __future__ import annotations

import datetime as dt
from typing import Any

import cv2
import numpy as np
from tqdm import tqdm

from .. import config
from ..api.pokemontcg import PokemonTCGClient
from ..recognize.features import create_orb, detect, prep
from ..storage.bundle import bundle_paths, save_bundle

EMPTY_DESC = np.zeros((0, 32), np.uint8)
EMPTY_KP = np.zeros((0, 7), np.float32)


def _decode_color(content: bytes):
    arr = np.frombuffer(content, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _make_thumb(color: np.ndarray, width: int = 245) -> np.ndarray:
    h, w = color.shape[:2]
    scale = width / w
    return cv2.resize(color, (width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def build_set(
    code: str,
    image_size: str = "large",
    force: bool = False,
    save_thumbnails: bool = True,
    client: PokemonTCGClient | None = None,
) -> tuple[dict[str, Any], dict]:
    code = code.lower()
    paths = bundle_paths(code)
    if paths["manifest"].exists() and not force:
        raise FileExistsError(
            f"Bundle for '{code}' already exists at {paths['dir']}. Use --force to rebuild."
        )

    client = client or PokemonTCGClient()
    set_meta = client.get_set(code)
    cards = client.get_cards(code)
    if not cards:
        raise RuntimeError(
            f"No cards returned for set '{code}'. Check the set id "
            f"(e.g. 'base1', 'swsh1', 'sv1') at https://pokemontcg.io."
        )

    orb = create_orb(config.ORB_NFEATURES)
    if save_thumbnails:
        paths["thumbnails"].mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    descriptors: list[np.ndarray] = []
    keypoints: list[np.ndarray] = []

    for card in tqdm(cards, desc=f"Building {code}", unit="card"):
        images = card.get("images") or {}
        url = images.get(image_size) or images.get("large") or images.get("small")
        row = {
            "card_id": card["id"],
            "number": card.get("number", "") or "",
            "name": card.get("name", "") or "",
            "rarity": card.get("rarity", "") or "",
            "set_code": code,
            "image_url": url or "",
        }
        rows.append(row)

        color = None
        if url:
            try:
                color = _decode_color(client.download(url))
            except Exception:
                color = None

        if color is None:
            descriptors.append(EMPTY_DESC)
            keypoints.append(EMPTY_KP)
            continue

        gray = prep(cv2.cvtColor(color, cv2.COLOR_BGR2GRAY))
        kp, desc = detect(orb, gray)
        descriptors.append(desc)
        keypoints.append(kp)

        if save_thumbnails:
            cv2.imwrite(str(paths["thumbnails"] / f"{card['id']}.jpg"), _make_thumb(color))

    manifest = {
        "set_code": code,
        "set_name": (set_meta or {}).get("name", ""),
        "card_count": len(rows),
        "feature_count": int(sum(len(d) for d in descriptors)),
        "cards_without_features": int(sum(1 for d in descriptors if len(d) == 0)),
        "orb_nfeatures": config.ORB_NFEATURES,
        "work_height": config.WORK_HEIGHT,
        "image_size": image_size,
        "schema_version": config.SCHEMA_VERSION,
        "build_date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    save_bundle(code, rows, descriptors, keypoints, manifest)
    return manifest, paths
