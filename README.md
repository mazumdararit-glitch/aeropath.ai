# AEROPATH backend

Route and time planner that ranks outdoor activity by air pollution exposure,
built against `AEROPATH PRD and System Architecture` v0.1.

Phase 0 (accuracy validation) is in progress — see [`docs/phase0-results.md`](docs/phase0-results.md).
**The gate currently fails**, and the results document explains exactly why.

## Layout

```
src/aeropath/
  schemas/common.py          shared wire types (no I/O)
  domain/
    grid.py                  H3 grid: point -> cell, bbox -> cells
    cpcb_aqi.py             national AQI from pollutant concentrations
    metrics.py              MAPE, category agreement, gate verdict
  services/scoring/
    engine.py               exposure scoring: mean_aqi for colour, dose for ranking
    street_model.py         road/green/water/time adjustment of concentrations
    categories.py           CPCB bands, labels, sensitivity advisories
  external/
    google_aq_client.py     Air Quality API, pinned to ind_cpcb
    cpcb_stations.py        station ground truth (XKDR / CPCB CAAQM)
    overpass.py             point-local OSM road context
    osm_context.py          road classification and proximity in metres
scripts/phase0_validate.py  the accuracy gate
```

## Setup

Python 3.12+. No Docker or `uv` required.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,geospatial]"
.venv/Scripts/python -m pytest tests -q
```

## Phase 0 gate

```bash
# Works today, no Google key needed: validates the methodology against the
# public CPCB station network.
python scripts/phase0_validate.py station-loo \
    --city Kolkata --start 2024-11-01 --end 2024-11-05 \
    --osm --out docs/phase0-osm.json

# Needs a Google Air Quality key and the ingestion path. This is the real gate,
# because its baseline is not built from the stations being tested.
python scripts/phase0_validate.py provider --city Kolkata
```

Exit codes: `0` pass, `1` fail, `2` could not run.

The public XKDR demo key reads **2024** bulk files only and caps at 10k rows per
query. Pass `--api-key` for current data.

## Design decisions worth knowing before reading the code

**Two numbers, never one.** The PRD's formula divides by total length, which ranks
routes by *mean* concentration and silently cancels `activity_factor`. The engine
publishes `mean_aqi` (drives the colour) and `dose` (drives ranking) separately, and
`dose` is the same metric the best-window search minimises.

**The street model scales concentrations, not the index.** CPCB sub-indexes are
piecewise-linear with uneven bands, so `aqi x f` and `aqi(concentration x f)` differ
by up to 80 AQI points and in opposite directions in different bands. See
`docs/phase0-results.md` finding 1.

**A uniform multiplier cannot change a ranking.** Only per-segment factors can, so
without OSM road context the model is a no-op. The harness reports whether context
was available rather than silently producing a neutral result.

**The category is a fact; the advisory is advice.** The number, colour and CPCB
category are population-level and never recoloured per user. Sensitivity adds a
separate `ok` / `caution` / `avoid` layer — recolouring AQI 120 as "Very Poor" for a
sensitive user misreports the air.

**`ind_cpcb`, never UAQI.** Google returns Universal AQI by default, which is a
different scale with different breakpoints. The client requests the Indian index
explicitly and rejects a response that lacks it.
