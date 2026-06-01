<h1 align="center">
  <img src="assets/pokeball.png" alt="" height="36">&nbsp; PackCapture
</h1>

<p align="center">
  <b>Computer-vision card logger for Pokémon TCG pack openings.</b><br>
  Point a camera at your pulls — PackCapture detects, recognizes, and logs every
  card automatically, so high-volume rippers get pull-rate analytics and clean
  data export instead of manual spreadsheet entry.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue?style=flat-square"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Built with OpenCV" src="https://img.shields.io/badge/CV-OpenCV-5C3EE8?style=flat-square&logo=opencv&logoColor=white">
  <a href="https://pokemontcg.io"><img alt="Data: Pokémon TCG API" src="https://img.shields.io/badge/data-Pok%C3%A9mon%20TCG%20API-EF5350?style=flat-square"></a>
  <img alt="Status: early development" src="https://img.shields.io/badge/status-early%20development-orange?style=flat-square">
</p>

> **Status:** early. Phase 1 (offline set bundles) and Phase 2 (static image
> recognition) are implemented. Live video, session tracking, export, and UI
> are next — see [CLAUDE.md](CLAUDE.md) for the full plan and build order.

## Supported packs

<img src="assets/phantasmalflames.png" alt="Phantasmal Flames" width="130" align="left" hspace="16" vspace="4">

**Phantasmal Flames** (`me2`) — the first fully supported set. Its recognition
bundle ships in the repo, so recognition works out of the box with no API key
and no build step. More sets will be delivered as downloadable bundles.

<br clear="left">


## How recognition works

PackCapture is **offline-first and set-locked**. You build a bundle for a set
once; recognition then searches only that set's ~100–400 cards instead of the
full ~20,000-card universe.

1. `build-set <code>` fetches a set from [pokemontcg.io](https://pokemontcg.io)
   and precomputes ORB keypoint descriptors for every card.
2. At match time, ORB descriptors from the query image are compared against the
   bundle (Lowe ratio test), and the top candidates are verified with a RANSAC
   homography. The card with the most geometric inliers wins.

## Install (Windows, Python 3.10+)

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## Usage

```powershell
# Build the bundle for a set (one-time per set). Set ids come from pokemontcg.io,
# e.g. base1, swsh1, sv1, sv3pt5.
packcapture build-set sv3pt5

# List the sets you've built locally.
packcapture list-sets

# Recognize a card photo against a built set.
packcapture match path\to\card.jpg --set sv3pt5 --top 5
```

Set `POKEMONTCG_API_KEY` to raise pokemontcg.io rate limits (optional). Built
bundles live under `sets/<code>/` and are git-ignored — they'll ship as GitHub
release assets so most users never hit the API.

## Bundle layout

```
sets/<code>/
  metadata.db      SQLite: card id, number, name, rarity, set_code (idx aligns to .npy rows)
  embeddings.npy   per-card ORB descriptors
  keypoints.npy    per-card ORB keypoints (for RANSAC verification)
  thumbnails/      small reference JPEGs (confirmation UI only)
  manifest.json    card count, build params, build date
```

## Development

```powershell
pip install -e ".[dev]"
pytest          # runs a network-free synthetic end-to-end test
```

## License

MIT
