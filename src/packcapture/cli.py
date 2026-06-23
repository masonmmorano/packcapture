"""Command-line entry point for PackCapture."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__


def cmd_build_set(args: argparse.Namespace) -> int:
    from .setbuild.builder import build_set

    manifest, paths = build_set(
        args.code,
        image_size=args.image_size,
        force=args.force,
        save_thumbnails=not args.no_thumbnails,
    )
    print(f"Built bundle for '{manifest['set_code']}' ({manifest['set_name']})")
    print(f"  cards:    {manifest['card_count']}")
    print(f"  features: {manifest['feature_count']}")
    if manifest["cards_without_features"]:
        print(f"  warning:  {manifest['cards_without_features']} card(s) had no usable image/features")
    print(f"  dir:      {paths['dir']}")
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    from .recognize.orb_matcher import Matcher
    from .storage.bundle import load_bundle

    bundle = load_bundle(args.set)
    matcher = Matcher(bundle, use_homography=not args.no_homography)
    results = matcher.match_image(args.image, top=args.top)
    if not results:
        print("No match (no features detected in query, or empty bundle).")
        return 1
    print(f"Top {len(results)} matches for {args.image} in set '{args.set}':")
    for i, r in enumerate(results, 1):
        print(
            f"  {i}. {r.name}  #{r.number}  [{r.rarity or '-'}]  "
            f"inliers={r.inliers} good={r.good}  ({r.card_id})"
        )
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    from .devmode import run

    source: object = int(args.source) if str(args.source).isdigit() else args.source
    return run(source, args.set, save=args.save, stable_frames=args.stable_frames,
               min_inliers=args.min_inliers)


def cmd_fetch_prices(args: argparse.Namespace) -> int:
    from .setbuild.prices import update_bundle_prices

    summary = update_bundle_prices(args.code)
    print(f"Updated prices for '{args.code}':")
    print(f"  cards:   {summary['cards']}")
    print(f"  priced:  {summary['priced']}")
    if summary["missing"]:
        print(f"  missing: {summary['missing']} card(s) had no usable price")
    return 0


def cmd_fetch_meta(args: argparse.Namespace) -> int:
    from .setbuild.builder import backfill_supertypes

    summary = backfill_supertypes(args.code)
    print(f"Backfilled supertypes for '{args.code}':")
    print(f"  cards:          {summary['cards']}")
    print(f"  with supertype: {summary['with_supertype']}")
    return 0


def cmd_overlay(args: argparse.Namespace) -> int:
    from .overlay import run, run_live_threaded

    source: object = int(args.source) if str(args.source).isdigit() else args.source
    if args.threaded:
        if args.save:
            print("error: --threaded is for a live window; drop --save for a "
                  "headless render.", file=sys.stderr)
            return 1
        stable = args.stable_frames if args.stable_frames is not None else 1
        return run_live_threaded(
            source, args.set, export=args.export, stable_frames=stable,
            min_inliers=args.min_inliers, facecam_frac=args.facecam_frac,
            reset_layout=args.reset_layout,
        )
    stable = args.stable_frames if args.stable_frames is not None else 5
    return run(
        source, args.set, save=args.save, export=args.export,
        stable_frames=stable, min_inliers=args.min_inliers,
        facecam_frac=args.facecam_frac, reset_layout=args.reset_layout,
    )


def cmd_gui(args: argparse.Namespace) -> int:
    from .overlay_server import gui

    return gui(set_code=args.set, host=args.host, port=args.port)


def cmd_serve(args: argparse.Namespace) -> int:
    from .overlay_server import serve

    source: object = int(args.source) if str(args.source).isdigit() else args.source
    stable = args.stable_frames if args.stable_frames is not None else 1
    return serve(
        source, args.set, host=args.host, port=args.port,
        min_inliers=args.min_inliers, stable_frames=stable, export=args.export,
    )


def cmd_list_cameras(args: argparse.Namespace) -> int:
    from .capture.devices import enumerate_cameras

    cams = enumerate_cameras(max_index=args.max_index)
    if not cams:
        print(f"No usable cameras found (indices 0-{args.max_index}).")
        print("If OBS is running, start its Virtual Camera so it appears here.")
        return 1
    print("Usable cameras:")
    for c in cams:
        fps = f"{c.fps:.0f}fps" if c.fps else "?fps"
        print(f"  index {c.index}:  {c.width}x{c.height}  {fps}")
    print("Pass an index as the source, e.g. `packcapture overlay <index> --set me2`.")
    return 0


def cmd_list_sets(args: argparse.Namespace) -> int:
    from .config import data_dir

    d = data_dir()
    found = (
        sorted(p.name for p in d.iterdir() if (p / "manifest.json").exists())
        if d.exists()
        else []
    )
    if not found:
        print(f"No bundles built yet ({d}).")
        return 0
    print("Built sets:")
    for code in found:
        print(f"  {code}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packcapture",
        description="Computer-vision card logger for Pokémon TCG pack openings.",
    )
    parser.add_argument("--version", action="version", version=f"packcapture {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-set", help="Fetch a set and precompute its recognition bundle")
    b.add_argument("code", help="Set id, e.g. base1, swsh1, sv1")
    b.add_argument("--image-size", choices=["large", "small"], default="large")
    b.add_argument("--force", action="store_true", help="Rebuild even if a bundle exists")
    b.add_argument("--no-thumbnails", action="store_true", help="Skip writing thumbnail images")
    b.set_defaults(func=cmd_build_set)

    m = sub.add_parser("match", help="Match a card image against a built set")
    m.add_argument("image", help="Path to a card photo/crop")
    m.add_argument("--set", required=True, help="Set code of a built bundle")
    m.add_argument("--top", type=int, default=5, help="How many candidates to show")
    m.add_argument("--no-homography", action="store_true", help="Skip RANSAC refinement")
    m.set_defaults(func=cmd_match)

    d = sub.add_parser("dev", help="Dev-mode viewer: live auto-ROI + detections, side by side")
    d.add_argument("source", help="Webcam/OBS device index (e.g. 0) or a video file path")
    d.add_argument("--set", required=True, help="Set code of a built bundle")
    d.add_argument("--save", help="Render the side-by-side to this video file instead of a window")
    d.add_argument("--stable-frames", type=int, default=5,
                   help="Frames an accepted card must persist before it's logged")
    d.add_argument("--min-inliers", type=int, default=25,
                   help="Confidence-gate inlier floor (lower for low-res footage)")
    d.set_defaults(func=cmd_dev)

    fp = sub.add_parser("fetch-prices", help="Refresh raw (TCGPlayer) prices on a built bundle")
    fp.add_argument("code", help="Set code of a built bundle, e.g. me2")
    fp.set_defaults(func=cmd_fetch_prices)

    fm = sub.add_parser("fetch-meta",
                        help="Backfill static card metadata (supertype) onto a built bundle")
    fm.add_argument("code", help="Set code of a built bundle, e.g. me2")
    fm.set_defaults(func=cmd_fetch_meta)

    o = sub.add_parser("overlay", help="Rip-mode price overlay on clean footage (ticker + total)")
    o.add_argument("source", help="Webcam/OBS device index (e.g. 0) or a video file path")
    o.add_argument("--set", required=True, help="Set code of a built bundle")
    o.add_argument("--save", help="Render to this video file instead of showing a window")
    o.add_argument("--export", help="Write a per-card/per-pack analytics JSON to this path")
    o.add_argument("--threaded", action="store_true",
                   help="Live window with recognition on a worker thread (smooth "
                        "video despite slow recognition); for webcam/OBS sources")
    o.add_argument("--stable-frames", type=int, default=None,
                   help="Recognitions an accepted card must persist before it's "
                        "logged (default 5 serial, 2 threaded/live)")
    o.add_argument("--min-inliers", type=int, default=25,
                   help="Confidence-gate inlier floor (lower for low-res footage)")
    o.add_argument("--facecam-frac", type=float, default=0.30,
                   help="Facecam height as a fraction of frame height; the price block sits below it")
    o.add_argument("--reset-layout", action="store_true",
                   help="Ignore the saved panel layout and start from default positions")
    o.set_defaults(func=cmd_overlay)

    g = sub.add_parser("gui",
                       help="Operator control panel in the browser (start/stop, live log, report)")
    g.add_argument("--set", default=None, help="Preselect a set (optional; pick it in the page)")
    g.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    g.add_argument("--port", type=int, default=8770, help="HTTP port (default 8770)")
    g.set_defaults(func=cmd_gui)

    sv = sub.add_parser("serve",
                        help="Serve the live overlay as a web page for an OBS Browser Source")
    sv.add_argument("source", help="Webcam/OBS device index (e.g. 0) or a video file path")
    sv.add_argument("--set", required=True, help="Set code of a built bundle")
    sv.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8770, help="HTTP port (default 8770)")
    sv.add_argument("--export", help="Write a per-card/per-pack analytics JSON on exit")
    sv.add_argument("--stable-frames", type=int, default=None,
                    help="Recognitions an accepted card must persist before logging (default 2)")
    sv.add_argument("--min-inliers", type=int, default=25,
                    help="Confidence-gate inlier floor (lower for low-res footage)")
    sv.set_defaults(func=cmd_serve)

    lc = sub.add_parser("list-cameras",
                        help="Probe device indices (find the OBS Virtual Cam)")
    lc.add_argument("--max-index", type=int, default=10,
                    help="Highest device index to probe (default 10)")
    lc.set_defaults(func=cmd_list_cameras)

    s = sub.add_parser("list-sets", help="List locally built bundles")
    s.set_defaults(func=cmd_list_sets)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, FileExistsError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
