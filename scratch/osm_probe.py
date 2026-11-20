"""Scratch: load OSM context for Kolkata and inspect the road mix."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aeropath.external.osm_context import OsmContextLoader, classify_road
from aeropath.schemas.common import Coordinate

OUT = Path(__file__).with_suffix(".log")


def main() -> None:
    loader = OsmContextLoader(REPO_ROOT / "scratch" / "osm_cache")
    roads, green, water, crs = loader.load(
        "Kolkata", center=Coordinate(lat=22.55, lng=88.36), radius_m=3000
    )

    lines = [
        f"crs={crs}",
        f"roads={len(roads)} green={len(green)} water={len(water)}",
        "road class mix:",
    ]
    counts = Counter(classify_road(hw).value for _, hw in roads)
    for name, count in counts.most_common():
        lines.append(f"  {name:14s} {count}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
