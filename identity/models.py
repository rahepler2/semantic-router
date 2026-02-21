"""Pydantic response models for the API."""

from pydantic import BaseModel


class RouteResponse(BaseModel):
    query: str
    route: str | None
    similarity_score: float | None


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    uptime_seconds: float | None = None
    routes_loaded: int | None = None
    index_type: str | None = None
    index_vectors: int | None = None
