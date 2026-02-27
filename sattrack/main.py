"""
SatTrack — Entrypoint

Starts APScheduler in a background thread, runs an immediate first
ingestion, then serves FastAPI on PORT (default 8000).
"""
from __future__ import annotations

import logging
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from api.auth import router as router_auth
from api.routes import router
from api.routes_propagate import router as router_propagate
from api.routes_passes import router as router_passes
from api.routes_conjunctions import router as router_conjunctions
from api.routes_ml import router as router_ml
from scheduler import create_scheduler, run_initial_ingestion

# ── OpenAPI tag metadata ───────────────────────────────────────────────────────

TAGS_METADATA = [
    {
        "name": "Satellites",
        "description": (
            "Search and retrieve satellite records from a **67 K-entry SATCAT** database. "
            "Supports filtering by orbit class, status, and name search with cursor-based pagination."
        ),
    },
    {
        "name": "TLE",
        "description": (
            "Two-Line Element sets. Retrieve the current best TLE for any tracked object, "
            "or fetch up to 2 000 TLEs in a single bulk request."
        ),
    },
    {
        "name": "Propagation",
        "description": (
            "**SGP4** orbit propagation. Get the real-time ECEF position of any satellite, "
            "compute observer look-angles (azimuth / elevation / range), "
            "or generate a ground-track for visualisation."
        ),
    },
    {
        "name": "Passes",
        "description": (
            "Predict when a satellite will pass over a ground observer. "
            "Supports a **1–14 day** prediction window with configurable minimum-elevation filtering. "
            "GEO satellites return a single geostationary entry."
        ),
    },
    {
        "name": "Conjunctions",
        "description": (
            "Near-miss screening. Returns close-approach events within a configurable "
            "miss-distance threshold and sliding time window, enriched with **ML risk scores**."
        ),
    },
    {
        "name": "Space Weather",
        "description": (
            "Latest **Kp** geomagnetic index and **F10.7** solar flux — "
            "used internally for atmospheric drag modelling."
        ),
    },
    {
        "name": "ML Insights",
        "description": (
            "Machine-learning derived analytics: detected **maneuver events**, "
            "orbital **decay / reentry predictions**, and orbital density maps."
        ),
    },
    {
        "name": "Auth",
        "description": "JWT verification and user profile.",
    },
    {
        "name": "System",
        "description": "Health checks, data-source freshness, and administrative utilities.",
    },
]

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LARUN SatTrack API",
    description="""
Real-time satellite tracking and space-situational awareness REST API.
No authentication required for read endpoints.

## Capabilities

| Group | What it does |
|---|---|
| **Satellites** | Search 67 K+ catalogued objects |
| **TLE** | Fetch current Two-Line Elements |
| **Propagation** | SGP4 orbit propagation & ground tracks |
| **Passes** | Ground-station pass predictions |
| **Conjunctions** | Close-approach / collision screening |
| **Space Weather** | Live Kp index & F10.7 solar flux |
| **ML Insights** | Maneuver detection, decay forecasts, density maps |
| **System** | Health checks & source freshness |

## Base URL
```
https://sattrack-production.up.railway.app
```

## Notes
- All timestamps are **UTC ISO 8601**
- Paginate large satellite lists with `after_id` (cursor) or `offset`
- Conjunction data refreshes every **6 hours**; ML decay predictions refresh **daily**
""",
    version="2.0.0",
    contact={
        "name": "Larun Engineering",
        "url": "https://sattrack.larun.space",
    },
    docs_url=None,       # replaced by custom /docs below
    redoc_url="/redoc",
    openapi_tags=TAGS_METADATA,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://sattrack.larun.space",
        "https://larun.space",
        "http://localhost:3000",   # local dev
        "http://localhost:5500",   # local dev (Live Server)
        "http://127.0.0.1:5500",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(router_auth)
app.include_router(router)
app.include_router(router_propagate, tags=["Propagation"])
app.include_router(router_passes,    tags=["Passes"])
app.include_router(router_conjunctions, tags=["Conjunctions"])
app.include_router(router_ml,        tags=["ML Insights"])


# ── Custom Swagger UI ─────────────────────────────────────────────────────────

_SWAGGER_CSS = """
/* ── LARUN SatTrack — Dark API Docs Theme ── */

/* Global */
*, *::before, *::after { box-sizing: border-box; }
body {
  background: #06060f;
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
}

/* Custom header */
.larun-header {
  position: sticky; top: 0; z-index: 1000;
  background: linear-gradient(180deg, #0d0d1f 0%, #0a0a18 100%);
  border-bottom: 1px solid #1e2040;
  height: 56px;
  display: flex; align-items: center;
  padding: 0 24px;
}
.larun-header-inner {
  max-width: 1460px; margin: 0 auto; width: 100%;
  display: flex; align-items: center; gap: 14px;
}
.larun-brand { display: flex; align-items: center; gap: 10px; flex: 1; }
.larun-sat-icon { width: 26px; height: 26px; flex-shrink: 0; }
.larun-title {
  font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
  color: #f1f5f9; text-decoration: none;
}
.larun-title .dot { color: #6366f1; }
.larun-sep { color: #2d3060; font-size: 18px; line-height: 1; }
.larun-badge {
  background: rgba(99,102,241,0.12); color: #a5b4fc;
  border: 1px solid rgba(99,102,241,0.25);
  border-radius: 20px; padding: 3px 12px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
}
.larun-nav { display: flex; align-items: center; gap: 20px; }
.larun-nav a {
  color: #4b5690; font-size: 13px; text-decoration: none;
  transition: color 0.15s;
}
.larun-nav a:hover { color: #a5b4fc; }
.larun-version-pill {
  background: #12122a; color: #4b5690;
  border: 1px solid #1e2040;
  border-radius: 4px; padding: 2px 8px;
  font-size: 11px; font-family: ui-monospace, monospace;
}
.larun-status-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #10b981; box-shadow: 0 0 6px #10b981;
  animation: pulse-green 2.4s ease-in-out infinite;
}
@keyframes pulse-green {
  0%, 100% { opacity: 1; box-shadow: 0 0 6px #10b981; }
  50%       { opacity: 0.4; box-shadow: 0 0 2px #10b981; }
}

/* Swagger UI resets */
.swagger-ui { background: transparent; font-family: inherit; }
.swagger-ui .topbar { display: none !important; }
.swagger-ui .wrapper { padding: 24px; max-width: 1460px; }

/* Info block */
.swagger-ui .info {
  background: #0d0d1f;
  border: 1px solid #1e2040;
  border-radius: 12px;
  padding: 28px 32px;
  margin: 0 0 20px;
}
.swagger-ui .info .title {
  color: #e2e8f0; font-size: 24px; font-weight: 700; letter-spacing: -0.02em;
}
.swagger-ui .info .title small.version-stamp {
  background: #6366f1; color: #fff;
  border-radius: 20px; padding: 2px 12px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.05em; vertical-align: middle;
}
.swagger-ui .info p, .swagger-ui .info li { color: #8892b0; font-size: 14px; line-height: 1.7; }
.swagger-ui .info a { color: #6366f1; }
.swagger-ui .info table { width: 100%; border-collapse: collapse; margin: 12px 0; }
.swagger-ui .info table th {
  background: #12122a; color: #c4cae8;
  padding: 8px 12px; text-align: left;
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
  border: 1px solid #1e2040;
}
.swagger-ui .info table td { padding: 8px 12px; color: #8892b0; font-size: 13px; border: 1px solid #1e2040; }
.swagger-ui .info code {
  background: #12122a; color: #7dd3fc;
  border: 1px solid #1e2040; border-radius: 6px;
  padding: 8px 16px; font-size: 13px; display: block;
  font-family: ui-monospace, "Fira Code", monospace;
}

/* Scheme container */
.swagger-ui .scheme-container {
  background: #0d0d1f; border: 1px solid #1e2040;
  border-radius: 8px; padding: 14px 20px; margin-bottom: 20px; box-shadow: none;
}
.swagger-ui .scheme-container .schemes > label { color: #8892b0; font-size: 12px; }

/* Tag sections */
.swagger-ui .opblock-tag {
  color: #c4cae8; border-bottom: 1px solid #1e2040;
  font-size: 14px; font-weight: 600; padding: 14px 4px; margin-top: 4px;
}
.swagger-ui .opblock-tag:hover { background: rgba(99,102,241,0.04); border-radius: 6px; }
.swagger-ui .opblock-tag small { color: #4b5690; font-size: 12px; font-weight: 400; }
.swagger-ui .opblock-tag-section { margin-bottom: 12px; }
.swagger-ui .opblock-tag svg { fill: #6366f1 !important; }

/* Operation blocks */
.swagger-ui .opblock {
  background: #0d0d1f; border: 1px solid #1e2040;
  border-radius: 8px; margin: 5px 0; box-shadow: none;
  transition: border-color 0.15s;
}
.swagger-ui .opblock:hover { border-color: #2d3060; }
.swagger-ui .opblock.is-open { border-color: #2d3060; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
.swagger-ui .opblock .opblock-summary { padding: 10px 16px; border-radius: 8px; }
.swagger-ui .opblock.is-open .opblock-summary { border-radius: 8px 8px 0 0; }

/* GET */
.swagger-ui .opblock.opblock-get { border-left: 3px solid #6366f1; }
.swagger-ui .opblock.opblock-get .opblock-summary { background: transparent; }
.swagger-ui .opblock.opblock-get .opblock-summary-method { background: #6366f1; }
/* POST */
.swagger-ui .opblock.opblock-post { border-left: 3px solid #10b981; }
.swagger-ui .opblock.opblock-post .opblock-summary-method { background: #10b981; }
/* DELETE */
.swagger-ui .opblock.opblock-delete { border-left: 3px solid #ef4444; }
.swagger-ui .opblock.opblock-delete .opblock-summary-method { background: #ef4444; }
/* PATCH */
.swagger-ui .opblock.opblock-patch { border-left: 3px solid #f59e0b; }
.swagger-ui .opblock.opblock-patch .opblock-summary-method { background: #f59e0b; }
/* PUT */
.swagger-ui .opblock.opblock-put { border-left: 3px solid #8b5cf6; }
.swagger-ui .opblock.opblock-put .opblock-summary-method { background: #8b5cf6; }

/* Method badge */
.swagger-ui .opblock-summary-method {
  border-radius: 5px; font-size: 10px; font-weight: 800;
  min-width: 58px; letter-spacing: 0.06em; padding: 4px 8px; text-align: center;
}
/* Path */
.swagger-ui .opblock-summary-path {
  color: #c4cae8; font-family: ui-monospace, "Fira Code", monospace; font-size: 13px;
}
.swagger-ui .opblock-summary-description { color: #4b5690; font-size: 13px; }

/* Expanded body */
.swagger-ui .opblock-body { background: #070712; border-top: 1px solid #1e2040; border-radius: 0 0 8px 8px; }
.swagger-ui .opblock-section-header { background: #0a0a18; border-bottom: 1px solid #1e2040; padding: 8px 20px; }
.swagger-ui .opblock-section-header h4 {
  color: #4b5690; font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.08em; margin: 0;
}

/* Parameters */
.swagger-ui table thead tr td, .swagger-ui table thead tr th {
  color: #4b5690; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid #1e2040; padding: 8px 12px;
}
.swagger-ui .parameter__name { color: #c4cae8; font-weight: 600; }
.swagger-ui .parameter__type { color: #7dd3fc; font-size: 12px; font-family: monospace; }
.swagger-ui .parameter__in {
  color: #a5b4fc; background: rgba(99,102,241,0.1);
  border-radius: 3px; padding: 1px 5px; font-size: 11px; font-weight: 600;
}
.swagger-ui td { color: #8892b0; padding: 8px 12px; border-bottom: 1px solid #111128; }

/* Buttons */
.swagger-ui .btn.execute {
  background: #6366f1; border: none; color: #fff;
  border-radius: 6px; font-weight: 700; font-size: 13px; padding: 8px 20px;
  transition: background 0.15s;
}
.swagger-ui .btn.execute:hover { background: #4f46e5; }
.swagger-ui .btn.cancel { border-color: #ef4444; color: #ef4444; border-radius: 6px; }
.swagger-ui .btn.authorize { border-color: #6366f1; color: #a5b4fc; border-radius: 6px; font-weight: 600; }
.swagger-ui .btn.authorize svg { fill: #6366f1; }
.swagger-ui .try-out__btn {
  border-color: #2d3060; color: #6b7a9e; border-radius: 6px;
  font-size: 12px; font-weight: 600; transition: all 0.15s;
}
.swagger-ui .try-out__btn:hover { border-color: #6366f1; color: #a5b4fc; }

/* Inputs */
.swagger-ui input[type=text], .swagger-ui input[type=password],
.swagger-ui input[type=search], .swagger-ui textarea, .swagger-ui select {
  background: #12122a; border: 1px solid #2d3060;
  border-radius: 6px; color: #c4cae8; font-size: 13px; padding: 7px 12px;
}
.swagger-ui input:focus, .swagger-ui textarea:focus, .swagger-ui select:focus {
  border-color: #6366f1; outline: none; box-shadow: 0 0 0 2px rgba(99,102,241,0.2);
}
.swagger-ui .filter .operation-filter-input {
  background: #12122a; border: 1px solid #2d3060; border-radius: 6px; color: #c4cae8;
}

/* Responses */
.swagger-ui .responses-inner { background: transparent; }
.swagger-ui .response-col_status { color: #c4cae8; font-weight: 700; font-family: monospace; }
.swagger-ui .response-col_description { color: #8892b0; }
.swagger-ui table.responses-table td { border-color: #1e2040; }
.swagger-ui .response.response_200 .response-col_status,
.swagger-ui .response.response_201 .response-col_status { color: #10b981; }
.swagger-ui .response.response_400 .response-col_status,
.swagger-ui .response.response_404 .response-col_status,
.swagger-ui .response.response_422 .response-col_status { color: #f59e0b; }
.swagger-ui .response.response_500 .response-col_status { color: #ef4444; }

/* Code / curl */
.swagger-ui .microlight {
  background: #070712; color: #e2e8f0; border-radius: 6px;
  padding: 12px 16px; border: 1px solid #1e2040;
}
.swagger-ui .curl { background: #070712 !important; border: 1px solid #1e2040; border-radius: 6px; color: #7dd3fc; }
.swagger-ui .highlight-code pre { background: #070712; }

/* Models */
.swagger-ui section.models { background: #0d0d1f; border: 1px solid #1e2040; border-radius: 8px; margin-top: 20px; }
.swagger-ui section.models h4 { color: #c4cae8; font-size: 14px; }
.swagger-ui .model-box { background: #070712; border: 1px solid #1e2040; border-radius: 6px; }
.swagger-ui .model { color: #c4cae8; background: transparent; }
.swagger-ui .model-title { color: #c4cae8; }
.swagger-ui .prop-type { color: #7dd3fc; }
.swagger-ui .prop-format { color: #a78bfa; }
.swagger-ui .primitive { color: #10b981; }

/* Markdown inside descriptions */
.swagger-ui .markdown h2 { color: #c4cae8; border-bottom: 1px solid #1e2040; padding-bottom: 8px; font-size: 16px; }
.swagger-ui .markdown h3 { color: #a5b4fc; font-size: 14px; }
.swagger-ui .markdown p { color: #8892b0; }
.swagger-ui .markdown table { border-collapse: collapse; width: 100%; margin: 12px 0; }
.swagger-ui .markdown table th { background: #12122a; color: #c4cae8; padding: 8px 12px; border: 1px solid #1e2040; font-size: 12px; }
.swagger-ui .markdown table td { padding: 8px 12px; border: 1px solid #1e2040; color: #8892b0; font-size: 13px; }
.swagger-ui .markdown code { background: #12122a; color: #7dd3fc; border: 1px solid #1e2040; border-radius: 4px; padding: 1px 6px; font-size: 12px; }
.swagger-ui .markdown pre { background: #070712; border: 1px solid #1e2040; border-radius: 6px; padding: 12px 16px; }
.swagger-ui .markdown pre code { background: transparent; border: none; padding: 0; }

/* Scrollbar */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #0a0a18; }
::-webkit-scrollbar-thumb { background: #2d3060; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #4b5690; }
"""


@app.get("/docs/swagger.css", include_in_schema=False)
def swagger_css() -> Response:
    return Response(content=_SWAGGER_CSS, media_type="text/css")


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui() -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LARUN SatTrack — API Docs</title>
  <link rel="icon" href="https://sattrack.larun.space/favicon.svg" type="image/svg+xml" />
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css"
        integrity="sha384-wxLW6kwyHktdDGr6Pv1zgm/VGJh99lfUbzSn6HNHBENZlCN7W602k9VkGdxuFvPn"
        crossorigin="anonymous" />
  <link rel="stylesheet" href="/docs/swagger.css" />
</head>
<body>

  <!-- ── Custom branded header ── -->
  <header class="larun-header">
    <div class="larun-header-inner">
      <div class="larun-brand">
        <svg class="larun-sat-icon" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
          <rect width="32" height="32" rx="6" fill="#0a0a0a"/>
          <rect x="13" y="12" width="6" height="8" rx="1" fill="#6366f1"/>
          <rect x="4"  y="14" width="8" height="4" rx="1" fill="#3b82f6"/>
          <rect x="20" y="14" width="8" height="4" rx="1" fill="#3b82f6"/>
          <line x1="16" y1="8" x2="16" y2="12" stroke="#888" stroke-width="1.5"/>
          <circle cx="16" cy="7" r="2" fill="none" stroke="#888" stroke-width="1.2"/>
        </svg>
        <a class="larun-title" href="https://larun.space">Larun<span class="dot">.</span>Space</a>
        <span class="larun-sep">|</span>
        <span class="larun-badge">SatTrack API</span>
      </div>
      <nav class="larun-nav">
        <div class="larun-status-dot" title="API Online"></div>
        <a href="https://sattrack.larun.space" target="_blank" rel="noopener">Live Tracker ↗</a>
        <a href="/redoc" target="_blank" rel="noopener">ReDoc ↗</a>
        <span class="larun-version-pill">v2.0.0</span>
      </nav>
    </div>
  </header>

  <div id="swagger-ui"></div>

  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"
          integrity="sha384-wmyclcVGX/WhUkdkATwhaK1X1JtiNrr2EoYJ+diV3vj4v6OC5yCeSu+yW13SYJep"
          crossorigin="anonymous"></script>
  <script>
    window.onload = () => {
      SwaggerUIBundle({
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        persistAuthorization: true,
        displayOperationId: false,
        defaultModelsExpandDepth: 0,
        defaultModelExpandDepth: 2,
        docExpansion: "list",
        filter: true,
        tryItOutEnabled: false,
        syntaxHighlight: { activated: true, theme: "monokai" },
        layout: "BaseLayout",
        showExtensions: false,
        showCommonExtensions: false,
      });
    };
  </script>

</body>
</html>"""
    return HTMLResponse(html)


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
