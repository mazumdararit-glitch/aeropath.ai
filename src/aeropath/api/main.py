"""HTTP surface for AEROPATH.

Deliberately thin. The route-planning logic lives in ``services.planning`` and the
maths in ``services.scoring``, so this module only translates JSON to a request,
calls the service, and translates the answer back. Anything resembling a
calculation here would be a bug: it would be untested by the scoring suite.

Startup is where configuration fails, not the first request. A missing XKDR key
is a deployment mistake and should say so immediately, with the link to fix it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from aeropath.api.map_ui import HTML_PAGE
from aeropath.api.v1 import v1_router
from aeropath.config import Settings, load_settings
from aeropath.external.cpcb_stations import StationClient, StationDataError
from aeropath.external.osrm import OsrmRouter, RoutingError
from aeropath.schemas.routes import (
    PlanRequestModel,
    PlanResponse,
    RouteModel,
    WindowModel,
)
from aeropath.services.planning import DataUnavailableError, PlanRequest, PlanService
from aeropath.services.provider_factory import build_provider
from aeropath.services.scoring import street_model

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, service: PlanService | None = None
) -> FastAPI:
    """Build the app.

    ``service`` exists so tests can inject a stub without reaching into FastAPI's
    dependency graph, which is an implementation detail that changes between
    versions.
    """
    resolved = settings or load_settings()

    app = FastAPI(
        title="AEROPATH",
        description="Route and time planner that ranks outdoor activity by air pollution exposure.",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(v1_router)

    def get_service() -> PlanService:
        if service is not None:
            return service
        # Selecting the provider is configuration; failing here rather than
        # per-request keeps that error at startup where an operator sees it.
        provider = build_provider(resolved)
        router = OsrmRouter(
            resolved.osrm_base_url, timeout_s=resolved.osrm_timeout_s
        )
        street_params = (
            street_model.DEFAULT_MODEL
            if resolved.street_model_version == "rules_v1"
            else street_model.IDENTITY_MODEL
        )
        return PlanService(provider, router, street_params=street_params)

    @app.get("/")
    async def root(request: Request) -> Response:
        """Landing route.

        Renders the interactive map UI directly in web browsers, while returning
        service metadata as JSON for API clients and automated tests.
        """
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(HTML_PAGE)
        return JSONResponse(
            {
                "service": "AEROPATH",
                "version": "0.1.0",
                "provider": resolved.grid_provider,
                "docs": "/docs",
                "map": "/map",
                "health": "/health",
                "plan": "POST /v1/plan",
                "airquality": "GET /v1/airquality",
                "routes_loop": "POST /v1/routes/loop",
                "profile": "GET /v1/profile",
                "places": "GET /v1/places",
            }
        )

    @app.get("/map", response_class=HTMLResponse)
    async def map_view() -> HTMLResponse:
        """Interactive map UI with Leaflet and live air quality overlay."""
        return HTMLResponse(HTML_PAGE)

    @app.get("/v1/geocode")
    async def geocode(q: str) -> list[dict[str, object]]:
        """Search addresses and landmarks via Nominatim with proper headers."""
        import httpx
        url = "https://nominatim.openstreetmap.org/search"
        params: dict[str, str | int] = {
            "format": "json",
            "q": q,
            "limit": 5,
            "countrycodes": "in",
        }
        headers = {"User-Agent": "AEROPATH/0.1 (air quality route planner)"}
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.get(url, params=params, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    return [
                        {
                            "name": str(item.get("display_name")),
                            "lat": float(item["lat"]),
                            "lng": float(item["lon"]),
                        }
                        for item in data
                        if "lat" in item and "lon" in item
                    ]
        except Exception as exc:
            logger.debug("Geocode lookup failed: %s", exc)
        return []

    @app.get("/healthz")
    @app.get("/health")
    @app.get("/v1/providers/health")
    async def health_report(
        svc: PlanService = Depends(get_service),  # noqa: B008
    ) -> dict[str, object]:
        """Diagnostic health report for active air quality providers and fallback chain."""
        base_resp: dict[str, object] = {
            "status": "ok",
            "provider": resolved.grid_provider,
            "xkdr_configured": bool(resolved.xkdr_api_key),
            "router": resolved.osrm_base_url,
        }
        provider = getattr(svc, "_provider", None)
        if provider and hasattr(provider, "get_health_status"):
            chain_health: dict[str, object] = provider.get_health_status()
            base_resp.update({
                "active_provider": chain_health.get("active_provider", resolved.grid_provider),
                "fallback_chain": chain_health.get("fallback_chain", [resolved.grid_provider]),
                "data_age": chain_health.get("data_age"),
                "last_error": chain_health.get("last_error"),
                "last_error_at": chain_health.get("last_error_at"),
                "request_budget": chain_health.get("request_budget"),
            })
        else:
            base_resp.update({
                "active_provider": resolved.grid_provider,
                "fallback_chain": [resolved.grid_provider],
                "data_age": None,
                "last_error": None,
                "last_error_at": None,
                "request_budget": None,
            })
        return base_resp

    @app.get("/v1/coverage")
    async def coverage() -> dict[str, object]:
        """What data exists, and how fresh it is.

        Without this a caller cannot tell live model output from a months-old
        archive, and would read "AQI 180" as today's air when it may describe
        last August.
        """
        if resolved.grid_provider != "xkdr":
            return {
                "provider": "open_meteo_cams",
                "freshness": "live",
                "resolution_km": 45,
                "pollutants": ["PM2.5", "PM10", "NO2", "SO2", "CO", "Ozone"],
                "note": (
                    "Modelled CAMS grid at roughly 45 km over India: every point "
                    "inside a city returns the same values, so this ranks routes "
                    "poorly but reports today's air correctly."
                ),
            }

        try:
            client = StationClient(api_key=resolved.require_xkdr())
            try:
                window = await client.coverage()
            finally:
                await client.aclose()
        except (StationDataError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "first_month": window.first_month,
            "last_month": window.last_month,
            "exported_at": window.exported_at,
            "note": (
                "CPCB ingestion lags the export date. The plan endpoint uses the "
                "newest day that actually has rows, not today."
            ),
        }

    @app.post("/v1/plan", response_model=PlanResponse)
    async def plan(
        body: PlanRequestModel,
        # A default-value dependency rather than ``Annotated[...]``: this module
        # uses ``from __future__ import annotations``, so annotations are strings
        # and FastAPI cannot resolve a closure named in one. The default value is
        # a real object, which it can.
        svc: PlanService = Depends(get_service),  # noqa: B008
    ) -> PlanResponse:
        request = PlanRequest(
            origin=body.origin,
            destination=body.destination,
            activity=body.activity,
            sensitivity=body.sensitivity,
            alternate_routes=body.alternate_routes,
        )
        try:
            result = await svc.plan(request, at=datetime.now(UTC))
        except RoutingError as exc:
            # The router failed, which is upstream and not the caller's fault.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except DataUnavailableError as exc:
            # Routing succeeded; the grid does not cover this area. 503 rather
            # than 502 so an operator checks data coverage, not the router.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return PlanResponse(
            routes=[
                RouteModel(
                    distance_m=route.distance_m,
                    duration_s=route.duration_s,
                    mean_aqi=route.score.mean_aqi,
                    dose=route.score.dose,
                    category=route.score.category,
                    advisory=route.score.advisory,
                    confidence=route.aqi_confidence,
                    basis=route.basis,
                    unscored_fraction=route.score.unscored_fraction,
                    geometry=list(route.geometry),
                    is_national_aqi=route.is_national_aqi,
                    pollutants_used=route.pollutants_used,
                    is_estimated=route.is_estimated,
                    relative_dose_pct=route.relative_dose_pct,
                    route_label=route.route_label,
                )
                for route in result.routes
            ],
            best_windows=[
                WindowModel(
                    hour=window.hour,
                    mean_aqi=window.mean_aqi,
                    category=window.category,
                    dose=window.dose,
                    samples=window.samples,
                    basis=window.basis,
                )
                for window in result.best_windows
            ],
            generated_at=result.generated_at,
            data_source=result.data_source,
            data_date=result.data_date,
            data_note=_data_note(result, provider_name=result.data_source),
        )

    return app


def _data_note(result: object, *, provider_name: str) -> str:
    """Assemble the caveats a caller must not have to rediscover.

    Provider-specific on purpose: the CAMS grid is live but coarse, while XKDR is
    station-accurate but months behind and short of pollutants. Saying the wrong
    one is worse than saying nothing.
    """
    routes = getattr(result, "routes", ())
    notes: list[str] = []

    if provider_name == "open_meteo_cams":
        notes.append(
            "Modelled CAMS grid at roughly 45 km resolution over India. Values are "
            "live but identical across a city, so use them for timing, not for "
            "choosing between nearby streets."
        )
        date = getattr(result, "data_date", None)
        if date:
            notes.append(f"Modelled for {date}.")
        return " ".join(notes)

    if routes and not all(route.is_national_aqi for route in routes):
        notes.append(
            "Index computed from fewer than CPCB's three required pollutants "
            "(the public dataset stops reporting gaseous pollutants after "
            "2024-12). This is a particulate sub-index, not a national AQI."
        )
    date = getattr(result, "data_date", None)
    if date:
        notes.append(f"Measurements describe {date}; CPCB ingestion lags.")
    return " ".join(notes)


app = create_app()
