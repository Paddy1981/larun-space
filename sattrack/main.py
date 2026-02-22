"""
SatTrack Phase 1 — Entrypoint

Starts APScheduler in a background thread, runs an immediate first
ingestion, then serves FastAPI on PORT (default 8000).
"""
from __future__ import annotations

import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from api.routes import router
from api.routes_propagate import router as router_propagate
from api.routes_passes import router as router_passes
from api.routes_conjunctions import router as router_conjunctions
from scheduler import create_scheduler, run_initial_ingestion

app = FastAPI(
    title="LARUN SatTrack",
    description="Phase 2 — orbit propagation, pass predictions, conjunction screening",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(router_propagate)
app.include_router(router_passes)
app.include_router(router_conjunctions)


@app.get("/")
def root():
    return {
        "service": "LARUN SatTrack",
        "phase": 2,
        "docs": "/docs",
        "status": "/v1/status",
    }


@app.on_event("startup")
def on_startup():
    logger.info("SatTrack starting up...")

    # Start the recurring scheduler
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    # Fire immediate first ingestion in background thread
    run_initial_ingestion()
    logger.info("Initial ingestion bootstrapped (running in background)")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
