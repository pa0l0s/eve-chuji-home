# Project Started Date on Card — Design Spec

**Date:** 2026-05-28
**Status:** Approved

## Goal

Show the project's start date on every project card, on the same line as the
existing "Last modified" caption — start on the left, last modified on the
right.

The ESI listing endpoint `/corporations/{corp}/projects` does NOT include a
created/start date; only `id, name, state, last_modified, progress, reward`.
The created datetime lives on the **detail** endpoint
`/corporations/{corp}/projects/{project_id}` as `details.created`. So this
spec adds a per-project detail fetch behind a permanent SQLite cache — the
`created` value is immutable per project, so each project is fetched once and
reused forever.

## Context

- `/api/projects` (main.py:641) calls `get_corp_projects(CORP_ID, access_token)`
  (`esi.py:307`) which hits the listing endpoint. The function returns a list
  of dicts matching the ESI `CorporationsProjectsDetailProject` schema.
- The project card in `static/index.html` (line 1983–1985) currently renders
  `Last modified: <date>` as a single line beneath the card body.
- The frontend reads `p.last_modified` and renders via
  `new Date(...).toLocaleDateString()` — locale-default formatting. Matching
  this style keeps the new "Started" caption consistent.
- ESI's project detail endpoint uses the same `X-Compatibility-Date:
  2026-01-01` header as the listing endpoint.

## Components

### 1. Data model — `project_created_cache`

Add a new table in `db.py`'s `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS project_created_cache (
    project_id TEXT PRIMARY KEY,
    created    TEXT NOT NULL,
    cached_at  REAL NOT NULL
)
```

`project_id` is the ESI UUID. `created` is the ISO datetime string as returned
by ESI (e.g. `"2025-06-01T00:00:00Z"`). `cached_at` is the unix epoch when we
first wrote the row — useful only for diagnostics; the value itself is
immutable so we never check it for invalidation.

Idempotent: `CREATE TABLE IF NOT EXISTS` — no migration logic needed for
pre-existing prod DBs (the table simply gets added on next boot).

### 2. `db.py` accessors

Two new functions, mirroring the existing cache patterns (e.g.
`upsert_janice_cache`, `get_janice_cache`):

```python
async def get_cached_project_created_map() -> dict[str, str]:
    """Returns {project_id: created} for every row in project_created_cache."""

async def cache_project_created_batch(items: list[tuple[str, str]]) -> None:
    """Upsert (project_id, created) pairs; cached_at set to time.time()."""
```

Batch APIs over per-row helpers because `/api/projects` always operates on a
set of N projects at a time.

### 3. `esi.py` — detail fetch

Add `get_corp_project_detail` next to `get_corp_projects` (line 307):

```python
async def get_corp_project_detail(
    corporation_id: int, project_id: str, access_token: str
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://esi.evetech.net/corporations/{corporation_id}/projects/{project_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()
        return r.json()
```

No pagination (single project).

### 4. `/api/projects` enrichment

After the existing market enrichment and before the `excluded_names` block
(around `main.py:678`), add:

```python
created_map = await database.get_cached_project_created_map()
missing = [p["id"] for p in active + closed if p.get("id") and p["id"] not in created_map]
if missing:
    details = await asyncio.gather(*[
        _safe(get_corp_project_detail(CORP_ID, pid, access_token)) for pid in missing
    ])
    new_rows = []
    for pid, (detail, _err) in zip(missing, details):
        if detail:
            c = (detail.get("details") or {}).get("created")
            if c:
                created_map[pid] = c
                new_rows.append((pid, c))
    if new_rows:
        await database.cache_project_created_batch(new_rows)
for p in active + closed:
    pid = p.get("id")
    if pid and pid in created_map:
        p["created"] = created_map[pid]
```

Best-effort throughout: each detail fetch is wrapped with `_safe` (the
existing helper at `main.py:_safe`), so a single failed detail just leaves
that project without a `p["created"]`. The projects list still returns 200
with all the other enrichment intact.

### 5. Frontend — project card

In `static/index.html`, change the existing block (around line 1983–1985):

```html
<div style="margin-top:0.75rem;font-size:0.7rem;color:#475569">
  Last modified: ${new Date(p.last_modified).toLocaleDateString()}
</div>
```

to:

```html
<div style="margin-top:0.75rem;font-size:0.7rem;color:#475569;display:flex;justify-content:space-between;gap:0.5rem">
  <span>${p.created ? 'Started: ' + new Date(p.created).toLocaleDateString() : ''}</span>
  <span>Last modified: ${new Date(p.last_modified).toLocaleDateString()}</span>
</div>
```

`justify-content: space-between` keeps "Last modified" pinned to the right
even when the left span is empty (which happens on the first page-load before
the cache is filled, or for any project whose detail fetch failed). Date-only
output via `.toLocaleDateString()` matches the existing caption style.

## Data Flow

```
GET /api/projects
  -> listing: get_corp_projects(...) -> raw[]
  -> existing market enrichment, etc.
  -> created_map = get_cached_project_created_map()
  -> missing = [p.id for p in projects if p.id not in created_map]
  -> for each missing pid concurrently: get_corp_project_detail() (best-effort)
       -> on success: created_map[pid] = detail.details.created
  -> cache_project_created_batch(new_rows) (single write)
  -> for each project: p["created"] = created_map.get(p.id)
project card render:
  flex row, "Started: <date>" on left (or empty), "Last modified: <date>" on right
```

## Error Handling

- Detail-fetch failure for a project (HTTP error or missing `details.created`)
  → no `p["created"]` written, no DB row. Card omits the "Started" caption.
  On the next `/api/projects` call we retry — the project is still "missing"
  from the cache.
- Cache write failure → enrichment still attaches `p["created"]` to the
  response, but the next request re-fetches detail. Self-healing.
- New project appears in the corp → first `/api/projects` after that fetches
  its detail once, caches `created`, and serves the value forever after.

## Testing

- `db.py`:
  - `init_db()` is idempotent for the new table.
  - `cache_project_created_batch` + `get_cached_project_created_map`
    round-trip; an upsert overwrites `created` and bumps `cached_at`.
- `main.py`:
  - First `/api/projects` call with no cache: respx mocks the listing AND the
    per-project detail endpoint. Response includes `p["created"]` and the DB
    row exists afterward.
  - Second `/api/projects` call with the cache populated: respx mocks ONLY
    the listing (no detail mock registered). `respx.mock` will raise if any
    unmocked request fires, so the test inherently asserts "no detail fetch
    happens for already-cached projects". `p["created"]` is still set.
  - Detail-fetch failure: respx returns 502 for the detail endpoint. The
    response is still 200, the project still appears, `p["created"]` is
    absent.
- Frontend: no JS test harness (project convention). Verified manually:
  card shows "Started: <date>" on the left and "Last modified: <date>" on the
  right. For a project with no cached `created`, only "Last modified" appears
  on the right (left is empty).

## Scope

In scope: the `project_created_cache` table + accessors,
`get_corp_project_detail`, `/api/projects` enrichment, the card layout
change.

Out of scope:
- ESI `details.expires` and `details.finished` (only `created` was asked for).
- Time-component display (date-only, matches "Last modified").
- Cache invalidation (`created` is immutable).
- Backfilling `created` for projects that no longer appear in the listing —
  if a project disappears, its cache row stays harmlessly orphaned.
- Pre-fetching detail for every project at deploy time (the cache fills
  lazily on first `/api/projects` call after deploy).
