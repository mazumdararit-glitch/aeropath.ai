"""V1 API Router aggregator."""

from __future__ import annotations

from fastapi import APIRouter

from aeropath.api.v1.airquality import router as airquality_router
from aeropath.api.v1.places import alerts_router, places_router
from aeropath.api.v1.routes import router as routes_router
from aeropath.api.v1.users import router as users_router

v1_router = APIRouter()
v1_router.include_router(airquality_router)
v1_router.include_router(routes_router)
v1_router.include_router(users_router)
v1_router.include_router(places_router)
v1_router.include_router(alerts_router)
