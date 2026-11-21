# AEROPATH backend

Route and time planner that ranks outdoor activity by air pollution exposure,
built against `AEROPATH PRD and System Architecture` v0.1.

Phase 0 concluded that the PRD's street-level model **fails its own accuracy
gate**, and the measurement is in [`docs/phase0-results.md`](docs/phase0-results.md).
The product ships without it. A `POST /v1/plan` call routes walking or running
trips, ranks them by inhaled dose, and reports the CPCB index with its category
and a sensitivity-aware advisory.

Be aware of the honest limit: with live data at ~45 km resolution, the API
**ranks times better than it ranks routes**. It tells you today's air and the
cleanest hour, not which side of the street to walk on.

## Quickstart

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m uvicorn aeropath.api.main:app --port 8000
```

No API key needed. The default provider is Open-Meteo's live CAMS feed, which is
free and keyless. Open the interactive map at <http://localhost:8000/map> or the API docs at <http://localhost:8000/docs>:

```bash
curl -X POST localhost:8000/v1/plan -H 'content-type: application/json' -d '{
  "origin":      {"lat": 28.6315, "lng": 77.2167},
  "destination": {"lat": 28.6129, "lng": 77.2295},
  "activity": "walk", "sensitivity": "normal"
}'
```

## Which data source, and why it matters

Two sources exist behind the `GridProvider` seam. They trade freshness against
spatial resolution, and the difference decides what the product can honestly claim.

| | Open-Meteo (default) | XKDR stations |
|---|---|---|
| Freshness | **live** | archive, ends 2025-08 |
| Resolution | ~45 km grid | ~2–3 km station spacing |
| Pollutants | 6 → national AQI | 2 after 2024-12 → sub-index only |
| Key | none | free, `airquality.xkdr.org/signup` |

The XKDR public dataset **collapsed after 2024-12**: 4.1M rows/month became ~1.4k,
covering two stations nationwide. It is a historical archive now. Open-Meteo is
live but its CAMS global grid over India is coarse enough that two Delhi points
2.9 km apart return byte-identical values.

Set `GRID_PROVIDER=xkdr` to use the station archive instead. The response always
states which one served it, via `data_source` and `data_note`.

### Configuration

No configuration is required for the default setup. These environment variables
(or a local `.env`) override it — `.env` is gitignored, and so is every `.env.*`
file, so a key cannot be committed by accident:

| Variable | Default | Purpose |
|---|---|---|
| `GRID_PROVIDER` | `open_meteo` | `open_meteo` or `xkdr` |
| `XKDR_API_KEY` | — | only read when `GRID_PROVIDER=xkdr` |
| `OSRM_BASE_URL` | `router.project-osrm.org` | routing backend |
| `CACHE_TTL_S` | `900` | provider cache lifetime |

## Layout

```
src/aeropath/
  config.py                 settings; missing key fails at startup
  schemas/common.py         shared wire types (no I/O)
  domain/
    grid.py                 H3 grid: point -> cell, bbox -> cells
    cpcb_aqi.py             national AQI from pollutant concentrations
    national_aqi.py         CPCB 24h/8h averaging windows -> index
    units.py                µg/m³ <-> mg/m³, so CO cannot saturate the index
    interpolation.py        IDW from stations to points (harness and product)
    metrics.py              MAPE, category agreement, gate verdict
  services/
    routing.py              route geometry -> grid segments
    planning.py             plan a trip; hour-of-day climatology
    scoring/
      engine.py             exposure scoring: mean_aqi for colour, dose for ranking
      street_model.py       legacy adjustment; production uses IDENTITY_MODEL
      categories.py         CPCB bands, labels, sensitivity advisories
  external/
    provider.py             the grid-data seam (swap providers without touching scoring)
    xkdr_provider.py        real CPCB stations + IDW
    osrm.py                 keyless routing
    cpcb_stations.py        station ground truth (XKDR / CPCB CAAQM)
    google_aq_client.py     Air Quality API, pinned to ind_cpcb (unused: paid)
    overpass.py             point-local OSM road context (unused: see finding 4)
  api/main.py               POST /v1/plan, /healthz
scripts/phase0_validate.py  the accuracy gate
scripts/road_class_residuals.py  does road class explain station residuals?
```

## Setup

Python 3.12+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,geospatial]"
.venv/Scripts/python -m pytest tests -q
```

## Phase 0 gate

```bash
# The gate that matters now. No key needed; validates what production ships.
python scripts/phase0_validate.py station-loo \
    --city Kolkata --start 2024-11-01 --end 2024-11-05 \
    --out docs/phase0-kolkata-noroad.json

# Every city in the dataset, densest network first.
python scripts/phase0_validate.py cities --all-cities --min-stations 8 \
    --start 2024-11-01 --end 2024-11-02 --out docs/phase0-cities.json

# The street-model variant, kept to reproduce the Phase 0 findings.
python scripts/phase0_validate.py station-loo \
    --city Kolkata --start 2024-11-01 --end 2024-11-05 \
    --osm --out docs/phase0-kolkata-osm.json
```

Exit codes: `0` pass, `1` fail, `2` could not run.

`station-loo` without `--osm` is no longer a compromise: with the street model
dropped, leave-one-out validates precisely the interpolation the product uses.

The public XKDR demo key reads **2024** bulk files only and caps at 10k rows per
query. Pass `--api-key` for current data. The client pages by day and splits the
parameter list if a single day still exceeds the cap, so a dense network cannot
silently lose stations; a query that still truncates raises rather than
returning a subset.

## Design decisions worth knowing before reading the code

**The gate is relative to the raw baseline, not absolute.** The original
70%-category target was miscalibrated: raw interpolation itself only reaches
~68% on the CPCB network, so the target measured the network's density rather
than the model. The gate now asks for a gain over a baseline evaluated on the
same samples (`≥5 points` agreement, `≥5%` MAPE), with a floor that marks the
run inconclusive if the baseline is too weak for a relative win to mean
anything. See `docs/phase0-results.md` finding 5.

**Two numbers, never one.** The PRD's formula divides by total length, which ranks
routes by *mean* concentration and silently cancels `activity_factor`. The engine
publishes `mean_aqi` (drives the colour) and `dose` (drives ranking) separately, and
`dose` is the same metric the best-window search minimises.

**The street model scales concentrations, not the index.** CPCB sub-indexes are
piecewise-linear with uneven bands, so `aqi x f` and `aqi(concentration x f)` differ
by up to 80 AQI points and in opposite directions in different bands. See
`docs/phase0-results.md` finding 1.

**A uniform multiplier cannot change a ranking.** Only per-segment factors can, so
without OSM road context the model is a no-op. An `--osm` run that resolves no
road context now aborts instead of quietly reporting a neutral result.

**Road class is a null result, not a tuning problem.** On Delhi's network (37
stations) road class explains 12.8% of between-station residual variance at
p = 0.429, and the sign is inverted: secondary and tertiary roads are *cleaner*
than their neighbours predict. Kolkata agrees (23.3%, p = 0.760). Refitting the
factors cannot fix a proxy that does not locate the effect. See
`docs/phase0-results.md` finding 4 and `scripts/road_class_residuals.py`.

**Production uses `IDENTITY_MODEL`, deliberately.** `rules_v1` remains for the
harness and its tests, but nothing user-facing references it. The identity model
is not "untuned" — it is the correct answer until a proxy that actually locates
local variation exists.

**CO is converted, because the units differ.** CPCB's CO anchors are in **mg/m³**
while most sources emit **µg/m³**. A normal 3675 µg/m³ reading fed in raw hits the
top of the scale and reports sub-index **500 — Severe**. `domain/units.py` converts
by explicit table and raises on unknown units rather than guessing.

**A single hourly sample is not a national AQI.** CPCB averages PM/NO2/SO2 over 24
hours and CO/O3 over 8. `domain/national_aqi.py` builds those windows and excludes
under-covered ones instead of shortening them, so a rush-hour spike never becomes
the reported daily value.

**The best-hour answer is climatology, and says so.** XKDR publishes measurements,
not forecasts. Rather than fake a forecast at 45 km resolution (which is what the
free alternatives offer), the API returns an hour-of-day profile from history with
`basis: "climatology"` and its sample count. Every response carries `basis` so a
measured answer can never be mistaken for a predicted one.

**Removing the adjustment restored the instrument.** Finding 3 rejected
station-to-station LOO because interpolated neighbours already encode their own
road effects. With no adjustment there is nothing to double-count, so LOO now
validates exactly what ships.

**The category is a fact; the advisory is advice.** The number, colour and CPCB
category are population-level and never recoloured per user. Sensitivity adds a
separate `ok` / `caution` / `avoid` layer — recolouring AQI 120 as "Very Poor" for a
sensitive user misreports the air.

**`ind_cpcb`, never UAQI.** Google returns Universal AQI by default, which is a
different scale with different breakpoints. The client requests the Indian index
explicitly and rejects a response that lacks it. The paid Google path is now
unused, but the pinned-index discipline is why the provider seam exists.
