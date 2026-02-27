# Railway deployment — SatTrack FastAPI backend
# This Dockerfile is at the repo root so Railway (GitHub integration)
# always deploys the FastAPI app from sattrack/ instead of auto-detecting
# the HTML/JS files and deploying a Caddy static site.

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY sattrack/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sattrack/ .

CMD ["python", "main.py"]
