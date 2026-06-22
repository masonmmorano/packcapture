"""On-disk set bundle: load and save.

Layout (per the project brief), under <data_dir>/<code>/:
  metadata.db    SQLite: cards table (idx aligns to .npy rows) + meta table
  embeddings.npy object array; row i = ORB descriptors (M_i x 32 uint8) for card i
  keypoints.npy  object array; row i = keypoints (M_i x 7 float32) for card i
  thumbnails/    small reference JPEGs for confirmation UI (not used for matching)
  manifest.json  set version, card count, build params, build date

embeddings/keypoints use allow_pickle (object arrays of ragged matrices). Bundles
are produced locally or shipped as trusted GitHub release assets.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import set_dir


@dataclass
class Bundle:
    code: str
    rows: list[dict[str, Any]]      # aligned to descriptors/keypoints by index
    descriptors: list[np.ndarray]   # row i = (M_i, 32) uint8
    keypoints: list[np.ndarray]     # row i = (M_i, 7) float32
    manifest: dict[str, Any]

    @property
    def orb_nfeatures(self) -> int:
        return int(self.manifest.get("orb_nfeatures", 1000))


def bundle_paths(code: str) -> dict[str, Path]:
    d = set_dir(code)
    return {
        "dir": d,
        "metadata": d / "metadata.db",
        "embeddings": d / "embeddings.npy",
        "keypoints": d / "keypoints.npy",
        "manifest": d / "manifest.json",
        "thumbnails": d / "thumbnails",
    }


def _load_rows(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Price columns are added by `fetch-prices` after the fact; older bundles
        # may not have them, so select them only when present.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
        base = ["idx", "card_id", "number", "name", "rarity", "set_code", "image_url"]
        extra = [c for c in ("price", "price_variant", "price_updated") if c in cols]
        cur = conn.execute(
            f"SELECT {', '.join(base + extra)} FROM cards ORDER BY idx"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def load_bundle(code: str) -> Bundle:
    paths = bundle_paths(code)
    if not paths["manifest"].exists():
        raise FileNotFoundError(
            f"No bundle for set '{code}' at {paths['dir']}. "
            f"Build it with: packcapture build-set {code}"
        )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    descriptors = list(np.load(paths["embeddings"], allow_pickle=True))
    keypoints = list(np.load(paths["keypoints"], allow_pickle=True))
    rows = _load_rows(paths["metadata"])
    return Bundle(
        code=code.lower(),
        rows=rows,
        descriptors=descriptors,
        keypoints=keypoints,
        manifest=manifest,
    )


def save_bundle(
    code: str,
    rows: list[dict[str, Any]],
    descriptors: list[np.ndarray],
    keypoints: list[np.ndarray],
    manifest: dict[str, Any],
) -> dict[str, Path]:
    paths = bundle_paths(code)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    # metadata.db (rebuilt from scratch each time)
    if paths["metadata"].exists():
        paths["metadata"].unlink()
    conn = sqlite3.connect(str(paths["metadata"]))
    try:
        conn.execute(
            "CREATE TABLE cards ("
            "idx INTEGER PRIMARY KEY, card_id TEXT UNIQUE, number TEXT, "
            "name TEXT, rarity TEXT, set_code TEXT, image_url TEXT)"
        )
        conn.executemany(
            "INSERT INTO cards (idx, card_id, number, name, rarity, set_code, image_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    i,
                    r["card_id"],
                    r["number"],
                    r["name"],
                    r["rarity"],
                    r["set_code"],
                    r["image_url"],
                )
                for i, r in enumerate(rows)
            ],
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in manifest.items()],
        )
        conn.commit()
    finally:
        conn.close()

    # Ragged object arrays of per-card descriptors / keypoints.
    np.save(paths["embeddings"], np.array(descriptors, dtype=object), allow_pickle=True)
    np.save(paths["keypoints"], np.array(keypoints, dtype=object), allow_pickle=True)

    paths["manifest"].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return paths
