# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- **Backend**: FastAPI (Python), `main.py` is the entrypoint
- **Frontend**: Single-page `static/index.html` — plain HTML/CSS/JS, no build tool
- **Deployment**: Docker via `docker-compose.yml`

## Dev Commands

```bash
# Install deps
pip install -r requirements.txt

# Run locally
uvicorn main:app --reload --port 8000

# Run tests
pytest

# Run a single test
pytest tests/test_foo.py::test_bar
```

## Architecture

FastAPI serves `static/index.html` as the SPA root via `StaticFiles`. All API routes are prefixed `/api/`. The frontend talks to the backend via `fetch()` with JSON — no framework, no bundler.

Backend modules are split by domain (one `.py` per concern). `main.py` wires them together with FastAPI routers or inline route definitions.

## Design System

Match the dark-theme card style from the reference project (`claude-shelly-plug`):

- Background: `#0f1117`, surface: `#1e2130`, border: `#2d3348`
- Accent (values): `#38bdf8`; muted text: `#475569`; labels: uppercase, `0.75rem`, `#64748b`
- Cards: `border-radius: 12px`, `padding: 1.5rem`, `border: 1px solid #2d3348`
- Grid layout: `repeat(auto-fit, minmax(320px, 1fr))`, `max-width: 1200px`, centered
- Font: `-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`

## Git

- GitHub user: pa0l0s / pawelgr@gmail.com
- Always use SSH remote URLs (`git@github.com:pa0l0s/...`)
- `gh` CLI is not installed — use raw `git` commands only
- GitHub repository git@github.com:pa0l0s/eve-chuji-home.git

## Nas File Deploy
Deploy files to paolo@nasty:/srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage using rsync or scp

## Docker Deploy
Use Proxteiner API:
URL: http://192.168.0.24:9000/ 
API_KEY: ptr_7pN8chEVqg4qTohwXoWbmEuRdzO2kHBK7rGx3ORUo8Y=
Use port 8760 in docker. Local Nginx Proxy Manager will redirect https://chuji.swoojeff.online to 192.168.0.24:8760

## Public URL
https://chuji.swoojeff.online