"""Tests for Task A5: IDW station concentration interpolation and fallback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aeropath.external.idw_provider import StationIDWProvider
from aeropath.external.provider import PointReading
from aeropath.external.station_providers import GroundStationReading
from aeropath.schemas.common import Coordinate


class _MockBaseProvider:
    name: str = "mock_cams_grid"

    async def concentrations_at(
        self, points: list[Coordinate], *, at: datetime | None = None
    ) -> list[PointReading]:
        return [
            PointReading(
                coordinate=p,
                concentrations={"PM2.5": 50.0, "PM10": 100.0, "NO2": 30.0},
                source=self.name,
                nearest_station_km=10.0,
                observed_at=datetime.now(UTC),
            )
            for p in points
        ]

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_idw_interpolates_with_three_or_more_stations() -> None:
    """When >= 3 stations are present, IDW computes inverse-distance weighted concentrations."""
    now = datetime.now(UTC)
    # Target point at (28.60, 77.20)
    # 3 surrounding stations with different distances and concentrations
    stations = [
        GroundStationReading(
            station_id="st1",
            name="Station 1",
            coordinate=Coordinate(lat=28.59, lng=77.20),  # ~1.1 km south
            pollutant="PM2.5",
            value=30.0,
            unit="µg/m³",
            timestamp=now,
            source="test",
            attribution="CPCB",
        ),
        GroundStationReading(
            station_id="st2",
            name="Station 2",
            coordinate=Coordinate(lat=28.61, lng=77.20),  # ~1.1 km north
            pollutant="PM2.5",
            value=70.0,
            unit="µg/m³",
            timestamp=now,
            source="test",
            attribution="CPCB",
        ),
        GroundStationReading(
            station_id="st3",
            name="Station 3",
            coordinate=Coordinate(lat=28.60, lng=77.22),  # ~2.0 km east
            pollutant="PM2.5",
            value=100.0,
            unit="µg/m³",
            timestamp=now,
            source="test",
            attribution="CPCB",
        ),
    ]

    provider = StationIDWProvider(
        _MockBaseProvider(),
        stations_loader=lambda: stations,
        min_stations=3,
        power=2.0,
        max_distance_km=25.0,
    )

    target = [Coordinate(lat=28.60, lng=77.20)]
    readings = await provider.concentrations_at(target)

    assert len(readings) == 1
    pm25 = readings[0].concentrations["PM2.5"]
    assert 45.0 <= pm25 <= 60.0
    assert readings[0].source == "station_idw"


@pytest.mark.asyncio
async def test_idw_falls_back_when_fewer_than_three_stations() -> None:
    """With < 3 stations, falls back to nearest-station bias correction."""
    now = datetime.now(UTC)
    # Only 2 stations available
    stations = [
        GroundStationReading(
            station_id="st1",
            name="Station 1",
            coordinate=Coordinate(lat=28.60, lng=77.21),
            pollutant="PM2.5",
            value=80.0,
            unit="µg/m³",
            timestamp=now,
            source="test",
            attribution="CPCB",
        ),
        GroundStationReading(
            station_id="st2",
            name="Station 2",
            coordinate=Coordinate(lat=28.60, lng=77.22),
            pollutant="PM2.5",
            value=85.0,
            unit="µg/m³",
            timestamp=now,
            source="test",
            attribution="CPCB",
        ),
    ]

    provider = StationIDWProvider(
        _MockBaseProvider(),
        stations_loader=lambda: stations,
        min_stations=3,
    )

    target = [Coordinate(lat=28.60, lng=77.20)]
    readings = await provider.concentrations_at(target)

    assert len(readings) == 1
    assert readings[0].source == "station_corrected_cams"
    assert readings[0].concentrations["PM2.5"] == pytest.approx(72.5, rel=0.1)
