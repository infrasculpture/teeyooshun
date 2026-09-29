"""Teeyooshun web app: entry point.

Run inside the container with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import init_db

APP_VERSION = "0.2.0"

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create the tables in the data directory if they don't exist
    yield


app = FastAPI(title="Teeyooshun", version=APP_VERSION, lifespan=lifespan)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/health")
def health() -> dict:
    """Used by the Docker healthcheck and by James to check the app is up."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Placeholder home page until the student pages exist."""
    return templates.TemplateResponse(request, "index.html", {"version": APP_VERSION})
