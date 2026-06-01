# PackCapture

Open-source desktop tool for Pokémon TCG pack rippers: point a camera at cards
as you open packs and PackCapture detects, recognizes, and logs each card
automatically — replacing manual spreadsheet entry. Built for content creators
and collectors opening high volumes who want pull-rate analytics and clean data
export.

> **Status:** early. Phase 1 (offline set bundles) and Phase 2 (static image
> recognition) are implemented. Live video, session tracking, export, and UI
> are next — see [CLAUDE.md](CLAUDE.md) for the full plan and build order.

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
