# CheatSheet Hub (v1)

Cheatsheets for Cloud, DevOps, SRE and AI Engineering. Register, upload, browse, search, download.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload      # http://localhost:8000

## Run with Docker
    cp .env.example .env               # set SECRET_KEY, COOKIE_SECURE=true behind HTTPS
    docker compose up -d --build

## Production checklist
- Put Caddy/Nginx/ALB in front for HTTPS (then COOKIE_SECURE=true)
- Set a strong SECRET_KEY; back up ./data (SQLite + uploads) or use Postgres via DATABASE_URL
- Health check: GET /healthz
