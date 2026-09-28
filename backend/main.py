from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.utils.config import settings

app = FastAPI(
    title="LearnMesh API",
    description="Shared organizational learning layer for AI agent fleets",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {
        "project": "LearnMesh",
        "tagline": "Correct once. Learn everywhere.",
        "status": "operational"
    }

@app.get("/api/system/status")
def system_status():
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "database": "sqlite_connected"
    }
