import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.utils.config import settings, validate_startup_config
from backend.utils.logging import setup_logging
from backend.utils.middleware import RequestContextMiddleware, register_exception_handlers
from backend.utils.metrics import get_metrics_response, HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS
from backend.utils.task_queue import task_queue
from backend.repositories.db_session import init_db, get_db
from backend.api.routes import router as api_router
from backend.api.enterprise_routes import enterprise_router

# Initialize structured logging
setup_logging()
logger = logging.getLogger("learnmesh.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing LearnMesh Enterprise Platform...")
    startup_diag = validate_startup_config()
    logger.info(f"Runtime Diagnostics: {startup_diag}")

    # Database initialization
    init_db()

    # Start async task workers
    if settings.FEATURE_BACKGROUND_QUEUE:
        await task_queue.start()

    yield

    # Shutdown
    logger.info("Shutting down LearnMesh Enterprise Platform...")
    if settings.FEATURE_BACKGROUND_QUEUE:
        await task_queue.stop()

app = FastAPI(
    title=settings.APP_NAME,
    description="Shared organizational learning layer for AI agent fleets - Enterprise Edition",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# 1. Attach Request Context Middleware (Correlation IDs & timing)
app.add_middleware(RequestContextMiddleware)

# 2. CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Register global standardized error handling
register_exception_handlers(app)

# 4. Metrics middleware
@app.middleware("http")
async def track_metrics_middleware(request, call_next):
    endpoint = request.url.path
    method = request.method
    import time
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        status_code = 500
        raise
    finally:
        duration = time.perf_counter() - start
        if not endpoint.startswith("/static") and endpoint != "/metrics":
            HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status_code=status_code).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(endpoint=endpoint).observe(duration)
    return response

# 5. Core Root & UI Endpoints
@app.get("/", tags=["Frontend"])
def read_root(request: Request):
    static_file = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "index.html")
    accept = request.headers.get("accept", "")
    if ("text/html" in accept or "*/*" in accept or not accept.startswith("application/json")) and os.path.exists(static_file):
        return FileResponse(static_file)
    return {
        "project": settings.APP_NAME,
        "tagline": "Correct once. Learn everywhere.",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "api_v1": settings.API_V1_STR,
        "docs": "/docs"
    }

@app.get("/health", tags=["Health & Diagnostics"])
def general_health(db: Session = Depends(get_db)):
    """General health check endpoint."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "healthy" if db_ok else "degraded",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": "connected" if db_ok else "disconnected",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# 6. Kubernetes-grade Health Probes
@app.get("/health/live", tags=["Health & Diagnostics"])
def liveness_probe():
    """Liveness probe: returns 200 if the process is alive."""
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/health/ready", tags=["Health & Diagnostics"])
def readiness_probe(db: Session = Depends(get_db)):
    """Readiness probe: validates database connectivity before accepting traffic."""
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "connected",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "error": str(e)}
        )

# 7. Prometheus Metrics Exporter
@app.get("/metrics", tags=["Health & Diagnostics"])
def metrics():
    """Prometheus-compatible scrape endpoint."""
    return get_metrics_response()

# 8. Include Core API Routers:
# Primary enterprise versioned router (/api/v1) and legacy backward-compatibility alias (/api)
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(api_router, prefix="/api")
app.include_router(enterprise_router, prefix=settings.API_V1_STR, tags=["Enterprise Platform"])
app.include_router(enterprise_router, prefix="/api", tags=["Enterprise Platform Legacy"])

# 8b. Optional PRIVATE APIs (local-only, gitignored — never on GitHub/Vercel).
# If backend/api/custom_private.py is absent (e.g. fresh clone / cloud deploy),
# the app simply skips it and runs normally.
try:
    from backend.api.custom_private import private_router
    app.include_router(private_router, prefix=settings.API_V1_STR, tags=["Private APIs"])
    app.include_router(private_router, prefix="/api", tags=["Private APIs"])
    logger.info("Private APIs loaded from backend/api/custom_private.py (local-only).")
except ImportError:
    logger.info("No private APIs found (backend/api/custom_private.py absent) — skipping.")

# 9. Mount frontend public directory
static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/app", tags=["Frontend"])
    def serve_app():
        return FileResponse(os.path.join(static_dir, "index.html"))
