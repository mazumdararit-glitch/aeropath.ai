"""Foot and bike routing via OSRM.

The OSRM demo server at ``router.project-osrm.org`` needs no key, which is why it
is the v1 router. It is also **shared public infrastructure with no SLA**: a v1
dependency and an explicit production risk. Rate limiting surfaces as a non-200
rather than a slow response, so it is mapped to a clear error instead of
propagating a bare httpx exception.

Route geometry is requested as GeoJSON because the segmentiser needs real
coordinates, not an encoded polyline it would have to decode with another
dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

# OSRM profiles available on the public demo server.
PROFILES = ("foot", "bike", "car")


class RoutingError(RuntimeError):
    """Routing failed, or the router returned something unusable."""


@dataclass(frozen=True, slots=True)
class RouteGeometry:
    """One route, as the coordinates the segmentiser will sample along."""

    coordinates: tuple[Coordinate, ...]
    distance_m: float
    duration_s: float


class OsrmRouter:
    def __init__(
        self,
        base_url: str = "https://router.project-osrm.org",
        *,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": "aeropath/0.1 (air quality route planner)"},
        )

    async def __aenter__(self) -> OsrmRouter:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        *,
        profile: str = "foot",
        alternatives: int = 0,
    ) -> list[RouteGeometry]:
        """Routes from origin to destination, best first.

        ``alternatives`` requests additional routes; OSRM does not guarantee it
        can find any, so callers must handle a single-element result.
        """
        if profile not in PROFILES:
            raise ValueError(f"profile must be one of {PROFILES}, got {profile!r}")

        # OSRM takes lon,lat order, which is the opposite of the Coordinate field
        # order. Getting this backwards produces a route in the wrong hemisphere.
        coords = f"{origin.lng},{origin.lat};{destination.lng},{destination.lat}"
        params: dict[str, object] = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
        if alternatives > 0:
            params["alternatives"] = str(alternatives)

        response = await self._client.get(f"/route/v1/{profile}/{coords}", params=params)

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise RoutingError(
                "the OSRM demo server is rate limiting this client; retry later or "
                "self-host a router"
            )
        if response.status_code != httpx.codes.OK:
            raise RoutingError(
                f"routing failed: {response.status_code} {response.text[:300]}"
            )

        payload = response.json()
        if payload.get("code") != "Ok":
            raise RoutingError(f"routing returned code {payload.get('code')!r}")

        routes: list[RouteGeometry] = []
        for entry in payload.get("routes", []):
            geometry = entry.get("geometry") or {}
            raw = geometry.get("coordinates") or []
            if len(raw) < 2:
                continue
            routes.append(
                RouteGeometry(
                    # GeoJSON is [lon, lat].
                    coordinates=tuple(
                        Coordinate(lat=point[1], lng=point[0]) for point in raw
                    ),
                    distance_m=float(entry["distance"]),
                    duration_s=float(entry["duration"]),
                )
            )

        if not routes:
            raise RoutingError("routing returned no usable route geometry")
        return routes
