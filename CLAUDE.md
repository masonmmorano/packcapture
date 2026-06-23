# PackCapture — Project Context

> Persistent context for Claude Code sessions. This is the source of truth for
> what PackCapture is and how it's being built.

## Session log & how to resume

> **⚠️ START HERE — first real tripod footage has LANDED and validated
> (2026-06-23).** The long-standing blocker is cleared: `IMG_7032.MOV` (3 real
> me2 packs, fixed tripod, native res) was transcoded to
> `scratch/footage/IMG_7032_fixed.mp4` and run through the overlay pipeline —
> **all 3 packs closed `COMPLETE`** with correct reverse-holo slot labels (this
> drove the energy-exclusion fix; see the 2026-06-23 block below).
>
> Remaining physical to-do (still off-keyboard, lower priority now): record the
> other two ripping styles for label coverage — **speed-rip straight to the hit**
> (`SPEED_RIPPED`) and **quick fan / hitless** (`NO_HIT`). The `COMPLETE` path is
> now validated on real footage; these two would validate the other status
> labels. (Also in memory: `physical-todo-real-pack-footage`.)

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

### Done so far (added 2026-06-05)
- **Native-resolution validation.** Transcoded the iPhone screen recording
  `scratch/footage/IMG_6903.MP4` (HEVC → H.264). It's a screen-record of the
  YouTube "Optimal Ripping" montage *with pillarbox black bars* — `cropdetect`
  gave `crop=2096:1180:230:0`, baked into the transcode → `rip_long.mp4` (2096x1180,
  60fps). At native res the recognizer **easily clears the default 25 gate**
  (inliers 25–106), confirming 480p was only the YouTube *download* limit, not an
  accuracy floor. But the clip is a fast montage, not a clean factory-order pack,
  so the count-to-10 checksum correctly can't reconcile it.
- **Whole-card auto-ROI fix (committed `642f0c6`).** The densest-connected-cluster
  box only covered a card's textured core → median **13%** of frame height, erratic
  aspect (0.41–1.98). Replaced with a robust **5–95 percentile bbox of the moving
  keypoints, grown to card aspect 0.72** (expand the deficient dim, never crop;
  bias to over-frame since ORB tolerates context but cropping kills it). `roi.py`
  `density_bbox` → `card_bbox`. Median box height **0.13 → ~0.40** of frame;
  recognitions **doubled (9 → 18)** on the validation clip; user confirmed the
  boxes now frame whole cards. (Scratch diagnostics `box_stats.py`/`box_stats2.py`
  measured this.)
- **Pack model pivot — see memory `pack-model-gap-segmented`.** Moved away from
  strict "every pack = 10, checksum to 10" (assumed a disciplined ripper) toward
  **segmented packs with status labels**, because volume rippers fan past or jump
  to the hit. One adaptive pipeline, no upfront mode:
  - State machine: `WAITING_FOR_PACK` (idle / opening next pack) ↔
    `DETECTING_PACK` (actively recognizing). A boundary closes a pack + ticks the
    counter.
  - **Boundary must be VISUAL, not a fixed time gap** — cadence between packs is
    not constant, so a fixed `gap_frames` is too fragile. Need a visual cue
    (card-present→absent transition, the tear/open-wrapper motion burst, hands
    leaving frame, robust "empty frame" state). **Open design problem.**
  - Status labels: `COMPLETE` (10 seen + checksum reconciles), `SPEED_RIPPED`
    (<10 but hit / ≥1 card logged — *not* an error), `NO_HIT` (cards seen, no
    rare+). 0 cards → not counted ("track ≥1 card" rule). The old checksum +
    variant-by-position code is **retained** — it now earns the `COMPLETE` label.
  - Common recall deprioritized — optimize for not missing **hits**. Dwell time is
    a free discriminator (the hit is held ~1s, commons fan by fast), already
    handled by the stable-match dedupe.

### Done so far (added 2026-06-10)
- **PR #1 merged to main.** New branch `phase3-segmented` for this work.
- **Inter-pack gap measured (user eyeballed `rip_long.mp4`):** grab 0:55 → tear
  1:00 → fan ~1:00–1:12 → set down + grab next → tear ~1:18. **Cards-absent gap
  between packs ≈ 4–6s**; within-pack pauses well under 2s. The ~3–5s
  wrapper-in-hand phase before each tear is a distinct visual state.
- **Segmented `session.py` rewrite (committed `80b21d1`):** packs close only on
  an explicit boundary (`close_pack()`/`finalize()`), never by counting to 10.
  Status labels: `COMPLETE` (exactly 10 + checksum reconciles), `SPEED_RIPPED`
  (rare+ logged, not an error), `NO_HIT`. Empty segments aren't counted;
  variants downgrade to `unknown` unless the pack closed as a full factory-order
  flip; >10-card segments flag a likely missed boundary. Checksum +
  variant-by-position retained as the `COMPLETE` earner.
- **Visual pack-boundary detector (committed `7508ba1`):**
  `pipeline/boundary.py` — WAITING_FOR_PACK ↔ DETECTING_PACK from two per-frame
  signals: card-presence evidence (matcher top candidate ≥ noise floor ~15
  inliers, softer than the logging gate) and MOG2 foreground fraction
  (`MotionFeatureROI.last_motion`). Cut = card-present→absent with 2.5s
  hysteresis + motion-burst accelerator (absent ≥1s AND motion ≥0.25). Entry
  debounced (3 evidence frames in a 12-frame window).
- **Validated against ground truth on `rip_long.mp4`** (scratch/boundary_probe.py):
  detector found PACK_END 0:55.3 (user: grabs pack 0:55), PACK_START 1:02.8
  (tear 1:00 + card becomes recognizable), PACK_END 1:12.8 (done ~1:12–1:14),
  PACK_START 1:18.5 (tear ~1:18). **All four boundaries within ~2s of the
  user's eyeballed timeline**, on hostile montage footage with jumpcuts.
  Evidence trace: 27–30/30 frames during packs, hard 0 in gaps.
- **Dev mode shows the boundary state** (user request): `DETECTING`/`WAITING`
  tag on the video + state and motion level on the panel; packs close live on
  PACK_END with their status label in the log. 34 tests green.
- **End-to-end render confirmed by the user** on the 0:40–1:35 window
  (`rip_window_dev_h264.mp4`): "captures the important cards he shows," states
  flip correctly, and **the pack count came out right** (4 segments, all
  `SPEED_RIPPED` — correct for montage footage; jumpcuts account for the extra
  segments, per the user's heads-up). 8 cards logged incl. Mega Lopunny i128.
- **`--save` renders now auto re-encode to H.264** (`devmode._to_h264`):
  OpenCV can only write mp4v on Windows, which stock players won't open; the
  finished render is handed to ffmpeg in place (graceful note if ffmpeg absent).
- **Footage cleanup (~700 MB freed):** deleted IMG_6903.MP4 (source iPhone
  recording — rip_long.mp4 is the keeper transcode), old dev renders, extracted
  frame dirs, stale diagnostics. Kept: `rip_long.mp4` (ground-truth clip),
  `rip_window_dev_h264.mp4` (validated render), `diag2.mp4`, the two state PNGs.
  The probe window can be re-cut anytime: `ffmpeg -ss 40 -to 95 -i rip_long.mp4
  -c copy rip_window.mp4`.
- **Threshold question answered for the user:** slow ripping (30s+ gaps) is
  inherently fine (the cut is visual absence, not gap length). Known failure
  modes, both visible: packs merge if the frame is never card-free ~1s between
  packs (>10 cards flags it; merged speed-rips undercount silently — the one
  sneaky case), and a >2.5s mid-pack walk-away splits a pack. Tripod footage
  tunes the two constants (2.5s absence, 1s+burst 0.25).

### Done so far (added 2026-06-22)
- **Competitor reframe — price overlay ("rip mode" front end).** User flagged a
  monetized competitor, *hypeoverlay*: fan each card full-in-frame, it pops the
  raw price as you scan. We replicate it AND keep our analytics edge. Screenshot
  saved at `scratch/hypeoverlay.png` (their layout: facecam top-left, price block
  below it). Our test footage has the **facecam top-right**, so our block sits
  top-right under the facecam.
- **Price layer (`setbuild/prices.py` + `packcapture fetch-prices <code>`).**
  Decoupled from the heavy ORB rebuild: prices change daily, so `fetch-prices`
  pulls TCGPlayer prices (JSON only, no media — works where the CDN is blocked)
  and writes `price`/`price_variant`/`price_updated` columns onto the existing
  bundle's `metadata.db` (ALTER-if-missing; loader selects them only when
  present, so old bundles still load). "Raw price" = market, preferring the
  non-foil printing (`normal → holofoil → reverseHolofoil`), field
  `market → mid → low`. **me2 priced: 130/130.** The committed me2 bundle now
  ships with prices baked in (zero-setup).
- **Overlay render (`overlay.py` + `packcapture overlay <src> --set --save
  --export`).** Same recognition core as dev mode (ROI → matcher → gate →
  BoundaryDetector → Session), but draws on the clean footage itself, in **two
  separated pieces** (user's call):
  - **Price ticker** (top-right, under facecam): current card + raw price only,
    with a **slide-up + fade-in** per card (`TICKER_ANIM_S=0.40s`, ease-out
    cubic). Rare+ gets a gold **HIT** tag + gold price. The "dumb price read."
  - **Pack analytics** (fixed, bottom-right): session value, pack/card counts,
    COMPLETE/SPEED/NOHIT breakdown, last pack label. Our edge over the competitor.
  - `--export <json>` writes per-card/per-pack analytics (price, rarity, variant,
    inliers, pack status/value, session totals). `--save` re-encodes to H.264.
- **Shared `mediautil.to_h264`** — pulled the H.264 re-encode out of devmode so
  overlay and devmode share it (devmode behavior unchanged).
- **Validated on `rip_window.mp4`** (re-cut 0:40–1:35 of rip_long): 8 cards, 4
  packs (all SPEED_RIPPED — correct for jumpcut montage), session raw value
  **$23.62**; Mega Lopunny ex hit at 121 inliers → $19.10. Render +
  `scratch/footage/rip_window_overlay.{mp4,json}`. User confirmed visuals
  (ticker slide-up + gold hit tag + fixed analytics panel) on extracted frames.
- **ORB same-name disambiguation confirmed:** me2 has Ambipom #79 (Rare) and
  #107 (Illustration Rare); the recognizer correctly matched the IR by art and
  priced it foil ($2.40). Validation win, not a bug.
- **Tests:** `tests/test_overlay.py` (price-selection preference order, export
  report totals/pack values, missing-price handling, draw smoke). **40 green.**
- **Still pending (unchanged):** real tripod footage; variant-specific pricing
  (currently one representative price/card — switch to per-slot reverse-holo
  pricing once packs close as COMPLETE on real footage).

### Done so far (added 2026-06-23)
- **First real tripod footage validated — the `COMPLETE` path works on real
  packs.** `IMG_7032.MOV` (3 me2 packs, fixed tripod, native res, HEVC →
  `IMG_7032_fixed.mp4`, 1920x1080 @ ~60fps, no pillarbox) run through
  `packcapture overlay`: **30 cards / 3 packs, all `COMPLETE`**, checksum
  reconciles, reverse-holo slots labeled by position. Total raw value **$8.08**,
  avg pack **$2.69** (no chase hits in these three — all base-rarity packs).
- **Energy exclusion + `supertype` bundle column (commit `b99fa46`).** The real
  footage surfaced a bug: the inserted basic energy false-matched me2's own
  **Ignition Energy #124** at the gate floor, logging a phantom 11th card →
  demoted `COMPLETE` packs to `SPEED_RIPPED` and shifted the reverse-holo slots.
  Fix: store card `supertype` in the bundle and **exclude energy-supertype
  matches from logging** across runner/overlay/devmode.
  - `build-set` now stores `supertype`; new **`fetch-meta`** backfills existing
    bundles via the lightweight metadata API (no ORB rebuild). Loader/saver treat
    it as optional so older bundles still load.
  - me2 re-typed in the committed bundle: **110 Pokémon / 19 Trainer / 1 Energy**.
- **Overlay restyle (same commit).** Ticker + analytics panels are now
  **draggable** in the live window; layout persists per set at
  `sets/<code>/overlay_layout.json` and is reused by `--save` renders
  (`--reset-layout` to ignore it). Fixed ticker line overlap + analytics
  overflow; red-orange gradient accent stripe on both panels. Ticker now shows
  the **slot variant** (reverse holo) rather than the price printing, the exact
  rarity **color-coded by tier**, and the gold **HIT** tag requires rare+ AND raw
  price > $1.50.
- **Tests: 44 green** (added energy exclusion, supertype round-trip, layout drag).

### Next action when resuming (do this first)
**The `COMPLETE` path is validated on real footage.** What's left for full
status-label coverage: record the other two ripping styles (speed-rip → hit for
`SPEED_RIPPED`, fan/hitless for `NO_HIT`) — see the START HERE block. Those tune
hysteresis/burst thresholds on real cadence for the non-COMPLETE labels.

To re-render the validated clip, **render from the raw `IMG_7032.MOV`** (the
clean camera source), not `IMG_7032_fixed.mp4` — that `_fixed` file is a *prior
overlay render with the overlay burned into the pixels*, so feeding it back in
double-stamps the overlay AND the burned-in corner panels knock two borderline
cards below the gate (packs demote COMPLETE → SPEED_RIPPED). On the raw MOV the
result is the validated **30 cards / 3 packs / all COMPLETE / $8.08**:

```powershell
.\.venv\Scripts\python.exe -m packcapture overlay scratch\footage\IMG_7032.MOV --set me2 --save scratch\footage\IMG_7032_overlay_raw.mp4 --export scratch\footage\IMG_7032_overlay_raw.json
```

New footage: transcode if HEVC, **check for pillarbox bars**
(`ffmpeg -i in.mp4 -vf cropdetect -t 20 -f null -`), then overlay (or `dev`) at
the default gate.

Buildable without more footage: the **anchor-and-hold box machine** (lock a
card-sized box on the first confident match, hold through the pack, re-anchor on
PACK_END — rides the same BoundaryDetector states), wiring the BoundaryDetector
into `runner.py` (it's only in devmode so far), and Phase 4 below.

### Next up (in priority order)
1. **Phase 3 finish (rip mode):** anchor-and-hold box machine + BoundaryDetector
   in `runner.py`; tune thresholds on real footage when it lands. Then the
   zone-mode OpenCV confirm-window UI (cv2.selectROI, live overlay, hotkeys)
   reusing the runner for the disciplined `COMPLETE` path.
2. **Session DB + pull-rate stats**, then **CSV/JSON export** (Phases 4-5).
3. **Set-bundling CI** (designed, not built): manual-trigger workflow that builds
   the latest set and publishes the bundle as a GitHub release asset, plus a
   `packcapture fetch-set <code>` command. User wants "latest set only" first.
4. **Coverage badge** once pytest-cov is added to CI.

### Open items / gotchas
- More YouTube footage needs `--cookies-from-browser` (bot challenge) and the
  tool sandbox disabled (CDN blocked); a phone photo of a real card dropped into
  the repo is the fastest clean test input.
- **Never feed an overlay render back into the pipeline as a source.** A
  `--save` render has the overlay burned into the pixels; re-running `overlay`/
  `dev` on it double-stamps the panels and the burned-in corners block
  recognition. Always render from the clean camera source (`IMG_7032.MOV`).
  **Naming trap:** `scratch/footage/IMG_7032_fixed.mp4` *looks* like a clean
  transcode but is actually a prior overlay render (old green-style overlay baked
  in) — do not use it as input. The clean source is the raw `.MOV`; the current
  validated render is `IMG_7032_overlay_raw.mp4`.
- Local-only scratch (git-ignored): `scratch/footage/` has `IMG_7032.MOV` (raw
  tripod source, 3 me2 packs — the clean recognition input), `IMG_7032_overlay_raw.mp4`
  (validated all-COMPLETE overlay render), `rip_long.mp4` (ground-truth montage
  clip), `rip_window_dev_h264.mp4` (validated dev render), `diag2.mp4` (working
  10s clip); helper scripts `scratch/extract_frames.py`, `match_frames.py`,
  `match_crop.py`, `boundary_probe.py` (boundary validation vs. user-eyeballed
  timestamps).

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
  cli.py                 argparse entry point (build-set / match / list-sets /
                         fetch-prices / fetch-meta / dev / overlay)
  config.py              paths, API endpoints, ORB params
  mediautil.py           to_h264(): re-encode a render in place (shared)
  devmode.py             dev viewer: video + auto-ROI + scrolling log, side by side
  overlay.py             rip-mode render: animated price ticker + fixed pack-analytics panel
  api/pokemontcg.py      pokemontcg.io v2 client (paginated, retrying)
  setbuild/builder.py    build-set: fetch + precompute + save
  setbuild/prices.py     fetch-prices: raw TCGPlayer prices -> bundle metadata.db columns
  recognize/
    features.py          ORB extraction + keypoint (de)serialization
    orb_matcher.py       set-locked matcher (ratio test + RANSAC)
  pipeline/              settle / confidence / roi / boundary / session / runner
  capture/source.py      FrameSource: webcam / OBS / video file
  storage/bundle.py      load/save the on-disk bundle (price + supertype columns optional)
tests/                   pytest suite (44 tests; test_overlay.py covers price+export)
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
