<p align="center">
  <img src="assets/packcapture_banner_gh.png" alt="PackCapture" width="100%">
</p>

<p align="center">
  <b>Computer-vision card logger for Pokémon TCG pack openings.</b><br>
  Point a camera at your pulls — PackCapture detects, recognizes, and logs every
  card automatically, with a live price overlay for streams and clean data export
  instead of manual spreadsheet entry.
</p>

<p align="center">
  <a href="https://github.com/masonmmorano/packcapture/actions/workflows/tests.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/masonmmorano/packcapture/tests.yml?branch=main&style=flat-square&label=tests"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue?style=flat-square"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Built with OpenCV" src="https://img.shields.io/badge/CV-OpenCV-5C3EE8?style=flat-square&logo=opencv&logoColor=white">
  <a href="https://pokemontcg.io"><img alt="Data: Pokémon TCG API" src="https://img.shields.io/badge/data-Pok%C3%A9mon%20TCG%20API-EF5350?style=flat-square"></a>
  <img alt="Status: early development" src="https://img.shields.io/badge/status-early%20development-orange?style=flat-square">
</p>

> **Status:** active development. The live pipeline works end to end — recognize
> cards from a camera, a price overlay for OBS, an operator control panel, and
> CSV/JSON export. A persistent session database and pull-rate analytics are next.

## Install (Windows, Python 3.10+)

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

The **Phantasmal Flames** (`me2`) set ships in the repo with prices baked in, so
recognition works out of the box — no API key, no build step.

## Start it

```powershell
packcapture gui
```

Then open the printed link (**http://localhost:8770/control**) in your browser.

## Work it

In the control panel:

1. **Pick the set** (`me2`) and your **camera** (click **↻ cameras**, or type a
   device index / a video file path). No camera yet? Point it at a recorded clip.
2. Press **Start** and **scan cards** — each one is recognized, priced, and added
   to the live log. Hold each card to the camera for a beat in decent light.
3. Made a bad scan? Click **✕** on that row to delete it (or **Clear all**).
4. Press **Export CSV** when you're done — it opens straight in Google Sheets.

### Show prices on stream (OBS)

PackCapture also serves a transparent overlay at **http://localhost:8770/overlay**.
Add it in OBS as a **Browser Source** and it floats the price ticker + pack
analytics over your camera for viewers.

→ Full walkthrough (camera sharing, OBS Virtual Camera, tips):
**[Live & OBS Setup](../../wiki/Live-and-OBS-Setup)**

## More

- **[CLI Reference](../../wiki/CLI-Reference)** — every command: building other
  sets, refreshing prices, the recorded-clip renderer, dev mode, how recognition
  works, bundle layout.
- **[Live & OBS Setup](../../wiki/Live-and-OBS-Setup)** — the full streaming setup.
- **[Wiki home](../../wiki)** · **[CLAUDE.md](CLAUDE.md)** — design notes and plan.

## License

MIT
