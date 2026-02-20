"""
APScheduler job registry for SatTrack.

All jobs run as async coroutines wrapped in asyncio.run() for the
BackgroundScheduler (thread-based) compatibility.
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


def _run(coro):
    """Synchronous wrapper to run a coroutine from APScheduler's thread pool."""
    try:
        asyncio.run(coro)
    except Exception as exc:
        logger.error("Scheduled job failed: %s", exc)


# --- Job wrappers ---

def job_celestrak_gp():
    from fetchers.celestrak import fetch_celestrak_gp
    _run(fetch_celestrak_gp())


def job_celestrak_supplemental():
    from fetchers.celestrak import fetch_celestrak_supplemental
    _run(fetch_celestrak_supplemental())


def job_space_weather_kp():
    from fetchers.space_weather import fetch_kp_index
    _run(fetch_kp_index())


def job_space_weather_f107():
    from fetchers.space_weather import fetch_f107_flux
    _run(fetch_f107_flux())


def job_satnogs_metadata():
    from fetchers.satnogs import fetch_satnogs_metadata
    _run(fetch_satnogs_metadata())


def job_amsat_elements():
    from fetchers.amsat import fetch_amsat_elements
    _run(fetch_amsat_elements())


def job_gcat_catalog():
    from fetchers.gcat import fetch_gcat_catalog
    _run(fetch_gcat_catalog())


def job_source_health_check():
    """Log a heartbeat — actual health data is written by each fetcher."""
    logger.debug("source_health_check heartbeat")


def create_scheduler() -> BackgroundScheduler:
    """Build and return a configured BackgroundScheduler (not yet started)."""
    scheduler = BackgroundScheduler(timezone="UTC")

    # celestrak_gp — every 60 min
    scheduler.add_job(
        job_celestrak_gp,
        trigger=IntervalTrigger(minutes=60),
        id="celestrak_gp",
        name="CelesTrak GP (all groups)",
        max_instances=1,
        coalesce=True,
    )

    # celestrak_supp — every 15 min (fresh Starlink/OneWeb data)
    scheduler.add_job(
        job_celestrak_supplemental,
        trigger=IntervalTrigger(minutes=15),
        id="celestrak_supp",
        name="CelesTrak Supplemental",
        max_instances=1,
        coalesce=True,
    )

    # space_weather Kp — every 30 min
    scheduler.add_job(
        job_space_weather_kp,
        trigger=IntervalTrigger(minutes=30),
        id="space_weather_kp",
        name="NOAA SWPC Kp index",
        max_instances=1,
        coalesce=True,
    )

    # space_weather F10.7 — daily at 01:00 UTC
    scheduler.add_job(
        job_space_weather_f107,
        trigger=CronTrigger(hour=1, minute=0),
        id="space_weather_f107",
        name="CelesTrak F10.7 flux",
        max_instances=1,
        coalesce=True,
    )

    # satnogs metadata — daily at 02:00 UTC
    scheduler.add_job(
        job_satnogs_metadata,
        trigger=CronTrigger(hour=2, minute=0),
        id="satnogs_metadata",
        name="SatNOGS satellite metadata",
        max_instances=1,
        coalesce=True,
    )

    # amsat elements — every 6 hours
    scheduler.add_job(
        job_amsat_elements,
        trigger=IntervalTrigger(hours=6),
        id="amsat_elements",
        name="AMSAT TLE elements",
        max_instances=1,
        coalesce=True,
    )

    # gcat catalog — daily at 03:00 UTC
    scheduler.add_job(
        job_gcat_catalog,
        trigger=CronTrigger(hour=3, minute=0),
        id="gcat_catalog",
        name="McDowell GCAT catalog",
        max_instances=1,
        coalesce=True,
    )

    # source health heartbeat — every 5 min
    scheduler.add_job(
        job_source_health_check,
        trigger=IntervalTrigger(minutes=5),
        id="source_health_check",
        name="Source health heartbeat",
        max_instances=1,
        coalesce=True,
    )

    return scheduler


def run_initial_ingestion() -> None:
    """
    Run an immediate first ingestion on startup so data flows within seconds.
    Runs in priority order: space weather → celestrak GP → amsat → supplemental.
    """
    import threading

    def _bootstrap():
        logger.info("Starting initial ingestion bootstrap...")
        for job_fn in [
            job_space_weather_kp,
            job_space_weather_f107,
            job_celestrak_gp,
            job_amsat_elements,
            job_celestrak_supplemental,
            job_satnogs_metadata,
            job_gcat_catalog,
        ]:
            try:
                job_fn()
            except Exception as exc:
                logger.error("Bootstrap job %s failed: %s", job_fn.__name__, exc)
        logger.info("Initial ingestion bootstrap complete.")

    t = threading.Thread(target=_bootstrap, daemon=True, name="bootstrap")
    t.start()
