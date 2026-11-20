"""Phase 0 accuracy gate.

Answers one question, with numbers: **is the street-level model better than the
raw provider data?** The PRD calls that model both the main differentiator and
the main accuracy risk (9.2) and never defines "accurate enough", so this script
supplies the missing pass/fail.

Two modes:

``station-loo``
    Leave-one-out cross-validation over the Kolkata monitoring network. Each
    station in turn is hidden, its value interpolated from the remaining
    stations exactly as the grid would interpolate it, and then adjusted by the
    street model using that station's road context. This measures the
    *methodology* — spatial interpolation plus road adjustment — against real,
    measured Kolkata data. It runs today, with no Google key, because the
    station network is public.

``provider``
    The published gate. Fetches the real air quality grid and compares both the
    raw grid value and the adjusted value against station readings.

``station-loo`` cannot replace ``provider``: it validates the adjustment against
station-to-station interpolation, not against the provider's 500 m grid. It is a
smoke test of the model's mechanics on real data, and it is honest about that in
its own output.

Usage::

    python scripts/phase0_validate.py station-loo --city Kolkata --days 30
    python scripts/phase0_validate.py provider --city Kolkata --days 14

Exit code 0 if the gate passes, 1 if it fails, 2 if it could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aeropath.domain import cpcb_aqi  # noqa: E402
from aeropath.domain.cpcb_aqi import InsufficientDataError  # noqa: E402
from aeropath.domain.metrics import (  # noqa: E402
    GateResult,
    Pair,
    category_agreement,
    error_stats,
)
from aeropath.domain.grid import cell_for  # noqa: E402
from aeropath.external.cpcb_stations import (  # noqa: E402
    Reading,
    Station,
    StationClient,
    StationDataError,
    pivot_readings,
)
from aeropath.schemas.common import Coordinate, RoadClass  # noqa: E402
from aeropath.external.osm_context import RoadContext  # noqa: E402
from aeropath.services.scoring import street_model  # noqa: E402
from aeropath.services.scoring.street_model import StreetModelParams  # noqa: E402

# Pollutants needed for a CPCB AQI, and the set the network actually reports.
AQI_PARAMETERS = ["PM2.5", "PM10", "NO2", "SO2", "CO", "Ozone", "NH3"]

# Kolkata's monitoring network covers roughly 1 station per 30 km², so the
# nearest-neighbour distances are large and that is itself a Phase 0 finding.
MAX_NEIGHBOUR_KM = 25.0


def haversine_km(a: Coordinate, b: Coordinate) -> float:
    from math import asin, cos, radians, sin, sqrt

    lat1, lon1, lat2, lon2 = map(radians, (a.lat, a.lng, b.lat, b.lng))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(h))


@dataclass(frozen=True, slots=True)
class StationContext:
    """What the street model needs to know about a station's surroundings."""

    station: Station
    road_class: RoadClass
    green_distance_m: float | None
    water_distance_m: float | None
    nearest_station_km: float
    neighbour_count: int


def inverse_distance_weights(
    target: Coordinate, others: list[Station], *, power: float = 2.0
) -> dict[str, float]:
    """IDW weights for interpolating a point from surrounding stations."""
    weights: dict[str, float] = {}
    for station in others:
        distance = haversine_km(target, station.coordinate)
        if distance <= 0.05:
            # A co-located station dominates; avoid a division by zero.
            return {station.station_id: 1.0}
        if distance > MAX_NEIGHBOUR_KM:
            continue
        weights[station.station_id] = 1.0 / distance**power
    return weights


def interpolate(values: dict[str, float], weights: dict[str, float]) -> float | None:
    usable = {sid: w for sid, w in weights.items() if sid in values}
    if not usable:
        return None
    total = sum(usable.values())
    if total <= 0:
        return None
    return sum(values[sid] * w for sid, w in usable.items()) / total


async def collect_station_data(
    city: str | None = None,
    *,
    state: str | None = None,
    start: date,
    end: date,
    api_key: str | None = None,
) -> tuple[list[Station], dict[tuple[str, datetime], dict[str, float]]]:
    kwargs = {"api_key": api_key} if api_key else {}
    async with StationClient(**kwargs) as client:  # type: ignore[arg-type]
        if city:
            stations = await client.stations(city=city)
        elif state:
            stations = await client.stations(state=state)
        else:
            stations = await client.stations()
        cpcb = [s for s in stations if s.source == "cpcb_caaqm"]
        readings: list[Reading] = await client.measurements(
            city=city,
            state=state,
            parameters=AQI_PARAMETERS,
            start=start,
            end=end,
            agg="hourly",
        )
    return cpcb, pivot_readings(readings)


def station_road_context(
    station: Station, stations: list[Station], road_lookup: dict[str, RoadClass]
) -> StationContext:
    others = [s for s in stations if s.station_id != station.station_id]
    distances = [haversine_km(station.coordinate, s.coordinate) for s in others]
    return StationContext(
        station=station,
        road_class=road_lookup.get(station.station_id, RoadClass.UNKNOWN),
        green_distance_m=None,
        water_distance_m=None,
        nearest_station_km=min(distances) if distances else float("inf"),
        neighbour_count=len(others),
    )


def attach_osm_context(
    stations: list[Station],
    *,
    cache_dir: Path,
    cache_key: str,
    radius_m: int = 150,
) -> dict[str, RoadContext]:
    """Road class and proximity for each station, from OpenStreetMap.

    Uses a single point-local Overpass query rather than building a routable
    graph: extracting a network for a 3 km radius took minutes and repeatedly
    timed out, and the model only needs the nearest road class plus the distance
    to green and water.
    """
    import pickle

    from aeropath.external.overpass import fetch
    from aeropath.external.osm_context import build_context

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Keyed by the query, not a fixed name: a shared file meant a second city
    # silently reused the first city's roads.
    safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in cache_key)
    cache_file = cache_dir / f"{safe_key}_r{radius_m}.pkl"

    points = {s.station_id: s.coordinate for s in stations}

    if cache_file.exists():
        print(f"using cached OSM context {cache_file}", file=sys.stderr)
        with cache_file.open("rb") as handle:
            ways, green, water = pickle.load(handle)  # noqa: S301 - local cache
    else:
        ways, green, water = fetch(points, road_radius_m=radius_m)
        with cache_file.open("wb") as handle:
            pickle.dump((ways, green, water), handle)

    from shapely.geometry import LineString, Polygon

    road_geoms: list[tuple[object, str | None]] = [
        (LineString([(lon, lat) for lat, lon in w.nodes]), w.highway) for w in ways
    ]
    green_geoms: list[object] = [
        Polygon([(lon, lat) for lat, lon in a.nodes]) if len(a.nodes) >= 3 else LineString([(lon, lat) for lat, lon in a.nodes])
        for a in green
    ]
    water_geoms: list[object] = [
        Polygon([(lon, lat) for lat, lon in a.nodes]) if len(a.nodes) >= 3 else LineString([(lon, lat) for lat, lon in a.nodes])
        for a in water
    ]

    contexts: dict[str, RoadContext] = {}
    for station in stations:
        contexts[station.station_id] = build_context(
            station.coordinate,
            roads=road_geoms,
            green_areas=green_geoms,
            water=water_geoms,
            crs="EPSG:32646",
        )
    return contexts


def run_station_loo(
    stations: list[Station],
    grouped: dict[tuple[str, datetime], dict[str, float]],
    *,
    params: StreetModelParams,
    contexts: dict[str, RoadContext],
) -> dict[str, object]:
    """Hide each station, interpolate it, then test the adjustment on it."""
    by_hour: dict[datetime, dict[str, dict[str, float]]] = {}
    for (station_id, hour), pollutants in grouped.items():
        by_hour.setdefault(hour, {})[station_id] = pollutants

    raw_pairs: list[Pair] = []
    adjusted_pairs: list[Pair] = []
    category_pairs_raw: list[tuple[float, float]] = []
    category_pairs_adj: list[tuple[float, float]] = []

    per_station: dict[str, dict[str, float]] = {}
    skipped = {"interpolation": 0, "aqi": 0}
    stations_with_road_context: set[str] = set()

    for hour, readings_by_station in by_hour.items():
        for station in stations:
            actual_pollutants = readings_by_station.get(station.station_id)
            if not actual_pollutants:
                continue
            try:
                actual = cpcb_aqi.compute(actual_pollutants).aqi
            except InsufficientDataError:
                skipped["aqi"] += 1
                continue

            weights = inverse_distance_weights(
                station.coordinate,
                [s for s in stations if s.station_id != station.station_id],
            )
            interpolated: dict[str, float] = {}
            for parameter in AQI_PARAMETERS:
                series = {
                    sid: pollutants[parameter]
                    for sid, pollutants in readings_by_station.items()
                    if parameter in pollutants
                }
                value = interpolate(series, weights)
                if value is not None:
                    interpolated[parameter] = value
            if len(interpolated) < cpcb_aqi.MINIMUM_POLLUTANTS:
                skipped["interpolation"] += 1
                continue

            try:
                raw = cpcb_aqi.compute(interpolated).aqi
            except InsufficientDataError:
                skipped["interpolation"] += 1
                continue

            # Adjust the concentrations and recompute. Multiplying the finished
            # index is not equivalent: the CPCB scale is piecewise-linear with
            # uneven bands, so the two paths disagree by tens of AQI points.
            context = contexts.get(station.station_id)
            road_class = context.road_class if context else RoadClass.UNKNOWN
            scaled, _ = street_model.adjust_concentrations(
                interpolated,
                params,
                road_class=road_class,
                hour_of_day=hour.hour,
                green_distance_m=context.green_distance_m if context else None,
                water_distance_m=context.water_distance_m if context else None,
            )
            if road_class is not RoadClass.UNKNOWN:
                stations_with_road_context.add(station.station_id)
            try:
                adjusted = cpcb_aqi.compute(scaled).aqi
            except InsufficientDataError:
                skipped["interpolation"] += 1
                continue

            raw_pairs.append(Pair(predicted=raw, actual=actual))
            adjusted_pairs.append(Pair(predicted=adjusted, actual=actual))
            category_pairs_raw.append((raw, actual))
            category_pairs_adj.append((adjusted, actual))

            bucket = per_station.setdefault(
                station.station_id, {"n": 0, "raw_abs": 0.0, "adj_abs": 0.0}
            )
            bucket["n"] += 1
            bucket["raw_abs"] += abs(raw - actual) / actual if actual else 0.0
            bucket["adj_abs"] += abs(adjusted - actual) / actual if actual else 0.0

    raw_stats = error_stats("raw interpolation", raw_pairs)
    adjusted_stats = error_stats("adjusted interpolation", adjusted_pairs)
    raw_agreement = category_agreement(category_pairs_raw)
    adjusted_agreement = category_agreement(category_pairs_adj)

    gate = GateResult(
        category_agreement=adjusted_agreement.rate,
        adjusted_mape=adjusted_stats.mape,
        raw_mape=raw_stats.mape,
        raw_category_agreement=raw_agreement.rate,
        samples=adjusted_stats.n,
    )

    return {
        "mode": "station-loo",
        "gate": {
            "verdict": gate.verdict(),
            "passed": gate.passed,
            "samples": gate.samples,
            "category_agreement": round(adjusted_agreement.rate, 4),
            "raw_category_agreement": round(raw_agreement.rate, 4),
            "category_gain_pts": round(gate.category_gain * 100, 2),
            "adjusted_mape": round(adjusted_stats.mape, 2),
            "raw_mape": round(raw_stats.mape, 2),
            "relative_improvement_pct": round(gate.improvement, 2),
            "baseline_informative": gate.baseline_informative,
        },
        "raw": _stats_dict(raw_stats),
        "adjusted": _stats_dict(adjusted_stats),
        "category_agreement": {
            "adjusted": round(adjusted_agreement.rate, 4),
            "raw": round(raw_agreement.rate, 4),
            "worst_confusions": [
                {"predicted": p.value, "actual": a.value, "count": c}
                for p, a, c in adjusted_agreement.worst_confusions[:10]
            ],
        },
        "stations": {
            "count": len(stations),
            "with_road_context": len(stations_with_road_context),
            "road_classes": {
                sid: ctx.road_class.value for sid, ctx in contexts.items()
            },
        },
        "per_station_mape": {
            sid: {
                "n": int(b["n"]),
                "raw_mape": round(100 * b["raw_abs"] / b["n"], 2) if b["n"] else None,
                "adjusted_mape": round(100 * b["adj_abs"] / b["n"], 2) if b["n"] else None,
            }
            for sid, b in per_station.items()
        },
        "skipped": skipped,
        "disclaimer": (
            "station-loo validates the adjustment against station-to-station "
            "interpolation, not against the provider's 500 m grid. It does not "
            "replace the provider gate."
        ),
    }


def _stats_dict(stats: object) -> dict[str, object]:
    return {
        "n": getattr(stats, "n"),
        "mape": round(getattr(stats, "mape"), 2),
        "bias": round(getattr(stats, "bias"), 2),
        "rmse": round(getattr(stats, "rmse"), 3),
        "excluded": getattr(stats, "excluded"),
    }


def grid_survey(stations: list[Station], *, resolution: int = 8) -> dict[str, object]:
    """How much of the city a coarse grid actually covers, and with what support.

    This is the measurement that decides the grid resolution from data instead of
    from a cost model: how many cells, and how far the nearest station is.
    """
    cells: set[str] = set()
    for station in stations:
        cells.add(cell_for(station.coordinate, resolution))
    nearest = []
    for station in stations:
        others = [s for s in stations if s.station_id != station.station_id]
        if others:
            nearest.append(min(haversine_km(station.coordinate, s.coordinate) for s in others))
    return {
        "resolution": resolution,
        "stations": len(stations),
        "station_cells": len(cells),
        "median_nearest_station_km": round(statistics.median(nearest), 2) if nearest else None,
        "max_nearest_station_km": round(max(nearest), 2) if nearest else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["station-loo", "provider", "cities"])
    parser.add_argument(
        "--city",
        default=None,
        help="city to validate (default: Kolkata). Repeat via --city for several.",
        action="append",
    )
    parser.add_argument("--state", default=None, help="validate every city in a state")
    parser.add_argument(
        "--all-cities",
        action="store_true",
        help="validate every city in the dataset, densest network first",
    )
    parser.add_argument("--min-stations", type=int, default=4)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument(
        "--start", default=None, help="YYYY-MM-DD (default: --days before --end)"
    )
    parser.add_argument(
        "--end",
        default=None,
        help="YYYY-MM-DD (default: yesterday). The public demo key only reads 2024.",
    )
    parser.add_argument("--resolution", type=int, default=8)
    parser.add_argument("--api-key", default=None, help="XKDR API key")
    parser.add_argument("--out", default=None, help="write JSON results here")
    parser.add_argument(
        "--osm", action="store_true", help="load OSM road context (slow, needed for a real test)"
    )
    parser.add_argument("--osm-radius", type=int, default=150, help="road query radius, metres")
    parser.add_argument("--osm-cache", default="scratch/osm_cache")
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days)
    days = (end - start).days + 1

    if args.mode == "provider":
        print(
            "provider mode needs a Google Air Quality API key and the ingestion "
            "path, which is not built yet.",
            file=sys.stderr,
        )
        print(
            "Run station-loo or cities for the methodology check that works today.",
            file=sys.stderr,
        )
        return 2

    if args.mode == "cities":
        return await _run_cities(args, start=start, end=end, days=days)

    city = args.city[0] if args.city else "Kolkata"
    print(f"Fetching {days} days of CPCB data for {city} ({start} to {end})...", file=sys.stderr)
    try:
        stations, grouped = await collect_station_data(
            city, start=start, end=end, api_key=args.api_key
        )
    except StationDataError as exc:
        print(f"station data unavailable: {exc}", file=sys.stderr)
        print(
            "The public demo key only reads 2024 bulk files and caps at 10k rows; "
            "pass --start/--end inside 2024, or supply --api-key.",
            file=sys.stderr,
        )
        return 2

    if not stations:
        print(f"no CPCB stations found for {city}", file=sys.stderr)
        return 2

    print(f"{len(stations)} CPCB stations, {len(grouped)} station-hours", file=sys.stderr)

    results = await _validate_one(
        city=city,
        stations=stations,
        grouped=grouped,
        args=args,
        cache_key=f"city_{city}",
    )
    if "error" in results:
        return 2
    results["generated_at"] = datetime.now(UTC).isoformat()
    results["city"] = city
    results["days"] = days
    results["period"] = {"start": start.isoformat(), "end": end.isoformat()}

    print(json.dumps(results, indent=2))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.out}", file=sys.stderr)

    gate = results["gate"]
    assert isinstance(gate, dict)
    if gate.get("samples", 0) == 0:
        print("\nINCONCLUSIVE: no comparable samples.", file=sys.stderr)
        return 2
    print(f"\nGATE: {gate['verdict']}", file=sys.stderr)
    return 0 if gate.get("passed") else 1


async def _validate_one(
    *,
    city: str,
    stations: list[Station],
    grouped: dict[tuple[str, datetime], dict[str, float]],
    args: argparse.Namespace,
    cache_key: str,
) -> dict[str, object]:
    survey = grid_survey(stations, resolution=args.resolution)
    print(f"grid survey: {survey}", file=sys.stderr)

    contexts: dict[str, RoadContext] = {}
    if args.osm:
        print("Loading OSM road context (slow, cached after first run)...", file=sys.stderr)
        try:
            contexts = attach_osm_context(
                stations,
                cache_dir=Path(args.osm_cache),
                cache_key=cache_key,
                radius_m=args.osm_radius,
            )
        except Exception as exc:  # noqa: BLE001 - OSM is a public, flaky service
            print(
                f"OSM context fetch failed ({type(exc).__name__}: {exc}). Road "
                "context is the whole point of --osm; retry, or drop --osm to run "
                "the no-context variant deliberately.",
                file=sys.stderr,
            )
            return {"error": "osm_fetch_failed", "detail": str(exc)}
    else:
        print("No --osm flag: road context is UNKNOWN and the model is a no-op.", file=sys.stderr)

    with_context = sum(1 for c in contexts.values() if c.road_class is not RoadClass.UNKNOWN)
    print(f"road context resolved for {with_context}/{len(stations)} stations", file=sys.stderr)

    # An --osm run that resolved nothing is not a test of the street model: the
    # model degenerates to a uniform multiplier, which Finding 2 shows cannot
    # reorder anything. Say so rather than reporting it as a real result.
    if args.osm and with_context == 0:
        print(
            "ERROR: --osm was requested but no road context resolved. The street "
            "model would be a uniform multiplier and this run would not test it.",
            file=sys.stderr,
        )
        return {"error": "osm_context_unavailable", "stations": len(stations)}
    if args.osm and with_context < len(stations):
        print(
            f"WARNING: road context missing for {len(stations) - with_context} "
            "stations; those use neutral factors and understate the model.",
            file=sys.stderr,
        )

    results = run_station_loo(
        stations, grouped, params=street_model.DEFAULT_MODEL, contexts=contexts
    )
    results["grid_survey"] = survey
    results["model_version"] = street_model.DEFAULT_MODEL.version
    return results


async def _run_cities(
    args: argparse.Namespace, *, start: date, end: date, days: int
) -> int:
    """Validate city by city across the network, densest first."""
    try:
        async with StationClient(**({"api_key": args.api_key} if args.api_key else {})) as client:  # type: ignore[arg-type]
            all_stations = (
                await client.stations(state=args.state)
                if args.state
                else await client.stations()
            )
    except StationDataError as exc:
        print(f"station list unavailable: {exc}", file=sys.stderr)
        return 2

    cpcb = [s for s in all_stations if s.source == "cpcb_caaqm" and s.city]
    if args.city:
        wanted = {c.lower() for c in args.city}
        cpcb = [s for s in cpcb if (s.city or "").lower() in wanted]

    by_city: dict[str, list[Station]] = {}
    for station in cpcb:
        assert station.city is not None
        by_city.setdefault(station.city, []).append(station)

    ranked = sorted(by_city.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if not args.all_cities and not args.city:
        print(
            "warning: no --city given; this runs every city. Pass --all-cities to "
            "confirm the intent.",
            file=sys.stderr,
        )
    eligible = [(c, s) for c, s in ranked if len(s) >= args.min_stations]
    print(
        f"{len(by_city)} cities with CPCB stations, {len(eligible)} with "
        f">= {args.min_stations} stations ({days} days each)",
        file=sys.stderr,
    )

    summaries: list[dict[str, object]] = []
    for index, (city, stations) in enumerate(eligible, start=1):
        print(f"\n[{index}/{len(eligible)}] {city} ({len(stations)} stations)", file=sys.stderr)
        try:
            _, grouped = await collect_station_data(
                city, start=start, end=end, api_key=args.api_key
            )
            result = await _validate_one(
                city=city,
                stations=stations,
                grouped=grouped,
                args=args,
                cache_key=f"city_{city}",
            )
        except StationDataError as exc:
            print(f"  skipped: {exc}", file=sys.stderr)
            continue

        if "error" in result:
            print(f"  skipped: {result['error']}", file=sys.stderr)
            continue

        gate = result["gate"]
        assert isinstance(gate, dict)
        summaries.append(
            {
                "city": city,
                "stations": len(stations),
                "samples": gate.get("samples"),
                "verdict": gate.get("verdict"),
                "passed": gate.get("passed"),
                "raw_category_agreement": gate.get("raw_category_agreement"),
                "category_agreement": gate.get("category_agreement"),
                "raw_mape": gate.get("raw_mape"),
                "adjusted_mape": gate.get("adjusted_mape"),
                "relative_improvement_pct": gate.get("relative_improvement_pct"),
            }
        )
        print(f"  {gate.get('verdict')}", file=sys.stderr)

    payload = {
        "mode": "cities",
        "generated_at": datetime.now(UTC).isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "days": days,
        "state": args.state,
        "model_version": street_model.DEFAULT_MODEL.version,
        "with_osm": bool(args.osm),
        "cities": summaries,
    }

    print(json.dumps(payload, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwritten to {args.out}", file=sys.stderr)

    passed = sum(1 for s in summaries if s["passed"])
    print(f"\n{passed}/{len(summaries)} cities passed the gate", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
