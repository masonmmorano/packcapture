# PackCapture — Project Context

> Persistent context for Claude Code sessions. This is the source of truth for
> what PackCapture is and how it's being built.

## Session log & how to resume

**Repo:** https://github.com/masonmmorano/packcapture (public). Local:
`C:\Users\Mason\Documents\repositories\packcapture`.

**To resume this work:** open a terminal in the repo folder and run
`claude --continue` (picks up the most recent session here) or `claude --resume`
(choose this session from a list). CLAUDE.md loads automatically either way.

### Done so far (as of 2026-06-01)
- **Repo + tooling:** public GitHub repo; Python 3.10 venv; deps (opencv,
  numpy, requests, tqdm, pytest, yt-dlp); ffmpeg installed; gh CLI installed and
  authed (scopes: repo, workflow). Commits use the GitHub noreply email and
  carry no AI attribution (see memory).
- **Phase 1 — `build-set`:** fetch a set from pokemontcg.io, precompute ORB
  descriptors/keypoints, write the bundle. Built `me2` (Phantasmal Flames, 130
  cards) and committed it in-repo for zero-setup recognition.
- **Phase 2 — matcher:** set-locked ORB (Lowe ratio + RANSAC). **Validated on
  real footage** — Murkrow #57 recognized at 51 inliers from a real overhead-cam
  frame (see "Recognition validated on real footage" below).
- **Phase 3 core (started):** `capture/source.py` (FrameSource: webcam/OBS/video
  file) and `pipeline/settle.py` (motion-settle/debounce state machine), with
  tests. `Matcher.match_array()` added for in-memory ROIs.
- **Quality:** 4 pytest tests green; **test CI live** on GitHub Actions (Ubuntu
  + Windows, py3.10/3.12).
- **Docs/brand:** README with badges, squared pokeball logo, "Supported packs"
  section with pack art; MIT LICENSE.

### Done so far (added 2026-06-03)
- **Phase 3 build-out (logic core):** `pipeline/confidence.py` (gate: inliers
  >= 25 AND a margin over the runner-up, noise floor 15), `pipeline/session.py`
  (variant-by-position + per-pack checksum that flags non-reconciling packs,
  per-set slot template), `pipeline/runner.py` (headless frames -> settle ->
  matcher -> gate -> session over any frame iterable). All unit-tested.
- **Auto-ROI breakthrough — `pipeline/roi.py`:** feature-density alone failed
  (cluttered background — posters/sealed stacks are feature-rich too — boxed the
  whole frame = noise). Fix: a fixed-camera rip has a *static* background, so an
  online MOG2 model drops the clutter; we box the densest connected cluster of
  ORB keypoints that fall on *moving* foreground, padded T/B. On the real me2
  clip this auto-recovers a tight box that recognizes Murkrow #57 at ~49 inliers
  with zero manual setup. This is the "point the camera and rip" enabler.
- **Dev mode — `devmode.py` + `packcapture dev <src> --set <code>`:** plays a
  clip or live cam with the auto-ROI box + current match on the left and a
  scrolling detection log + pack tally on the right; `--save` renders the
  side-by-side to a file (headless). Stable-match dedupe logs a card once it
  persists as the accepted top match. Window-based, so not in CI.
- **Real-footage regression test:** `tests/test_real_footage.py` matches an
  in-hand crop of me2 Murkrow #57 (local, git-ignored fixture under
  `tests/assets/`; the crop is a frame from a third-party video, kept out of the
  public repo for provenance) and asserts the gate accepts it as a Common.
  Skips when the asset/bundle is absent so CI stays green. 23 tests total.
- **Brand:** README now leads with `packcapture_banner_gh.png`; dropped the
  pack-art PNG; pokeball got its background removed (transparent) and is reused
  as the Highlights bullet icon.
- **Box smoothing — `BoxSmoother` in `pipeline/roi.py`:** the per-frame auto-ROI
  jittered (each frame recomputed independently). Added a temporal low-pass
  (EMA on x/y/w/h + hold-last-box on miss + deadband); dev mode feeds raw
  detections through it.
- **Tunable gate floor:** `packcapture dev ... --min-inliers N`. Production floor
  stays 25; lower it for low-res footage.
- **Validated the corrected sequence on `diag2.mp4` (480p):** at floor 18 the
  pipeline logged Snubbull #37 → Murkrow #57 → Darumaka #15 → Bronzor #71 →
  Sacred Charm #93 — all confirmed by the user's eye, and they land in template
  slots 1-5 (4 Common + 1 Uncommon), so the per-pack checksum is consistent. Key
  insight: the recognizer was MORE accurate than the gate allowed — the right
  cards sat at 21-23 inliers (clear margin over ~6-9 runner-ups). The 480p was
  just the YouTube *download* resolution, not a real limit; at native res the
  default floor of 25 should hold.
- **PR open:** all of today's work is on branch `phase3-pipeline` →
  https://github.com/masonmmorano/packcapture/pull/1 (25 tests passing).

### Next action when resuming (do this first)
A ~2-min **1080p-ish screen recording** of the same Full Heal video is waiting at
`scratch/footage/IMG_6903.MP4` (git-ignored). It's an iPhone capture: **HEVC,
landscape 2556x1180 after rotation, 60fps** — clean full-frame video, no black
bars. OpenCV won't reliably read HEVC, so transcode to H.264 first, then run dev
mode at the **default gate (no --min-inliers)** to confirm the recognizer clears
25 at real resolution and to get the first multi-pack / checksum-closing run:

```powershell
ffmpeg -i "scratch/footage/IMG_6903.MP4" -c:v libx264 -crf 18 -preset fast -an "scratch/footage/rip_long.mp4"
.\.venv\Scripts\python.exe -m packcapture dev scratch\footage\rip_long.mp4 --set me2 --save scratch\footage\rip_dev.mp4
```

(yt-dlp downloads failed today — bot challenge + Chrome cookie-decryption issues
on Windows; the screen recording was the workaround.)

### Next up (in priority order)
1. **Phase 3 finish:** zone-mode OpenCV confirm-window UI (cv2.selectROI, live
   overlay, hotkeys to confirm/correct) reusing the runner; rip-mode dedupe
   (settle-on-ROI assumes a fixed box, so the moving auto-ROI needs the
   stable-match approach dev mode prototypes).
2. **Session DB + pull-rate stats**, then **CSV/JSON export** (Phases 4-5).
3. **Set-bundling CI** (designed, not built): manual-trigger workflow that builds
   the latest set and publishes the bundle as a GitHub release asset, plus a
   `packcapture fetch-set <code>` command. User wants "latest set only" first.
4. **Coverage badge** once pytest-cov is added to CI.

### Open items / gotchas
- More YouTube footage needs `--cookies-from-browser` (bot challenge) and the
  tool sandbox disabled (CDN blocked); a phone photo of a real card dropped into
  the repo is the fastest clean test input.
- Local-only scratch (git-ignored): `scratch/footage/` has `diag2.mp4` (working
  10s clip) + extracted `frames/`; helper scripts `scratch/extract_frames.py`,
  `match_frames.py`, `match_crop.py`.

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

Next up: build the Phase 3 live pipeline core (zone mode) and validate against a
real pack-opening video.

## Focus set: Phantasmal Flames (`me2`)

First set to support end-to-end. Mega Evolution series, released 2025-11-14.
130 cards (94 numbered + secret rares). Bundle is built locally.
- Rarity distribution: 43 Common, 31 Uncommon, 56 Rare-or-higher
  (Rare 10, Double Rare 10, Illustration Rare 13, Ultra Rare 17,
  Special Illustration Rare 5, Mega Hyper Rare 1).
- Exactly 1 Energy-supertype card — so the *inserted* basic energy in a pack
  will not false-match a set card; the count-to-10 stays robust.

### Recognition validated on real footage (2026-06-01)

Tested against a real opening video ("I opened 216 packs of Phantasmal Flames"
by Full Heal). Findings:
- **It works on real cards.** A 480p overhead-cam frame of a physical Murkrow
  held in-hand matched `me2` Murkrow #57 at 51 inliers (next candidate: 9).
  Snubbull #37 (27) and Darumaka #15 (23) also matched correctly with clear
  margins.
- **Isolation is essential.** Whole-frame matching (hands, desk, sealed packs,
  face-cam all in frame) was noise — 6-15 inliers, wrong card every time. The
  *same* frames center-cropped produced the correct matches above. This is hard
  empirical justification for the ROI / card-detection stage.
- **Confidence threshold:** noise floor ~15 inliers; real hits 25-50+. Starting
  gate: inliers >= ~25 AND a clear margin over the runner-up. Tune with more /
  higher-res footage.
- **Observed real workflow:** overhead cam, cards fanned in-hand (small,
  overlapping, ~100-150px each at 480p) = the hard end-goal "rip mode". Confirms
  zone mode (flat, isolated, full-ROI card -> 600+ inliers) is the right first
  step.

Footage note: yt-dlp can't reach YouTube's media CDN inside the tool sandbox
(metadata works, media stalls at 0 bytes); it succeeds with the sandbox
disabled, but repeated pulls trip YouTube's "confirm you're not a bot" challenge
(needs --cookies-from-browser to continue). For clean accuracy testing, a phone
photo of a real card dropped into the repo is the fastest gold-standard input.

## Phase 3 design (decided)

### Two modes, one core
The recognizer + variant logic + session/stats live behind a swappable front end:

```
[frame source] -> [card-ready detector] -> [recognize + variant] -> [dedupe/count] -> [session] -> [export]
      |                    |
 webcam/OBS/video    ZONE (v1)  ->  CENTER-FRAME RIP (end goal)
```

- **Zone mode (v1):** user drags a detection box once; rips packs to the side and
  throws each card onto a growing stack inside the box (box always frames the top
  card). When the stack gets tall/messy they shove it aside and start a new one.
- **Rip mode (end goal):** detect cards live, center-frame, as the pack is ripped.
  The user should trust it enough to "rip away and not touch it." Same core; only
  the detector box changes. This is the ultimate goal — zone mode is the scaffold
  that proves the core and gives us a tuning harness.
- OBS virtual cam appears to OpenCV as just another webcam, so webcam + OBS are
  one code path (`cv2.VideoCapture`). A **video file path uses the same path**, so
  we can replay YouTube pack-opening clips frame-for-frame for testing/tuning.

### Pack model (the leverage)
Every booster = 10 tracked cards in a fixed structure, plus 1 inserted basic
energy + 1 code card (both worthless / excluded):

| Slot | Contents | Variant label source |
|------|----------|----------------------|
| 1-4  | Commons (circle)        | normal (matched rarity confirms) |
| 5-7  | Uncommons (diamond)     | normal (matched rarity confirms) |
| 8-9  | **Reverse Holos** (any rarity) | **slot position** (only reliable signal) |
| 10   | Rare / Holo / ex / SIR+ (star) | matched rarity confirms (rare+) |

- **Variant by position is primary.** ORB identifies *which* card; the card's own
  rarity (from the bundle) gives base rarity; **position** is the only reliable
  way to mark the 2 reverse-holo slots (a reverse holo can be any base rarity).
  Relies on factory order being preserved (true when flipping straight off the
  top, which is how packs come ordered).
- **Per-pack checksum:** expect exactly 4 base-Common + 3 base-Uncommon + 1 Rare+
  + 2 reverses = 10. If a pack doesn't reconcile, flag it (don't silently log).
  This is what lets a ripper not babysit it — the program knows when it missed one.
- **Foil detection is scoped, not deferred:** shine can only appear in the last 3
  real cards (slots 8-10). Run foil detection only there, as confirmation of the
  position inference; foil firing in slots 1-7 is itself an error signal. Caveat:
  holo shimmer is easy to detect in motion (rip mode) and hard on a flat still
  card (zone mode), so in zone mode position stays primary and foil is best-effort.
- **Counting is lazy-proof:** only set-matching Pokémon cards count toward 10.
  Code card and inserted basic energy don't match `me2` -> tagged and excluded,
  whether or not the user pre-removed them. A missed/low-confidence real card
  shows up as a pack that closes at <10 -> flagged by the checksum.
- The slot template is **configurable per set** (a few sets / promo configs differ).

### Debounce / dedupe
Zone mode reduces dedupe to a motion-settle state machine on the ROI: watch for
motion, and emit exactly one recognition per motion->settle transition (one card
thrown = one settle event = one emit). A card lingering in frame never re-emits
because no new motion->settle cycle occurs. Implemented in `pipeline/settle.py`.

### UI
v1 = a single OpenCV display window (drag-to-select ROI via `cv2.selectROI`, live
feed + text overlay + keyboard hotkeys to confirm/correct/skip). No extra GUI
dependency. The polished UI (set picker, report screen) stays Phase 6.

### Schema additions planned
Store `supertype` in the bundle (helps classify energy), and add `variant`/
`is_holo` columns to the session log.

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
