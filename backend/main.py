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

from backend.repositories.db_session import init_db
from backend.api.routes import router as api_router

init_db()

app.include_router(api_router)
