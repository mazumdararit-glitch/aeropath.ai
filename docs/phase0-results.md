# Phase 0 — accuracy validation results

**Status: gate FAILED.** The street-level model, as specified in PRD 9.3, is
worse than doing nothing on the only data available today. This now holds on a
dense network, not just Kolkata's sparse one — see Finding 6.

**Data:** CPCB CAAQM via XKDR Forum. Kolkata, 2024-11-01 → 2024-11-05. 7 CPCB stations, 951 station-hours, 826 comparable samples.
**Method:** leave-one-out. Each station is hidden; its pollutant concentrations are interpolated from the remaining six by inverse-distance weighting; the street model is applied; both raw and adjusted are scored against the hidden station's own CPCB index.

## Verdict

| | MAPE | Category agreement | RMSE |
|---|---|---|---|
| Raw interpolation | 19.96% | 65.98% | 52.0 |
| Adjusted, no road context | 21.75% | 61.74% | 56.6 |
| Adjusted, with OSM context | 25.12% | 55.45% | — |

The gate requires a **gain over the raw baseline** (≥5 agreement points and
≥5% MAPE), not an absolute category figure — see Finding 5 for why that changed.
Both conditions fail, and adding real OSM road context made it **worse** (−10.5
points, −25.9% MAPE).

---

## Finding 1 — PRD 9.3 is dimensionally wrong

The PRD specifies `segment_aqi = base_aqi × road_factor × time_factor`: multiplying the finished index.

The CPCB sub-index is **piecewise-linear with uneven bands** (PM2.5 spans 30/30/30/30/130 µg/m³). So `aqi × f` is not `aqi(concentration × f)`:

| PM2.5 µg/m³ | AQI | AQI × 1.35 | AQI(conc × 1.35) | gap |
|---|---|---|---|---|
| 30 | 50.0 | 67.5 | 67.5 | 0 |
| 60 | 100.0 | 135.0 | 170.0 | **−35.0** |
| 90 | 200.0 | 270.0 | 301.2 | **−31.2** |
| 120 | 300.0 | 405.0 | 332.3 | **+72.7** |
| 200 | 361.5 | 488.1 | 408.0 | **+80.1** |

The error reaches **80 AQI points and flips sign between bands**. Kolkata sits in Moderate-to-Poor all winter, exactly where the sign flip is largest, so the model would overstate busy roads below AQI 300 and understate them above it.

**Fixed.** `street_model.adjust_concentrations` scales concentrations and the index is recomputed. `adjust_aqi` is retained for index-only inputs and documented as the approximation it is.

## Finding 2 — a uniform multiplier cannot change any ranking

A positive scalar `f` preserves order: if `a > b` then `a·f > b·f`. So `time_factor` and `weather_factor`, which are the same for every segment, can never reorder routes. **Only** per-segment factors — road class, green, water — can.

Consequence: with no OSM context the street model is a no-op that still moves every number. That is what produced the −9% in the first run: pure added error with no added signal. The harness now reports `with_road_context` so this can never be mistaken for a working model.

## Finding 3 — leave-one-out is the wrong instrument

This is the finding that matters most.

Interpolating a station from its neighbours **already encodes the neighbours' road effects**, because those neighbours also sit on roads. Applying `road_factor` afterwards double-counts. That is why OSM context made things worse rather than better, and why the magnitude gets larger as the factors get stronger.

The station-to-station harness therefore cannot validate the street model at all. It can only validate *interpolation*. Testing the adjustment needs a baseline that is **not** built from stations — i.e. the provider's 500 m grid.

## Finding 4 — `road_factor` has the wrong sign on this network

Residual analysis: how much dirtier each station is than its neighbours predict, grouped by OSM road class.

| Road class | n | Mean residual |
|---|---|---|
| secondary | 119 | **−10.90%** |
| tertiary | 119 | **−12.99%** |
| service | 351 | −7.64% |
| residential | 237 | **+5.24%** |

The model asserts secondary > tertiary > residential (1.15 / 1.00 / 1.00). The data says secondary and tertiary roads are **cleaner** than their neighbours predict and residential is **dirtier**.

Two caveats, both important:

* The sample is **7 stations**, far too few to fit 13 road factors. One station per class in most cases.
* The residuals are confounded by genuine local sources. `site_5126` Rabindra Sarobar sits on a lake (−27.4%) and `site_5238` Ballygunge is a dense commercial area (+20.5%). Those are real green/water effects, not road-class effects.

What the data does establish: **local context is worth ±20–27%**, which supports the product premise. What it does not establish: that road class is the right proxy for it.

## Finding 5 — the gate was miscalibrated, and is now relative

| Predictor | MAPE | Category agreement |
|---|---|---|
| Persistence (station's own recent mean) | 43.13% | 49.7% |
| Neighbour IDW (same hour) | 20.63% | 65.6% |

Spatial interpolation beats persistence by a wide margin, so the network is informative. But **raw interpolation itself only reaches ~66%** on Kolkata and ~68% on Delhi. Requiring an adjustment to reach 70% absolute asks the model to beat the network's own spatial noise at 1 station per 30 km² (median nearest neighbour 2.24 km, max 5.96 km).

**Changed.** The gate now asks for a *gain over the raw baseline*, scored on the same samples: ≥5 category-agreement points and ≥5% MAPE reduction. A floor of 50% baseline agreement marks a run `INCONCLUSIVE` rather than `PASS`, so beating a useless baseline cannot manufacture a win. Two of the six cities measured fall below that floor.

## Finding 6 — the failure reproduces on a dense network

Kolkata has 7 stations, which Finding 4 called too few to fit road factors. Delhi has 39 — a 5.5× denser network. If the model's failure were an artifact of sparse data, Delhi should show it.

| City | Stations | Samples | Raw agreement | Adjusted | Gain | ΔMAPE |
|---|---|---|---|---|---|---|
| Delhi | 39 | 1800 | 70.8% | 69.2% | −1.6 pts | −8.0% |
| Mumbai | 30 | 1173 | 52.3% | 52.2% | −0.2 pts | −2.1% |
| Bengaluru | 14 | 610 | 50.5% | 49.3% | −1.1 pts | −1.1% |
| Hyderabad | 14 | 576 | 49.0% | 49.3% | +0.4 pts | +0.1% |
| Pune | 9 | 304 | 50.3% | 48.7% | −1.6 pts | −1.9% |
| Ahmedabad | 8 | 370 | 49.7% | 49.7% | 0.0 pts | −3.2% |

No city passes. Without road context the model is a uniform multiplier, which by Finding 2 cannot reorder anything, so these numbers are pure added error — the expected result, and the control.

Delhi **with** real OSM road context (39/39 stations resolved) is the informative row: **−7.0 points, −7.3% MAPE**. Density did not rescue the model, so the conclusions in Findings 3 and 4 hold.

## Finding 7 — the harness was silently dropping stations

Two bugs meant earlier numbers were computed on a subset of the network while appearing complete:

* **Silent truncation.** The station endpoint caps at 10k rows and applies the cap *after ordering*, so a 5-day Delhi query returned **15 of 39 stations** with HTTP 200. Fixed by paging by day and splitting the parameter list if a single day still exceeds the cap; a query that still truncates now raises.
* **Shared OSM cache.** The cache filename was hardcoded to `kolkata`, so any second city silently reused Kolkata's road geometries. Fixed by keying the file on the query.

Separately, an `--osm` run that resolved no context used to continue and report a neutral result as if it were a test. It now aborts.

---

## What is built and working

- **CPCB AQI from concentrations**, tested at every published band edge (`domain/cpcb_aqi.py`, 30 tests). Verified: 30 µg/m³ → 50, 60 → 100, 90 → 200, saturation at 500, three-pollutant minimum, PM2.5/PM10 requirement.
- **Scoring engine** — pure, deterministic, no I/O. `mean_aqi` drives colour, `dose` drives ranking, ±5% tiebreak, missing cells excluded rather than zeroed (`services/scoring/engine.py`).
- **Street model** — concentration-based, ±40% cap, every factor returned for the planner to cite (`services/scoring/street_model.py`).
- **Metrics and the gate** — MAPE with a near-zero floor, bias, RMSE, category confusion matrix ranked by two-band misses, relative gate verdict logic (`domain/metrics.py`).
- **Providers** — Google Air Quality pinned to `ind_cpcb`, with `history:lookup` for the validation window and page counting; CPCB stations with truncation-safe day paging; point-local Overpass road context.
- **Nationwide validation** — `cities --all-cities` walks every city in the dataset (247 cities, 558 stations), densest network first.
- **252 unit tests passing.**

## What is blocked

The real gate needs the provider grid, which needs a Google Air Quality key and the ingestion path. The client now implements `history:lookup`, which is the endpoint the comparison requires, but it cannot be exercised without a key. Everything above is honest about being an interpolation test, and every output says so.
