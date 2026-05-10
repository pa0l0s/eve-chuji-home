# Design Spec: Grupa Operacyjna ZLY CHUJI Corporation Portal

**Date:** 2026-05-10  
**Status:** Approved  
**Corporation:** Grupa Operacyjna ZLY CHUJI (ID: 98340844)  
**Public URL:** https://chuji.swoojeff.online

---

## 1. Overview

A members-only corporation portal for the EVE Online corporation "Grupa Operacyjna ZLY CHUJI". Authentication is required to access any content. Two views are available post-login: a Corporation Projects Dashboard and a Member Profile page. The entire frontend is a single `static/index.html` file; all data comes from a FastAPI backend via `fetch()`.

---

## 2. Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.11 |
| Frontend | Single `static/index.html` — plain HTML/CSS/JS, no build tool |
| Database | SQLite (`./data/chuji.db`) |
| Session | Signed cookies via `itsdangerous` |
| Docker | `tiangolo/uvicorn-gunicorn-fastapi:python3.11` |
| HTTP client | `httpx` (async) |

**Dependencies (`requirements.txt`):** `httpx`, `itsdangerous`, `aiosqlite`, `python-dotenv`

---

## 3. Environment Variables (`.env`)

```
EVE_CLIENT_ID=bed583dd2f5e4a01b189d54ead038418
EVE_CLIENT_SECRET=<secret>
EVE_CALLBACK_URL=https://chuji.swoojeff.online/api/auth/callback
SECRET_KEY=<generate with openssl rand -hex 32>
CORP_ID=98340844
DATABASE_URL=sqlite:///./data/chuji.db
JANICE_LINK_ID=QcoH7M
```

---

## 4. Architecture

```
Browser
  └─ GET /  →  static/index.html  (always served by FastAPI)
               JS checks GET /api/auth/me on load
               ├─ 401  →  show Login view
               └─ 200  →  show Projects view (default) or Member view

FastAPI
  ├─ GET /                    → serves static/index.html
  ├─ GET /api/auth/login      → redirect to EVE SSO
  ├─ GET /api/auth/callback   → exchange code, verify corp, set session cookie
  ├─ GET /api/auth/logout     → clear session, redirect to /
  ├─ GET /api/auth/me         → return character info or 401
  ├─ GET /api/projects        → ESI corp projects + Janice prices (auth required)
  └─ GET /api/member          → ESI wallet + skills (auth required)

SQLite
  ├─ tokens: character_id, character_name, access_token,
  │          refresh_token, expires_at, corporation_id
  └─ janice_cache: item_id, buy_price, cached_at (TTL 1 hour)
```

**File layout:**
```
main.py          — FastAPI app, mounts StaticFiles, wires routers
auth.py          — EVE SSO OAuth flow, session helpers, corp membership check
esi.py           — ESI API client, automatic token refresh
janice.py        — Janice API client, 1-hour price cache in SQLite
db.py            — SQLite init, tokens table CRUD
static/
  index.html     — entire frontend SPA
```

---

## 5. Authentication Flow

1. User hits `/` → JS calls `/api/auth/me` → 401 → Login view shown
2. User clicks "Login with EVE Online" → browser goes to `/api/auth/login`
3. FastAPI builds EVE SSO URL with required scopes → redirect
4. EVE SSO redirects to `/api/auth/callback?code=...&state=...`
5. FastAPI exchanges code for `access_token` + `refresh_token`
6. Fetches `/characters/{id}/` to get `corporation_id`
7. If `corporation_id != 98340844` → return 403 "Not a corp member"
8. Stores tokens in SQLite, sets signed session cookie (`character_id`)
9. Redirects to `/` → JS calls `/api/auth/me` → 200 → Projects view shown

**Token refresh:** Before every ESI API call, check `expires_at`. If expired, use `refresh_token` to obtain a new `access_token` and update the SQLite row. If refresh fails (token revoked), clear session and return 401.

**EVE SSO scopes used for MVP:**
- `publicData`
- `esi-corporations.read_projects.v1`
- `esi-wallet.read_character_wallet.v1`
- `esi-skills.read_skills.v1`
- `esi-characters.read_corporation_roles.v1`

---

## 6. Module: Corporation Projects Dashboard

**Endpoint:** `GET /api/projects`

1. Uses the authenticated user's token to call `GET /corporations/98340844/projects/` — the ESI endpoint requires the character to have a corp role with project visibility; if ESI returns 403, the view shows "Insufficient corporation roles"
2. Filters to active projects only
3. For each project of type `Buyback`:
   - Calls Janice `POST /api/v1/get-pricelist` with item list, market `60003760` (Jita 4-4)
   - Caches result in SQLite for 1 hour
   - Buy price = Janice instant buy × 0.90
4. Returns unified JSON array to frontend

**Frontend card per project:**
- Project name + type badge (Item Delivery / Manual / Buyback)
- Progress bar (`#38bdf8`) showing `delivered / required`
- For Buyback projects: table — Item | Required Qty | Progress % | Corp Buy Price (ISK)

Auto-refreshes every 60 seconds.

---

## 7. Module: Member Profile Page

**Endpoint:** `GET /api/member`

Parallel ESI calls using the authenticated character's token:
- `GET /characters/{id}/` — name, portrait, corporation, security status
- `GET /characters/{id}/wallet/` — ISK balance
- `GET /characters/{id}/skills/` — total SP, skill queue status

**Frontend layout:**
- Character card: EVE portrait (`https://images.evetech.net/characters/{id}/portrait`), name, corporation, security status
- Metric card: Wallet Balance (formatted with ISK separators)
- Metric card: Total Skillpoints (SP, formatted with comma separators)
- Badge: Training Queue — "Training" (green) or "Idle" (muted)

---

## 8. Frontend Views

### Login View
- Centered card, corp name header
- Official EVE SSO login button image
- Links to `/api/auth/login`

### Post-login Shell
- Top nav bar: corp name (left), character portrait + name (right), Logout button
- Nav links: `PROJECTS` | `MEMBER`
- View container swapped by JS based on active nav link

### Design System (shelly-plug tokens)
```css
background:   #0f1117
surface:      #1e2130
deep surface: #252a3d
border:       #2d3348
text:         #e2e8f0
muted text:   #475569
labels:       #64748b, uppercase, 0.75rem
accent:       #38bdf8
card:         border-radius 12px, padding 1.5rem, border 1px solid #2d3348
grid:         repeat(auto-fit, minmax(320px, 1fr)), max-width 1200px
font:         -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif
```

---

## 9. Error Handling

| Scenario | Behaviour |
|---|---|
| Non-corp member authenticates | 403 page: "Not a member of Grupa Operacyjna ZLY CHUJI" |
| ESI 503 / timeout | Inline red error banner on affected view |
| Token refresh fails | Session cleared, redirect to login |
| Janice API unavailable | Project cards shown without buy price column, label "Price unavailable" |
| Character not in corp anymore | Next token refresh returns 403, session cleared |

---

## 10. Docker

**`docker-compose.yml`:**
```yaml
services:
  app:
    image: tiangolo/uvicorn-gunicorn-fastapi:python3.11
    ports:
      - "8760:80"
    volumes:
      - /srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage:/app
      - /srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage/data:/app/data
    env_file:
      - .env
```

App files are served directly from the NAS path — deploy by syncing files there (rsync/scp per CLAUDE.md). SQLite lives at `data/chuji.db` inside that same NAS directory, persisted across container restarts. Port 8760 matches the Nginx Proxy Manager redirect for `https://chuji.swoojeff.online`.

---

## 11. Out of Scope (this iteration)

- Public homepage content (planned for future)
- Role-based access beyond corp membership check
- Corp wallet / corporation-level financials
- Kill feed / zKillboard integration
- Any scope beyond the 5 listed in section 5
