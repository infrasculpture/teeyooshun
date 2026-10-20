"""Teeyooshun web app: entry point.

Run inside the container with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import (routes_ask, routes_parent, routes_review, routes_revise, routes_revise_parent, routes_rewards,
                 routes_rewards_parent, routes_session, routes_student)
from app.db import connect, init_db
from app.topics import TopicsError, load_topics, unknown_question_topics
from app.web import STATIC_DIR

APP_VERSION = "0.6.0"
log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create or upgrade the tables in the data directory
    conn = connect()
    try:
        log.info("Loaded %d topics from topics.yaml", load_topics(conn))
        for question_id, topic in unknown_question_topics(conn):
            log.warning("Question %s has topic '%s', which is not in topics.yaml", question_id, topic)
    except TopicsError as e:
        log.error("Topics not loaded: %s", e)
    finally:
        conn.close()
    yield


app = FastAPI(title="Teeyooshun", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")   # theme.css and original art only
app.include_router(routes_session.router)
app.include_router(routes_rewards.router)
app.include_router(routes_student.router)
app.include_router(routes_parent.router)
app.include_router(routes_ask.router)
app.include_router(routes_review.router)
app.include_router(routes_revise.router)
app.include_router(routes_revise_parent.router)
app.include_router(routes_rewards_parent.router)


@app.get("/health")
def health() -> dict:
    """Used by the Docker healthcheck and by James to check the app is up."""
    return {"status": "ok"}
