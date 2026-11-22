"""Point-local OSM road context via a single Overpass query.

osmnx is the right tool for extracting a routable network, but it is the wrong
tool for "what is near these 7 points": building a graph for a 3 km radius took
minutes and repeatedly timed out. A direct Overpass query with an ``around``
clause per point returns exactly the nearby ways and features in one round trip.

The output is deliberately small — nearest highway class, and distance to the
nearest green area and water — because that is all the street model consumes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Only these highway values affect road_factor, so asking for more wastes time.
_HIGHWAY_FILTER = (
    "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|"
    "secondary_link|tertiary|tertiary_link|residential|unclassified|"
    "living_street|pedestrian|service|footway|path|cycleway"
)

_GREEN_FILTER = '["leisure"~"^(park|garden|nature_reserve)$"]'
_GREEN_LANDUSE = '["landuse"~"^(grass|forest|recreation_ground|meadow)$"]'
_WATER_FILTER = '["natural"~"^(water|wetland)$"]'


@dataclass(frozen=True, slots=True)
class NearbyWay:
    highway: str
    nodes: tuple[tuple[float, float], ...]
    name: str | None


@dataclass(frozen=True, slots=True)
class NearbyArea:
    kind: str
    nodes: tuple[tuple[float, float], ...]


def build_query(
    points: dict[str, Coordinate], *, road_radius_m: int = 150, feature_radius_m: int = 1500
) -> str:
    """One Overpass QL program covering every point."""
    parts: list[str] = ["[out:json][timeout:120];", "("]
    for coordinate in points.values():
        lat, lng = coordinate.lat, coordinate.lng
        parts.append(
            f'  way(around:{road_radius_m},{lat},{lng})["highway"~"^({_HIGHWAY_FILTER})$"];'
        )
        parts.append(f"  way(around:{feature_radius_m},{lat},{lng}){_GREEN_FILTER};")
        parts.append(f"  way(around:{feature_radius_m},{lat},{lng}){_GREEN_LANDUSE};")
        parts.append(f"  way(around:{feature_radius_m},{lat},{lng}){_WATER_FILTER};")
    parts.append(");")
    parts.append("out geom tags;")
    return "\n".join(parts)


def parse_response(
    payload: dict[str, object],
) -> tuple[list[NearbyWay], list[NearbyArea], list[NearbyArea]]:
    ways: list[NearbyWay] = []
    green: list[NearbyArea] = []
    water: list[NearbyArea] = []

    elements = payload.get("elements")
    if not isinstance(elements, list):
        return [], [], []

    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        geometry = element.get("geometry")
        if not isinstance(geometry, list):
            continue
        nodes = tuple(
            (float(node["lat"]), float(node["lon"]))
            for node in geometry
            if isinstance(node, dict) and "lat" in node and "lon" in node
        )
        if len(nodes) < 2:
            continue

        tags = element.get("tags") or {}
        if not isinstance(tags, dict):
            tags = {}

        highway = tags.get("highway")
        if highway is not None:
            ways.append(
                NearbyWay(
                    highway=str(highway),
                    nodes=nodes,
                    name=tags.get("name") if isinstance(tags.get("name"), str) else None,
                )
            )
            continue

        leisure = tags.get("leisure")
        landuse = tags.get("landuse")
        natural = tags.get("natural")
        if leisure in {"park", "garden", "nature_reserve"}:
            green.append(NearbyArea(kind=str(leisure), nodes=nodes))
        elif landuse in {"grass", "forest", "recreation_ground", "meadow"}:
            green.append(NearbyArea(kind=str(landuse), nodes=nodes))
        elif natural in {"water", "wetland"}:
            water.append(NearbyArea(kind=str(natural), nodes=nodes))

    return ways, green, water


_OVERPASS_CACHE: dict[str, tuple[list[NearbyWay], list[NearbyArea], list[NearbyArea]]] = {}


def fetch(
    points: dict[str, Coordinate],
    *,
    road_radius_m: int = 150,
    feature_radius_m: int = 1500,
    timeout_s: float = 180.0,
    attempts: int = 2,
) -> tuple[list[NearbyWay], list[NearbyArea], list[NearbyArea]]:
    """Query Overpass with in-memory caching across mirrors."""
    cache_key = (
        "|".join(f"{sid}:{round(c.lat, 3)},{round(c.lng, 3)}" for sid, c in sorted(points.items()))
        + f":{road_radius_m}:{feature_radius_m}"
    )
    if cache_key in _OVERPASS_CACHE:
        return _OVERPASS_CACHE[cache_key]

    query = build_query(points, road_radius_m=road_radius_m, feature_radius_m=feature_radius_m)
    last_error: Exception | None = None

    for url in OVERPASS_URLS[:attempts]:
        started = time.monotonic()
        try:
            response = httpx.post(
                url,
                data={"data": query},
                timeout=timeout_s,
                headers={"User-Agent": "aeropath-phase0/0.1 (air quality validation)"},
            )
            if response.status_code != httpx.codes.OK:
                raise RuntimeError(f"overpass {url} returned {response.status_code}")
            payload = response.json()
            elapsed = time.monotonic() - started
            ways, green, water = parse_response(payload)
            logger.info(
                "overpass %s: %d ways, %d green, %d water in %.1fs",
                url,
                len(ways),
                len(green),
                len(water),
                elapsed,
            )
            _OVERPASS_CACHE[cache_key] = (ways, green, water)
            return ways, green, water
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("overpass %s failed: %s", url, exc)
            last_error = exc

    raise RuntimeError(f"all Overpass mirrors failed: {last_error}")
