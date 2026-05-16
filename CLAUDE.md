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

## Deploy

**Run `./deploy.sh`** — it does rsync + Portainer stack env sync + redeploy + verify. Details below for when it needs changing.

### Nas File Deploy
Sync the working tree to `paolo@nasty:/srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage`.
SSH as `paolo@nasty` works with `-o BatchMode=yes` (no password). No docker CLI access as `paolo` — inspect containers via the Portainer API instead.
rsync MUST exclude `data/` (the live SQLite DB lives there), plus `.git/ venv/ __pycache__/ *.pyc .pytest_cache/ .claude/ node_modules/`. The app is bind-mounted into the container, so synced files are live immediately. Dry-run first with `-azn --itemize-changes`.

### Docker Deploy
Portainer ("Proxteiner") API: `http://192.168.0.24:9000/api` — header `X-API-Key: ptr_7pN8chEVqg4qTohwXoWbmEuRdzO2kHBK7rGx3ORUo8Y=`
App = Portainer **stack id 110** (`eve-chuji-homepage`), **endpointId 2** (`local`). Port 8760; Nginx Proxy Manager redirects https://chuji.pl → 192.168.0.24:8760.
Restart to pick up new code: `POST /api/stacks/110/stop?endpointId=2` then `POST /api/stacks/110/start?endpointId=2`.
**GOTCHA — env vars:** the Portainer stack carries its own `Env` array that OVERRIDES the NAS `.env`. Editing `.env` + restarting is NOT enough. To change an env var (e.g. `EVE_CALLBACK_URL`): GET `/api/stacks/110` + `/api/stacks/110/file`, modify the `Env` entry, then `PUT /api/stacks/110?endpointId=2` with `{stackFileContent, env, prune:false, pullImage:false}`.
Verify after deploy: container env via `GET /api/endpoints/2/docker/containers/<id>/json`, then `curl -sI https://chuji.pl` expecting HTTP 200.

## Public URL
https://chuji.pl

To change the domain, follow `docs/CHANGING_DOMAIN.md` (covers env values + the Portainer stack-env gotcha).