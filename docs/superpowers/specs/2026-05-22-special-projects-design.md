# Special Projects — Design Spec

**Date:** 2026-05-22
**Status:** Approved

## Goal

Let an admin mark certain corp projects (e.g. "Armor Remote Repair", "Remote
Boost Shield") as **special**. Special projects get two extra behaviours:

1. Their **project card** shows the top 3 contributors beneath the progress bar
   — each as a row of `[face icon] [nickname] [zkillboard link]`.
2. Their **details modal** shows the donut pie chart **and** the full ranked
   contributor list below it (normal projects keep just the pie).

## Context

- `project_settings` (SQLite) currently holds `project_name TEXT PRIMARY KEY`,
  `price_excluded INTEGER DEFAULT 0`, `updated_at REAL`. An admin UI panel
  toggles `price_excluded` ("Fixed Price") per project via
  `POST /api/admin/projects/settings`.
- `/api/projects` builds the project list, enriches active projects with a
  `market` block (best-effort), and flags warnings. Project cards
  (`projectCard`) currently carry no contributor data.
- Contributors are fetched on demand by the details modal via
  `GET /api/projects/{id}/contributors` (`esi.get_corp_project_contributors`).
- `renderContributors` shows the donut pie (`renderContributorPie`); the older
  detailed list was removed and is recoverable from git.
- Helpers in `static/index.html`: `characterZkillLink(id)`, portraits via
  `https://images.evetech.net/characters/{id}/portrait`.

## Components

### 1. Data model — `special` flag

Add a `special INTEGER DEFAULT 0` column to `project_settings`. Because the
table already exists in production, `init_db()` runs an idempotent migration:
`ALTER TABLE project_settings ADD COLUMN special INTEGER DEFAULT 0`, ignoring
the "duplicate column name" error on subsequent runs.

New `db.py` accessors:
- `list_special_projects() -> set[str]` — names where `special = 1`.
- `set_project_special(name: str, special: bool)` — upsert, mirroring
  `set_project_price_excluded`.
- `list_project_settings()` — extended to also select `special`.

### 2. Admin — toggle

`POST /api/admin/projects/settings` (body `{name, excluded?, special?}`):
applies whichever of `excluded` / `special` is present in the body, so the
existing "Fixed Price" toggle and the new "Special" toggle each post
independently. `name` stays required.

The admin project-settings panel in `static/index.html` gains a **"Special"**
toggle/checkbox per project row, next to the existing "Fixed Price" control,
posting `{name, special}`.

### 3. Contributors cache

`get_corp_project_contributors` calls ESI per project. Add an in-memory cache
in `main.py` — `_contributors_cache: dict[str, tuple[float, list]]` keyed by
project id, TTL 600 s (contributors change slowly) — and a helper
`_cached_project_contributors(project_id, access_token) -> list`, mirroring the
existing `_cached_jita_prices` / `_cached_market_history` pattern.

`GET /api/projects/{id}/contributors` switches to this helper.

### 4. `/api/projects` enrichment

After loading projects and the `excluded_names`, also load
`special_names = await database.list_special_projects()`. For every project set
`p["special"] = (p.get("name") or "") in special_names`.

For each **special** project (active or closed — a small admin-controlled set),
fetch its contributors via `_cached_project_contributors`, wrapped best-effort
(`_safe`, like the market enrichment — a failure must never break the projects
list). Attach `p["top_contributors"]` = the 3 highest `contributed`
contributors as `[{"id": ..., "name": ...}]`. Non-special projects get no
`top_contributors`.

### 5. Project card

`projectCard` — when `p.special` is true, render a block beneath the progress
bar containing up to 3 rows, one per `top_contributors` entry:
`[28px round face icon] [nickname] [characterZkillLink(id)]`.

- Fewer than 3 contributors → render only those present.
- Empty / missing `top_contributors` → render nothing (no error).
- Non-special cards are unchanged.

### 6. Details modal

`renderContributors(project, contributors)` — always renders the existing pie
(`renderContributorPie`). When `project.special` is true, it also appends the
**full ranked contributor list** below the pie via a `renderContributorList`
function (restored from git history `static/index.html` before the pie commit):
ranked rows of rank · portrait · name · `characterZkillLink` · mini progress
bar · last-contribution date · contributed amount · %.

## Data Flow

```
admin toggles "Special" -> POST /api/admin/projects/settings -> project_settings.special
GET /api/projects
  -> projects + list_special_projects()
  -> p.special set on every project
  -> for special projects: _cached_project_contributors() -> p.top_contributors (best-effort)
project card: p.special -> 3 contributor rows (face/nick/zkill)
click card -> openProjectContributors -> GET /api/projects/{id}/contributors (cached)
  -> renderContributors: pie always; + full list when project.special
```

## Error Handling

- Contributor enrichment for a special project fails → that project simply has
  no `top_contributors`; the card shows no icon rows; `/api/projects` still
  succeeds.
- `ALTER TABLE` migration on an already-migrated DB → the "duplicate column"
  error is caught and ignored.
- A special project with zero contributors → card shows no rows; modal list
  shows the existing "No contributors yet." message.

## Testing

- `db.py`: the `special` column migration is idempotent; `set_project_special`
  + `list_special_projects` round-trip; `list_project_settings` includes
  `special`.
- `main.py`: `POST /api/admin/projects/settings` with `{name, special}` sets
  the flag (admin-only — 401/403 without admin); `/api/projects` marks
  `special` on the right projects; `_cached_project_contributors` serves the
  second call from cache.
- Frontend: no JS test harness (project convention) — verified manually: mark a
  project special in the admin panel, confirm the card shows 3 contributor rows
  and the modal shows pie + full list.

## Scope

In scope: the `special` flag (DB + admin + UI), the contributors cache, the
`/api/projects` enrichment, the card contributor rows, and the modal full-list
for special projects.

Out of scope: contributor icons on non-special cards, configurable row count
(fixed at 3), a dedicated special-projects view, per-contributor stats beyond
what the restored list already shows.
