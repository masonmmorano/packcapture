<p align="center">
  <img src="assets/packcapture_banner_gh.png" alt="PackCapture" width="100%">
</p>

<p align="center">
  <b>Computer-vision card logger for Pokémon TCG pack openings.</b><br>
  Point a camera at your pulls — PackCapture detects, recognizes, and logs every
  card automatically, so high-volume rippers get pull-rate analytics and clean
  data export instead of manual spreadsheet entry.
</p>

<p align="center">
  <a href="https://github.com/masonmmorano/packcapture/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/masonmmorano/packcapture/tests.yml?branch=main&style=flat-square&label=tests"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue?style=flat-square"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Built with OpenCV" src="https://img.shields.io/badge/CV-OpenCV-5C3EE8?style=flat-square&logo=opencv&logoColor=white">
  <a href="https://pokemontcg.io"><img alt="Data: Pokémon TCG API" src="https://img.shields.io/badge/data-Pok%C3%A9mon%20TCG%20API-EF5350?style=flat-square"></a>
  <img alt="Status: early development" src="https://img.shields.io/badge/status-early%20development-orange?style=flat-square">
</p>

> **Status:** active development. Phase 1 (offline set bundles), Phase 2 (static
> image recognition), and much of Phase 3 (live video pipeline) are implemented —
> auto-ROI, segmented pack sessions, visual pack-boundary detection, a rip-mode
> price overlay, and analytics export. Persistent session DB and a polished UI
> are next — see [CLAUDE.md](CLAUDE.md) for the full plan and build order.

## Highlights

<img src="assets/pokeball.png" width="16" align="absmiddle">&nbsp; **Offline & set-locked** — recognition searches one set's ~100–400 cards, no network at match time.<br>
<img src="assets/pokeball.png" width="16" align="absmiddle">&nbsp; **Point and rip** — an auto-ROI finds the held cards on a static-camera recording, so there's no zone to set up.<br>
<img src="assets/pokeball.png" width="16" align="absmiddle">&nbsp; **Live price overlay** — `packcapture overlay` draws a raw-price ticker for each card you fan (like the stream overlays) **plus** a pack-analytics panel, and exports per-card / per-pack data as JSON.<br>
<img src="assets/pokeball.png" width="16" align="absmiddle">&nbsp; **Pack-aware** — packs are segmented by a visual boundary detector and labelled (`COMPLETE` / `SPEED_RIPPED` / `NO_HIT`); a per-pack checksum flags any full pack that doesn't reconcile, so a missed card is caught, not silently logged.<br>
<img src="assets/pokeball.png" width="16" align="absmiddle">&nbsp; **Dev mode** — `packcapture dev` replays a clip or live feed with the ROI box and a live detection log side by side, for tuning.

## Supported packs

**Phantasmal Flames** (`me2`) — the first fully supported set. Its recognition
bundle ships in the repo (with raw prices baked in), so recognition and the
price overlay work out of the box with no API key and no build step. More sets
will be delivered as downloadable bundles.

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

# Refresh raw (TCGPlayer) prices for a built set. Prices change daily and are
# stored on the bundle, separate from the one-time recognition build.
packcapture fetch-prices me2

# Rip mode: a price ticker + pack-analytics overlay on a clip (or live webcam/OBS
# index). --save renders to a video file; --export writes a per-card/per-pack JSON.
packcapture overlay path\to\opening.mp4 --set me2 --save out.mp4 --export out.json
packcapture overlay 0 --set me2      # live camera (device index 0)

# Dev mode: replay a clip (or a live webcam/OBS index) with the auto-ROI box and
# a live detection log side by side. --save renders it to a video file instead.
packcapture dev path\to\opening.mp4 --set me2
packcapture dev 0 --set me2          # live camera (device index 0)
```

Set `POKEMONTCG_API_KEY` to raise pokemontcg.io rate limits (optional). Built
bundles live under `sets/<code>/` and are git-ignored — they'll ship as GitHub
release assets so most users never hit the API.

## Bundle layout

```
sets/<code>/
  metadata.db      SQLite: card id, number, name, rarity, set_code, raw price (idx aligns to .npy rows)
  embeddings.npy   per-card ORB descriptors
  keypoints.npy    per-card ORB keypoints (for RANSAC verification)
  thumbnails/      small reference JPEGs (confirmation UI only)
  manifest.json    card count, build params, build date
```

## Development

```powershell
pip install -e ".[dev]"
pytest          # network-free suite (synthetic build/match, pipeline, overlay, sessions)
```

## License

MIT
