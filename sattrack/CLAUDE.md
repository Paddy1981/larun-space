# SatTrack Backend — Claude Code Guide

**Stack:** FastAPI · Python 3.12 · Railway (service: `web`)
**Repo:** `Paddy1981/LARUN-SPACE` (PUBLIC)
**Live URL:** `https://sattrack-production.up.railway.app`
**Supabase:** `mwmbcfcvnkwegrjlauis` (Tokyo) — shared with larun.space

---

## Project Structure

```
sattrack/
├── main.py                  # FastAPI app entry point
├── scheduler.py             # APScheduler background jobs
├── requirements.txt
├── Dockerfile
├── railway.json             # start: python main.py
├── api/
│   ├── routes.py            # Core satellite routes (/v1/satellites, /v1/tle/*)
│   ├── routes_passes.py     # Pass predictions (/v1/passes)
│   ├── routes_conjunctions.py
│   ├── routes_ml.py         # ML routes (/ml/maneuvers, /ml/decay, /ml/density)
│   └── routes_propagate.py  # SGP4 propagation (/v1/propagate)
├── propagator/              # SGP4 propagation logic
├── fetchers/                # TLE + space weather data fetchers
├── passes/                  # Pass prediction logic
├── conjunctions/            # Conjunction detection
├── db/                      # Supabase client
├── quality/                 # Data quality checks
└── ml-db/                   # Local ML PostgreSQL (Docker, port 5433)
    ├── docker-compose.yml
    ├── schema_ml.sql
    ├── models/conjunction_v1.pkl   # XGBoost model (157KB)
    └── push_to_supabase.py         # --all flag pushes enrichment+maneuvers+decay
```

## Key Commands

```bash
# Local dev
uvicorn main:app --reload --port 8000

# Deploy to Railway
railway up --service web --detach

# Railway status check
railway whoami && railway status

# Health check
curl https://sattrack-production.up.railway.app/health

# Local ML DB (Docker)
docker compose -f ml-db/docker-compose.yml up -d

# Push ML data to Supabase
python ml-db/push_to_supabase.py --all
```

## API Routes (19 total)

| Route | Description |
|-------|-------------|
| `GET /health` | Health check |
| `GET /v1/satellites` | All satellites |
| `GET /v1/satellites/{id}/enrichment` | ML enrichment data |
| `GET /v1/tle/{norad_id}` | Current TLE |
| `POST /v1/propagate` | SGP4 propagation |
| `GET /v1/passes` | Pass predictions |
| `GET /v1/conjunctions` | Conjunction alerts |
| `GET /v1/weather/current` | Space weather |
| `GET /ml/maneuvers/*` | Maneuver events |
| `GET /ml/decay/*` | Decay predictions |
| `GET /ml/density` | Atmospheric density |

## Supabase Tables

- `satellites` (68K rows), `tle_history`, `space_weather`, `conjunctions`, `source_health`, `analyses`
- ML tables: `satellite_enrichment`, `maneuver_events`, `decay_predictions`
- ML columns on conjunctions: `risk_score`, `conjunction_risk_label`, `rcs_m2_*`, `ml_conjunction_probability`

## Important Notes

- **psycopg2**: import locally inside functions in `routes_ml.py` (not at module top) — avoids Railway build issues
- **Decimal types**: use `_clean_row()` helper before sending to Supabase
- **Railway**: repo uses `master` branch. Always `railway up` — do NOT rely on GitHub auto-deploy
- **`.railwayignore`**: excludes `ml-db/pgdata/` (was 580MB — keep excluded)
- **pgAdmin local**: `admin@sattrack.dev` / `pgadmin_local` → `sattrack_ml_db:5432`, user `sattrack_ml`
- **TLE cache**: 60-second cache in `propagator.py`

## Commit & Deploy

```bash
# Commit (specific files only — never git add -A)
git add <files>
git commit -m "$(cat <<'EOF'
<summary>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push

# Deploy
railway up --service web --detach
```
