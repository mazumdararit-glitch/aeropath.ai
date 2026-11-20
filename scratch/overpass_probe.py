"""Scratch: probe whether OSM's Overpass endpoint responds at all.

If the public API is unreachable, the Phase 0 road-context path cannot be
validated today, and that is worth knowing precisely rather than guessing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT = Path(__file__).with_suffix(".log")
lines: list[str] = []


def probe(name: str, fn: object) -> None:
    started = time.monotonic()
    try:
        result = fn()  # type: ignore[operator]
        elapsed = time.monotonic() - started
        lines.append(f"{name:28s} OK   {elapsed:6.1f}s  {result}")
    except Exception as exc:  # noqa: BLE001 - probing
        elapsed = time.monotonic() - started
        lines.append(f"{name:28s} FAIL {elapsed:6.1f}s  {type(exc).__name__}: {str(exc)[:160]}")


def main() -> None:
    import httpx

    START = time.monotonic()
    try:
        response = httpx.get(
            "https://overpass-api.de/api/status",
            timeout=20.0,
            headers={"User-Agent": "aeropath-phase0/0.1 (validation)"},
        )
        lines.append(f"overpass status              {response.status_code} {response.text[:120]!r}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"overpass status              FAIL {type(exc).__name__}: {exc}")

    try:
        import osmnx as ox

        lines.append(f"osmnx version                {ox.__version__}")

        def small_graph() -> str:
            graph = ox.graph_from_point(
                (22.5448, 88.3404), dist=600, network_type="walk", simplify=True
            )
            return f"{graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"

        probe("graph_from_point 600 m", small_graph)

        def features() -> str:
            gdf = ox.features_from_point(
                (22.5448, 88.3404), tags={"leisure": "park"}, dist=1500
            )
            return f"{len(gdf)} features"

        probe("features park 1500 m", features)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"osmnx probe                  FAIL {type(exc).__name__}: {exc}")

    lines.append(f"total elapsed                {time.monotonic() - START:6.1f}s")
    text = "\n".join(lines)
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
