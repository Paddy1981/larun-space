# SatTrack ML Local Database

Local PostgreSQL database for ML model training — purpose-built for four tasks:
1. **TLE element forecasting** — predict next orbital elements
2. **Maneuver detection** — classify thruster burns vs natural perturbation
3. **Re-entry / orbital decay prediction**
4. **Conjunction probability improvement**

Supabase is kept for live tracking. This local DB is for training workloads: no API rate
limits, full SQL analytics, pre-computed feature vectors.

---

## Quick Start

### 1. Configure credentials

```bash
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, PGADMIN_PASSWORD, SUPABASE_SERVICE_KEY
```

### 2. Start PostgreSQL + pgAdmin

```bash
docker compose up -d
# PostgreSQL on localhost:5433
# pgAdmin on http://localhost:5050  (login with PGADMIN_EMAIL / PGADMIN_PASSWORD)
```

Schema is auto-applied on first start via `schema_ml.sql`.

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Sync recent data from Supabase

```bash
# Incremental sync (last 60 days)
python sync_from_supabase.py --since 2026-01-01

# Full mirror (slow for large tle_history)
python sync_from_supabase.py --full
```

### 5. Compute ML features

```bash
python feature_engineer.py --all
```

### 6. Detect maneuvers

```bash
python maneuver_detector.py --all
```

### 7. Verify

```bash
psql -U sattrack_ml -h localhost -p 5433 -d sattrack_ml -c \
  "SELECT count(*) FROM tle_features WHERE is_maneuver = TRUE;"
```

---

## Historical Backfill

### ETH Zurich (7.97M TLEs, no login required)

```bash
# All active satellites, 1 year back
python backfill_local.py --source ethz --all-active --days-back 365

# Specific NORAD IDs
python backfill_local.py --source ethz --norad-ids 25544 20580 --days-back 730
```

### Space-Track GP_History (138M TLEs, free account required)

Register at https://www.space-track.org/auth/createAccount then:

```bash
python backfill_local.py --source spacetrack \
  --norad-ids 25544 20580 44713 \
  --st-user your@email.com --st-pass yourpassword \
  --days-back 1825
```

### CelesTrak Archives (weather/NOAA/ISS history)

```bash
python backfill_local.py --source celestrak_archives
```

### Ingest already-downloaded files

```bash
python backfill_local.py --ingest-only --output ./tle_training_data
```

---

## Schema Overview

| Table             | Description                                         |
|-------------------|-----------------------------------------------------|
| `satellites`      | Master catalog (mirrors Supabase)                   |
| `tle_history`     | Every TLE record (mirrors Supabase + backfill)      |
| `space_weather`   | F10.7 / Kp time series (mirrors Supabase)           |
| `tle_features`    | Pre-computed ML feature vectors (one row per TLE)   |
| `maneuver_events` | Detected maneuver events with Δv proxy              |
| `decay_predictions` | Re-entry prediction log (populated by ML model)   |

### Key `tle_features` columns

| Column            | Type    | Description                          |
|-------------------|---------|--------------------------------------|
| `sma_km`          | float   | Semi-major axis (km)                 |
| `perigee_km`      | float   | Perigee altitude above WGS84 (km)    |
| `apogee_km`       | float   | Apogee altitude (km)                 |
| `period_min`      | float   | Orbital period (minutes)             |
| `dt_hours`        | float   | Time gap from previous TLE           |
| `d_inclination`   | float   | Inclination change rate (deg/hr)     |
| `d_mean_motion`   | float   | Mean motion change rate (rev/day/hr) |
| `kp_at_epoch`     | float   | Kp index at TLE epoch (nearest ±1h)  |
| `f107_at_epoch`   | float   | F10.7 flux at epoch                  |
| `is_maneuver`     | boolean | Maneuver label (NULL = unlabeled)    |

---

## Data Volume Estimates

| Source                | Records     | Storage est. |
|-----------------------|-------------|--------------|
| Current Supabase sync | ~2–5M TLEs  | ~2 GB        |
| ETH Zurich backfill   | 7.97M TLEs  | ~4 GB        |
| Space-Track (1 yr)    | ~50M TLEs   | ~25 GB       |
| `tle_features`        | same as TLEs| ~3 GB extra  |
| `space_weather`       | ~100K rows  | ~20 MB       |

**Recommended starting point:** Sync Supabase + ETH Zurich → ~10M records, then add
Space-Track incrementally by NORAD group.

---

## Directory Structure

```
ml-db/
├── docker-compose.yml      # PostgreSQL 16 (port 5433) + pgAdmin (port 5050)
├── .env.example            # Copy to .env and fill in credentials
├── schema_ml.sql           # Full schema — auto-applied on first docker compose up
├── sync_from_supabase.py   # Mirror Supabase live data → local DB
├── backfill_local.py       # Historical backfill (ETH Zurich, Space-Track, CelesTrak)
├── feature_engineer.py     # Compute tle_features from tle_history
├── maneuver_detector.py    # Rule-based maneuver labeling → maneuver_events
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

---

## Connecting with psql

```bash
psql -U sattrack_ml -h localhost -p 5433 -d sattrack_ml
```

## Useful Queries

```sql
-- Count TLEs per source
SELECT source, count(*) FROM tle_history GROUP BY source ORDER BY count DESC;

-- ISS maneuvers (NORAD 25544)
SELECT detected_epoch, maneuver_type, confidence, delta_v_proxy
FROM maneuver_events WHERE norad_id = 25544 ORDER BY detected_epoch DESC LIMIT 20;

-- Feature distribution for LEO satellites
SELECT
  avg(d_inclination) AS avg_d_inc,
  avg(d_mean_motion) AS avg_d_mm,
  count(*) FILTER (WHERE is_maneuver) AS maneuver_count,
  count(*) AS total
FROM tle_features tf
JOIN satellites s USING (norad_id)
WHERE s.orbit_class = 'LEO';

-- Satellites with most maneuvers
SELECT s.name, s.norad_id, count(*) AS maneuvers
FROM maneuver_events me
JOIN satellites s USING (norad_id)
GROUP BY s.name, s.norad_id
ORDER BY maneuvers DESC LIMIT 20;
```
