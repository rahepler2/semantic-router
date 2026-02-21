"""
Semantic Router Service
-----------------------
FastAPI service wrapping aurelio-labs/semantic-router.
Uses Azure OpenAI embeddings and Typesense as the persistent vector index.
Designed for Azure Container Apps with KEDA autoscaling.

Probes:
  GET /healthz   → liveness   (process alive?)
  GET /readyz    → readiness  (router + index ready?)
  GET /startupz  → startup    (init finished?)
"""

import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query

from semantic_router.routers import SemanticRouter

from app.encoder import build_encoder
from app.routes import build_routes
from app.typesense_index import TypesenseIndex
from app.models import RouteResponse, HealthResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
router_instance: SemanticRouter | None = None
startup_complete: bool = False
startup_time: datetime | None = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global router_instance, startup_complete, startup_time

    logger.info("Initialising semantic router...")
    t0 = time.perf_counter()

    try:
        encoder = build_encoder()
        routes = build_routes()
        index = TypesenseIndex()

        router_instance = SemanticRouter(
            encoder=encoder,
            routes=routes,
            index=index,
            auto_sync="local",
        )

        startup_complete = True
        startup_time = datetime.now(timezone.utc)
        logger.info(
            "Router ready in %.2fs — %d routes, index has %d vectors",
            time.perf_counter() - t0,
            len(routes),
            len(index),
        )
    except Exception:
        logger.exception("Failed to initialise router")

    yield

    logger.info("Shutting down.")
    router_instance = None
    startup_complete = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Semantic Router Service",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
@app.get("/healthz", response_model=HealthResponse, tags=["probes"])
def liveness():
    """Liveness — always 200 if the process is running."""
    return HealthResponse(
        status="alive",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/readyz", response_model=HealthResponse, tags=["probes"])
def readiness():
    """Readiness — 200 only when the router and Typesense index are operational."""
    if not startup_complete or router_instance is None:
        raise HTTPException(status_code=503, detail="Router not ready")

    idx = router_instance.index
    uptime = (
        (datetime.now(timezone.utc) - startup_time).total_seconds()
        if startup_time
        else None
    )

    return HealthResponse(
        status="ready",
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=uptime,
        routes_loaded=len(router_instance.routes),
        index_type=getattr(idx, "type", None),
        index_vectors=len(idx) if idx else None,
    )


@app.get("/startupz", response_model=HealthResponse, tags=["probes"])
def startup_probe():
    """Startup — 503 until init is complete; gives cold starts breathing room."""
    if not startup_complete:
        raise HTTPException(status_code=503, detail="Still starting up")
    return HealthResponse(
        status="started",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@app.get("/route", response_model=RouteResponse, tags=["routing"])
def route_query(query: str = Query(..., min_length=1, description="Text to classify")):
    """Classify a single query into a semantic route."""
    if router_instance is None:
        raise HTTPException(status_code=503, detail="Router not initialised")

    result = router_instance(query)
    return RouteResponse(
        query=query,
        route=result.name if result.name else None,
        similarity_score=result.similarity_score if result.similarity_score else None,
    )


@app.post("/route", response_model=list[RouteResponse], tags=["routing"])
def route_batch(queries: list[str]):
    """Classify a batch of queries."""
    if router_instance is None:
        raise HTTPException(status_code=503, detail="Router not initialised")

    results = []
    for q in queries:
        result = router_instance(q)
        results.append(
            RouteResponse(
                query=q,
                route=result.name if result.name else None,
                similarity_score=result.similarity_score if result.similarity_score else None,
            )
        )
    return results
