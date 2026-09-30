"""Teeyooshun web app: entry point.

Run inside the container with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import routes_parent, routes_student
from app.db import init_db

APP_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create or upgrade the tables in the data directory
    yield


app = FastAPI(title="Teeyooshun", version=APP_VERSION, lifespan=lifespan)
app.include_router(routes_student.router)
app.include_router(routes_parent.router)


@app.get("/health")
def health() -> dict:
    """Used by the Docker healthcheck and by James to check the app is up."""
    return {"status": "ok"}
