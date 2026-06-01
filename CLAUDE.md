# PackCapture — Project Context

> Persistent context for Claude Code sessions. This is the source of truth for
> what PackCapture is and how it's being built.

## What it is

An open-source desktop tool for Pokémon TCG pack rippers to automatically detect
and log cards via computer vision, replacing manual spreadsheet entry. Target
user: content creators or collectors opening high volumes of packs who want pull
rate analytics and data export.

## Core UX flow

1. User selects active set + enters pack count for the session
2. Camera feed (webcam or OBS virtual cam) runs in background
3. Cards are detected, recognized, and logged automatically as packs are opened
4. Session ends → pull rate report generated + CSV export

## Recognition pipeline

1. **Frame capture** — OpenCV pulls frames from webcam/OBS virtual cam
2. **Card detection** — isolate card rectangle from frame (contour detection or lightweight YOLO)
3. **Stabilize/dedupe** — only attempt recognition on a reasonably still card; debounce to avoid logging the same card multiple times
4. **Recognize** — ORB keypoint matching as primary (fast, works well on flat printed cards); OCR of collector number + set code as verifier/fallback
5. **Confirm + log** — write to local SQLite session DB
6. **Export** — CSV/JSON at session end; third-party sync (Collectr etc.) as future stretch goal pending their API

## Local set index (offline-first)

- User runs `packcapture build-set <code>` once per set to fetch and precompute
- Data sourced from Pokémon TCG API (pokemontcg.io) — free, has card images, collector numbers, rarity
- Bundle stored at `sets/<code>/`:
  - `metadata.db` — SQLite: id, number, name, rarity, set_code
  - `embeddings.npy` — precomputed ORB descriptors / embedding vectors aligned to metadata rows
  - `keypoints.npy` — ORB keypoints aligned to the same rows (used for RANSAC verification)
  - `thumbnails/` — small reference images for confirmation UI only, not for matching
  - `manifest.json` — set version, card count, build date
- Prebuilt bundles shipped as GitHub release assets so most users never hit the API
- Set-locked matching: NN search over ~100–400 candidates, not 20,000+

## Tech stack

- **Language:** Python (3.10+)
- **Vision:** OpenCV, ORB keypoint matching (fallback: CLIP/DINOv2 embeddings if foil/glare breaks ORB)
- **OCR:** PaddleOCR or Tesseract (collector number verifier) — not yet implemented
- **DB:** SQLite (local session store + set metadata)
- **Data:** Pokémon TCG API → local `.npy` + SQLite bundle
- **Export:** CSV/JSON

## Build order

1. **`build-set <code>`** — fetch set from API, precompute ORB descriptors, save bundle ✅
2. **Static image matcher** — prove accuracy on a handful of real card photos ✅
3. **Live video pipeline** — card detection + debounce on top of working recognizer ⬜
4. **Session DB + pull rate stats** ⬜
5. **CSV/JSON export** ⬜
6. **UI layer** (set selector, live feed, session summary) ⬜

## Current status (Phase 1 + 2 done)

Implemented:
- `packcapture build-set <code>` — fetches a set from pokemontcg.io, computes ORB
  features per card, writes the bundle under `sets/<code>/`.
- `packcapture match <image> --set <code>` — set-locked ORB matcher with Lowe
  ratio test + RANSAC homography inlier scoring.
- `packcapture list-sets` — lists locally built bundles.
- Network-free synthetic end-to-end test in `tests/test_pipeline.py`.

Next up: validate accuracy on real card photos (Phase 2 acceptance), then start
the live video pipeline (Phase 3).

## Repo layout

```
src/packcapture/
  cli.py                 argparse entry point (build-set / match / list-sets)
  config.py              paths, API endpoints, ORB params
  api/pokemontcg.py      pokemontcg.io v2 client (paginated, retrying)
  setbuild/builder.py    build-set: fetch + precompute + save
  recognize/
    features.py          ORB extraction + keypoint (de)serialization
    orb_matcher.py       set-locked matcher (ratio test + RANSAC)
  storage/bundle.py      load/save the on-disk bundle
tests/test_pipeline.py   synthetic build+match test (no network)
```

## Dev setup (Windows)

```powershell
py -3.10 -m venv .venv          # 3.7 is also installed on this machine — do NOT use it
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## Conventions & decisions

- **Python 3.10+ only.** OpenCV/numpy won't install on the machine's old 3.7.
- **Set-locked by design.** Never match against the full card universe; always
  scope to one built set's candidates.
- **Bundles are git-ignored** (`sets/`, `*.npy`, `*.db`). Ship them as release
  assets, not in the repo.
- `embeddings.npy`/`keypoints.npy` are ragged object arrays saved with
  `allow_pickle=True` — only load bundles you built or that came from trusted
  releases.
- ORB descriptors come from grayscale images resized to `WORK_HEIGHT` (config)
  so reference cards and live frames compare at a consistent scale.

## Stretch / future

- CLIP/DINOv2 embedding fallback when foils/glare defeat ORB
- OCR verifier (collector number + set code) to disambiguate near-identical arts
- Third-party sync (Collectr, etc.) pending their API
