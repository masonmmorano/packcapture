"""Central configuration: paths, API endpoints, and recognition parameters."""
from __future__ import annotations

import os
from pathlib import Path

# Repo root: <root>/src/packcapture/config.py -> parents[2] == <root>
_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Directory holding built set bundles (one subdir per set code).

    Override with the PACKCAPTURE_DATA_DIR env var; defaults to <repo>/sets.
    """
    override = os.environ.get("PACKCAPTURE_DATA_DIR")
    return Path(override) if override else _REPO_ROOT / "sets"


def set_dir(code: str) -> Path:
    return data_dir() / code.lower()


# --- Pokémon TCG API (pokemontcg.io) ---
API_BASE = "https://api.pokemontcg.io/v2"
API_KEY_ENV = "POKEMONTCG_API_KEY"  # optional; raises rate limits when set

# --- Recognition / ORB ---
# Number of ORB keypoints to extract per image.
ORB_NFEATURES = 1000
# Images are resized to this height (px) before feature extraction so that
# reference cards and live frames are compared at a consistent scale.
WORK_HEIGHT = 600

# Bumped whenever the on-disk bundle format changes.
SCHEMA_VERSION = 1
