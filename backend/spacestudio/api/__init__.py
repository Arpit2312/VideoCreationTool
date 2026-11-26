"""FastAPI routers (spec §6), mounted under /api."""
from fastapi import APIRouter

from . import batches, engines, projects, system, videos

api_router = APIRouter(prefix="/api")
for _m in (system, engines, projects, videos, batches):
    api_router.include_router(_m.router)
