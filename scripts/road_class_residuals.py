"""Does road class explain where a station sits relative to its neighbours?

The leave-one-out gate asks a confounded question. The baseline comes from
neighbouring stations, which also sit on roads, so the interpolation already
encodes an average road effect and applying ``road_factor`` afterwards
double-counts it. That is Finding 3.

This asks the separable question: **is a station on a busy road systematically
dirtier than its neighbours predict?** If the residual is positive for busy
classes in the order the model asserts, ``road_factor`` carries signal worth
calibrating. If it is flat or inverted, the factor is noise and the gate is
right to fail.

Two statistical choices that the earlier 7-station analysis could not make:

* **The station is the unit of analysis, not the sample.** A station's hourly
  residuals are strongly autocorrelated — it is one location with one local
  source mix, measured many times. Treating 700 hourly residuals as 700
  independent observations would shrink a standard error by ~sqrt(hours) and
  report significance for anything. So each station is reduced to one mean
  residual, and the class comparison runs over stations.
* **Significance is bootstrapped over stations.** With a few stations per class
  there is no defensible closed form, and a bootstrap makes the small-n
  fragility visible instead of hiding it behind a t-statistic.

Usage::

    python scripts/road_class_residuals.py --city Delhi --start 2024-11-01 --end 2024-11-05
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from aeropath.domain import cpcb_aqi  # noqa: E402
from aeropath.domain.cpcb_aqi import InsufficientDataError  # noqa: E402
from aeropath.schemas.common import RoadClass  # noqa: E402
from phase0_validate import (  # noqa: E402
    attach_osm_context,
    collect_station_data,
    haversine_km,
)

# The order the model asserts: busier road, dirtier air.
MODEL_ORDER = [
    RoadClass.MOTORWAY,
    RoadClass.TRUNK,
    RoadClass.PRIMARY,
    RoadClass.SECONDARY,
    RoadClass.TERTIARY,
    RoadClass.RESIDENTIAL,
]

BOOTSTRAP_DRAWS = 10_000
SEED = 20241101

# Stations closer than this are the same physical measurement published under
# two IDs. Delhi has two such pairs (Lodhi Road, Pusa) at 0.0 km. They must be
# collapsed: a co-located twin makes inverse-distance weighting divide by zero,
# and in leave-one-out it predicts its own pair near-perfectly, inflating scores.
COLOCATED_KM = 0.05


@dataclass(frozen=True, slots=True)
class StationResidual:
    station_id: str
    road_class: RoadClass
    hours: int
    mean: float
    median: float


def dedupe_colocated(
    stations: list[object],
) -> tuple[list[object], list[tuple[str, str, float]]]:
    """Collapse stations sharing a location, keeping the first by ID.

    Returns the retained stations plus the dropped pairs, so the report can say
    how many measurements were merged rather than silently shrinking the network.
    """
    kept: list[object] = []
    dropped: list[tuple[str, str, float]] = []
    for station in sorted(stations, key=lambda s: s.station_id):  # type: ignore[attr-defined]
        duplicate = None
        for other in kept:
            distance = haversine_km(  # type: ignore[attr-defined]
                station.coordinate,
                other.coordinate,  # type: ignore[attr-defined]
            )
            if distance <= COLOCATED_KM:
                duplicate = (station.station_id, other.station_id, distance)  # type: ignore[attr-defined]
                break
        if duplicate is not None:
            dropped.append(duplicate)
        else:
            kept.append(station)
    return kept, dropped


def station_residuals(
    truth: dict[tuple[str, object], float],
    coords: dict[str, object],
) -> dict[str, list[float]]:
    """Fractional residual of each station against its neighbours, per hour."""
    residuals: dict[str, list[float]] = defaultdict(list)
    by_hour: dict[object, dict[str, float]] = defaultdict(dict)
    for (sid, hour), value in truth.items():
        by_hour[hour][sid] = value

    for readings in by_hour.values():
        for sid, actual in readings.items():
            if actual <= 0:
                continue
            others = {s: v for s, v in readings.items() if s != sid and s in coords}
            if not others:
                continue

            weights: dict[str, float] = {}
            for other_id in others:
                distance = haversine_km(coords[sid], coords[other_id])  # type: ignore[arg-type]
                if distance <= COLOCATED_KM:
                    # Deduplication should have removed these; if one survives,
                    # it dominates and dividing by its square is undefined.
                    weights = {other_id: 1.0}
                    break
                weights[other_id] = 1.0 / distance**2

            total = sum(weights.values())
            if total <= 0:
                continue
            predicted = sum(others[s] * w for s, w in weights.items()) / total
            residuals[sid].append((actual - predicted) / actual)
    return residuals


def summarise(
    residuals: dict[str, list[float]], contexts: dict[str, object]
) -> list[StationResidual]:
    out: list[StationResidual] = []
    for sid, values in residuals.items():
        if not values:
            continue
        context = contexts.get(sid)
        road_class = (
            context.road_class if context is not None else RoadClass.UNKNOWN  # type: ignore[union-attr]
        )
        ordered = sorted(values)
        out.append(
            StationResidual(
                station_id=sid,
                road_class=road_class,
                hours=len(values),
                mean=sum(values) / len(values),
                median=ordered[len(ordered) // 2],
            )
        )
    return sorted(out, key=lambda r: (r.road_class.value, -r.mean))


def bootstrap_mean_ci(
    values: list[float], *, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED
) -> tuple[float, float] | None:
    """Percentile CI for the mean of ``values``, resampling over stations."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [rng.choice(values) for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    lower = means[int(0.025 * draws)]
    upper = means[int(0.975 * draws) - 1]
    return lower, upper


def permutation_p_value(
    group_a: list[float], group_b: list[float], *, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED
) -> float | None:
    """Two-sided permutation test for a difference in means.

    The only assumption is exchangeability under the null, which is exactly the
    hypothesis being tested: that road class does not label a difference.
    """
    if len(group_a) < 2 or len(group_b) < 2:
        return None
    observed = abs(sum(group_a) / len(group_a) - sum(group_b) / len(group_b))
    pooled = group_a + group_b
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        rng.shuffle(pooled)
        a = pooled[: len(group_a)]
        b = pooled[len(group_a) :]
        if abs(sum(a) / len(a) - sum(b) / len(b)) >= observed:
            hits += 1
    return hits / draws


def eta_squared(groups: dict[RoadClass, list[float]]) -> float:
    """Share of between-station variance explained by road class.

    This is the question the pairwise tests cannot answer. A class can differ
    from another while road class still explains almost nothing, if the spread
    *within* each class is larger than the spread between classes — which is
    what a bad proxy looks like.
    """
    all_values = [v for values in groups.values() for v in values]
    if len(all_values) < 2:
        return 0.0
    grand_mean = sum(all_values) / len(all_values)
    ss_total = sum((v - grand_mean) ** 2 for v in all_values)
    if ss_total == 0:
        return 0.0
    ss_between = sum(
        len(values) * (sum(values) / len(values) - grand_mean) ** 2
        for values in groups.values()
        if values
    )
    return ss_between / ss_total


def eta_squared_p_value(
    groups: dict[RoadClass, list[float]], *, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED
) -> float:
    """Permutation test for eta-squared, shuffling class labels over stations.

    With six classes and a few dozen stations, eta-squared is biased upward by
    construction — more groups can always absorb more variance. Permuting the
    labels replaces a rule of thumb with the actual null distribution for this
    sample size.
    """
    observed = eta_squared(groups)
    labels: list[RoadClass] = [label for label, values in groups.items() for _ in values]
    values = [v for group in groups.values() for v in group]
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        shuffled = values[:]
        rng.shuffle(shuffled)
        regrouped: dict[RoadClass, list[float]] = defaultdict(list)
        for label, value in zip(labels, shuffled, strict=True):
            regrouped[label].append(value)
        if eta_squared(regrouped) >= observed:
            hits += 1
    return hits / draws


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="Delhi")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--osm-radius", type=int, default=150)
    parser.add_argument("--osm-cache", default="scratch/osm_cache")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    stations, grouped = asyncio.run(
        collect_station_data(args.city, start=start, end=end, api_key=args.api_key)
    )
    if not stations:
        print(f"no stations for {args.city}", file=sys.stderr)
        return 2

    print(f"{args.city}: {len(stations)} stations, {len(grouped)} station-hours", file=sys.stderr)

    stations, dropped = dedupe_colocated(stations)
    if dropped:
        print(f"collapsed {len(dropped)} co-located duplicate station(s):", file=sys.stderr)
        for kept_id, dropped_id, distance in dropped:
            print(
                f"  {dropped_id} merged into {kept_id} ({distance * 1000:.0f} m apart)",
                file=sys.stderr,
            )
    kept_ids = {s.station_id for s in stations}  # type: ignore[attr-defined]
    grouped = {(sid, hour): v for (sid, hour), v in grouped.items() if sid in kept_ids}

    contexts = attach_osm_context(
        stations,
        cache_dir=Path(args.osm_cache),
        cache_key=f"city_{args.city}",
        radius_m=args.osm_radius,
    )
    resolved = sum(1 for c in contexts.values() if c.road_class is not RoadClass.UNKNOWN)
    print(f"road context resolved for {resolved}/{len(stations)} stations", file=sys.stderr)
    if resolved == 0:
        print("ERROR: no road context; road class cannot be tested.", file=sys.stderr)
        return 2

    truth: dict[tuple[str, object], float] = {}
    for (sid, hour), pollutants in grouped.items():
        try:
            truth[(sid, hour)] = cpcb_aqi.compute(pollutants).aqi
        except InsufficientDataError:
            continue

    coords = {s.station_id: s.coordinate for s in stations}
    per_station = summarise(station_residuals(truth, coords), contexts)

    lines: list[str] = []
    lines.append(f"{args.city}  {start} -> {end}")
    lines.append("")
    lines.append(
        f"{'station':>10} {'road class':>13} {'hours':>6} {'mean resid':>11} {'median':>9}"
    )
    for row in per_station:
        lines.append(
            f"{row.station_id:>10} {row.road_class.value:>13} {row.hours:>6} "
            f"{row.mean:>+10.2%} {row.median:>+8.2%}"
        )

    by_class: dict[RoadClass, list[float]] = defaultdict(list)
    for row in per_station:
        by_class[row.road_class].append(row.mean)

    lines.append("")
    lines.append(
        f"{'road class':>13} {'stations':>8} {'mean resid':>11} {'95% CI':>20} {'spread':>9}"
    )
    ordered_classes = [c for c in MODEL_ORDER if c in by_class]
    ordered_classes += [c for c in by_class if c not in MODEL_ORDER]
    for road_class in ordered_classes:
        values = by_class[road_class]
        mean = sum(values) / len(values)
        ci = bootstrap_mean_ci(values)
        ci_text = f"[{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "n<2, no CI"
        spread = f"{max(values) - min(values):.1%}" if len(values) > 1 else "-"
        lines.append(
            f"{road_class.value:>13} {len(values):>8} {mean:>+10.2%} {ci_text:>20} {spread:>9}"
        )

    lines.append("")
    lines.append("Model asserts busier road => dirtier. Tested pairs (permutation, unit=station):")
    pairs = [
        (RoadClass.SECONDARY, RoadClass.RESIDENTIAL),
        (RoadClass.TERTIARY, RoadClass.RESIDENTIAL),
        (RoadClass.PRIMARY, RoadClass.RESIDENTIAL),
        (RoadClass.SECONDARY, RoadClass.TERTIARY),
    ]
    verdict_sign_flips = 0
    for busier, quieter in pairs:
        a, b = by_class.get(busier, []), by_class.get(quieter, [])
        if not a or not b:
            continue
        difference = sum(a) / len(a) - sum(b) / len(b)
        p = permutation_p_value(a, b)
        p_text = f"p={p:.3f}" if p is not None else "p=n/a"
        # The model asserts busier is dirtier, i.e. a positive difference.
        agrees = difference > 0
        if not agrees:
            verdict_sign_flips += 1
        lines.append(
            f"  {busier.value:>10} - {quieter.value:<11} = {difference:>+8.2%}  "
            f"{p_text:<9} {'agrees with model' if agrees else 'CONTRADICTS model'}"
        )

    # The decisive number. If road class explains little of the between-station
    # variation, no amount of refitting the factors will help: the proxy itself
    # is wrong, not just its constants.
    testable = {k: v for k, v in by_class.items() if v}
    eta = eta_squared(testable)
    eta_p = eta_squared_p_value(testable)
    lines.append("")
    lines.append(
        f"Road class explains {eta:.1%} of between-station variance in the residual "
        f"(permutation p={eta_p:.3f} over {len(testable)} classes, "
        f"{sum(len(v) for v in testable.values())} stations)."
    )
    if eta_p > 0.05:
        lines.append(
            "  -> Not distinguishable from chance. Road class does not explain these "
            "residuals; refitting its factors cannot fix that."
        )
    elif eta < 0.25:
        lines.append("  -> Detectable but weak. Road class is a poor proxy even where it is real.")
    else:
        lines.append("  -> Road class carries real signal; refitting is justified.")

    lines.append("")
    lines.append("A positive residual means the station is dirtier than its neighbours predict.")
    lines.append(
        "If the busier classes are not systematically more positive, road_factor is "
        "not supported by this network."
    )
    lines.append(
        "Caveat: OSM road class describes the nearest road to the station, which for "
        "a rooftop monitor may not be the road driving its exposure."
    )

    text = "\n".join(lines)
    print(text)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwritten to {args.out}", file=sys.stderr)

    return 0 if verdict_sign_flips == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
