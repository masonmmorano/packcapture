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
