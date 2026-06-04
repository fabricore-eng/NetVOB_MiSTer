"""CLI: ``python3 -m tools.discid <VIDEO_TS dir>``.

Prints the disc fingerprint and the resolved title. TMDB enrichment runs only if
``TMDB_API_KEY`` is set in the environment; otherwise the resolver falls back to
sidecar/cache/folder-name and reports ``source=fallback``.
"""

from __future__ import annotations

import argparse
import json
import sys

from .discid import disc_fingerprint
from .metadata import resolve_title


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.discid",
        description="Fingerprint a VIDEO_TS dump and resolve its title.",
    )
    parser.add_argument("video_ts_dir", help="path to a VIDEO_TS directory")
    parser.add_argument(
        "--json", action="store_true", help="emit a single JSON object instead of text"
    )
    parser.add_argument(
        "--no-cache-write",
        action="store_true",
        help="do not persist resolved results to cache.json",
    )
    args = parser.parse_args(argv)

    try:
        fp = disc_fingerprint(args.video_ts_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    result = resolve_title(
        args.video_ts_dir, fp, write_cache=not args.no_cache_write
    )

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"fingerprint: {fp}")
    print(f"title:       {result['title']}")
    if result.get("year"):
        print(f"year:        {result['year']}")
    if result.get("poster"):
        print(f"poster:      {result['poster']}")
    print(f"source:      {result['source']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
