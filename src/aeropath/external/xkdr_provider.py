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
from datetime import UTC, datetime, timedelta

from aeropath.domain.cpcb_aqi import MINIMUM_POLLUTANTS
from aeropath.domain.interpolation import interpolate_point
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
        """
        if not points:
            return []

        # One day of history, so the 24-hour CPCB windows can be built downstream.
        end = (at or datetime.now(UTC)).date()
        start = end - timedelta(days=1)

        try:
            stations = await self._stations_for(points)
            readings = await self._client.measurements(
                city=self._city,
                parameters=REQUESTED_PARAMETERS,
                start=start,
                end=end,
                agg="hourly",
            )
        except StationDataError as exc:
            logger.warning("station data unavailable: %s", exc)
            return [
                PointReading(
                    coordinate=point,
                    concentrations={},
                    source=self.name,
                    nearest_station_km=float("inf"),
                )
                for point in points
            ]

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
                totals[parameter][station_id] = (
                    totals[parameter].get(station_id, 0.0) + value
                )
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

    async def _stations_for(self, points: list[Coordinate]) -> list[Station]:
        """Stations near the query, from the city filter or a nationwide list.

        A nationwide station list is ~558 rows and is cached by the caller, so it
        is cheaper than guessing which city a coordinate belongs to.
        """
        if self._city:
            return [
                s
                for s in await self._client.stations(city=self._city)
                if s.source == "cpcb_caaqm"
            ]
        stations = await self._client.stations()
        return [s for s in stations if s.source == "cpcb_caaqm"]
