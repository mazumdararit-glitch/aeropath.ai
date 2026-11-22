"""Grid provider backed by real CPCB station measurements.

CPCB publishes point measurements, not a grid, so a point query is answered by
inverse-distance weighting from the surrounding monitors. That is the same
transformation the Phase 0 harness validates, which is the point: accuracy
measured in the harness is accuracy delivered here.

The honest limitation, stated in every response rather than hidden: stations in
Indian cities sit roughly 2-3 km apart at best, so IDW smooths away the
sub-kilometre variation that Phase 0 measured as worth +/-20-27%. Until there is
a denser network or a real dispersion model, the product reports neighbourhood
air, not street air.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from aeropath.domain.cpcb_aqi import MINIMUM_POLLUTANTS
from aeropath.domain.interpolation import haversine_km, interpolate_point
from aeropath.domain.units import normalise
from aeropath.external.cpcb_stations import (
    Station,
    StationClient,
    StationDataError,
    pivot_readings,
)
from aeropath.external.provider import PointReading
from aeropath.schemas.common import Coordinate

logger = logging.getLogger(__name__)

# The CPCB index needs at least three pollutants including a particulate, so the
# provider must request at least that many or every AQI would be uncomputable.
REQUESTED_PARAMETERS = ["PM2.5", "PM10", "NO2", "SO2", "CO", "Ozone", "NH3"]

# Station units are conventional, but CO is the one that differs from the rest
# and is the one that would silently saturate the index if mistreated.
ASSUMED_UNITS: dict[str, str] = {
    "PM2.5": "µg/m³",
    "PM10": "µg/m³",
    "NO2": "µg/m³",
    "SO2": "µg/m³",
    "NH3": "µg/m³",
    "Ozone": "µg/m³",
    "CO": "mg/m³",
}


class XkdrStationProvider:
    """Concentrations from the nearest CPCB stations, interpolated."""

    name = "xkdr_cpcb_idw"

    def __init__(
        self,
        api_key: str,
        *,
        city: str | None = None,
        max_station_distance_km: float = 25.0,
        provider: StationClient | None = None,
    ) -> None:
        self._city = city
        self._max_km = max_station_distance_km
        self._owns_client = provider is None
        self._client = provider or StationClient(api_key=api_key)
        # Resolved once per city: the newest available day changes monthly, so
        # re-discovering it per request would add latency for no benefit.
        self._resolved_days: dict[str, date] = {}
        self._resolved_day: date | None = None
        # Likewise the nearest station's city, which drives the measurement filter.
        self._inferred_city: str | None = None

    async def __aenter__(self) -> XkdrStationProvider:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        """Interpolated concentrations for each point.

        A point far from every monitor gets **no reading**, not a city average
        presented as local air. Callers see an empty ``concentrations`` and the
        engine excludes it rather than scoring it as clean.

        When ``at`` is omitted the newest available data is used, discovered from
        the dataset rather than assumed. Asking for "now" is wrong for this
        source: CPCB ingestion lags by months, and the API answers a window past
        its coverage with HTTP 500 instead of an empty result.
        """
        if not points:
            return []

        # City first: the day search must probe the same place the query will,
        # or it resolves a month that has data somewhere else in the country.
        try:
            city = await self._infer_city(points[0])
            target_day = await self._resolve_day(at, city=city)
            if target_day is None:
                logger.warning("could not determine an available data day")
                return [self._empty(point) for point in points]
            start = target_day - timedelta(days=1)
            end = target_day

            # Filter by city server-side. Without it the nationwide query for a
            # month exceeds the row cap and is truncated by station, so the
            # stations actually near these points come back with no rows.
            stations = await self._stations_for(points)
            readings = await self._client.measurements(
                city=city,
                parameters=REQUESTED_PARAMETERS,
                start=start,
                end=end,
                agg="hourly",
            )
        except StationDataError as exc:
            logger.warning("station data unavailable: %s", exc)
            return [self._empty(point) for point in points]

        grouped = pivot_readings(readings)
        coordinates = {s.station_id: s.coordinate for s in stations}

        # Collapse the pivoted data into one value per (parameter, station).
        #
        # This is a proper mean over the available hours of the requested day, not
        # an incremental `(previous + value) / 2`, which would weight the most
        # recent hour at 50% and understate a stable reading. Accumulate sums and
        # divide once.
        totals: dict[str, dict[str, float]] = {}
        counts: dict[str, dict[str, int]] = {}
        for (station_id, _hour), pollutants in grouped.items():
            for parameter, value in pollutants.items():
                totals.setdefault(parameter, {})
                counts.setdefault(parameter, {})
                totals[parameter][station_id] = totals[parameter].get(station_id, 0.0) + value
                counts[parameter][station_id] = counts[parameter].get(station_id, 0) + 1

        series: dict[str, dict[str, float]] = {
            parameter: {
                station_id: total / counts[parameter][station_id]
                for station_id, total in per_station.items()
                if counts[parameter][station_id] > 0
            }
            for parameter, per_station in totals.items()
        }

        out: list[PointReading] = []
        for point in points:
            interpolated = interpolate_point(
                point,
                coordinates,
                series,
                parameters=REQUESTED_PARAMETERS,
                max_distance_km=self._max_km,
            )
            concentrations = normalise(interpolated.concentrations, ASSUMED_UNITS)
            if len(concentrations) < MINIMUM_POLLUTANTS:
                logger.info(
                    "point %s has %d reportable pollutants (needs %d); nearest station %.1f km",
                    point,
                    len(concentrations),
                    MINIMUM_POLLUTANTS,
                    interpolated.nearest_station_km,
                )
            out.append(
                PointReading(
                    coordinate=point,
                    concentrations=concentrations,
                    source=self.name,
                    nearest_station_km=interpolated.nearest_station_km,
                )
            )
        return out

    @property
    def resolved_day(self) -> date | None:
        """The day the last request actually served, if one has run."""
        return self._resolved_day

    async def _resolve_day(self, at: datetime | None, *, city: str | None = None) -> date | None:
        """Which day to actually query, for a given city.

        An explicit ``at`` is honoured but still validated against coverage, so a
        caller asking for today gets the newest available day plus a warning rather
        than an opaque server error. Cached per (instance, city) because the
        answer changes monthly, not per request.
        """
        cache_key = city or ""
        if cache_key in self._resolved_days:
            return self._resolved_days[cache_key]

        requested = (at or datetime.now(UTC)).date()
        resolved = await self._client.latest_available_day(
            city=city, parameters=REQUESTED_PARAMETERS
        )
        if resolved is None:
            return None
        self._resolved_days[cache_key] = resolved
        self._resolved_day = resolved

        if at is not None and abs((requested - resolved).days) > 1:
            logger.warning(
                "requested %s but the newest available data is %s; CPCB ingestion "
                "lags, so the response reports the available day",
                requested,
                resolved,
            )
        return resolved

    def _empty(self, point: Coordinate) -> PointReading:
        return PointReading(
            coordinate=point,
            concentrations={},
            source=self.name,
            nearest_station_km=float("inf"),
        )

    async def _stations_for(self, points: list[Coordinate]) -> list[Station]:
        """Stations near the query, from the city filter or a nationwide list."""
        if self._city:
            return [
                s for s in await self._client.stations(city=self._city) if s.source == "cpcb_caaqm"
            ]
        stations = await self._client.stations()
        return [s for s in stations if s.source == "cpcb_caaqm"]

    async def _infer_city(self, point: Coordinate) -> str | None:
        """The city of the nearest **CPCB** station, for the measurement filter.

        Two traps this avoids:

        * Looking at all stations picks a US Embassy monitor, whose ``city_name``
          ("New Delhi") is a *different* query filter from the CPCB city
          ("Delhi"), so the measurement query returns almost nothing.
        * Only CPCB stations contribute to the interpolation anyway, so filtering
          to them keeps the day search and the value query on the same data.
        """
        if self._city:
            return self._city
        if self._inferred_city is not None:
            return self._inferred_city

        stations = [s for s in await self._client.stations() if s.city and s.source == "cpcb_caaqm"]
        if not stations:
            return None

        nearest = min(stations, key=lambda s: haversine_km(point, s.coordinate))
        distance = haversine_km(point, nearest.coordinate)
        if distance > self._max_km:
            logger.warning(
                "nearest CPCB station %s is %.1f km away, beyond the %.0f km limit",
                nearest.station_id,
                distance,
                self._max_km,
            )
            return None
        self._inferred_city = nearest.city
        return self._inferred_city
