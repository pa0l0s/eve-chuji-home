# Track Projects + Admin Name Autocomplete — Design Spec

**Date:** 2026-05-28
**Status:** Approved

## Goal

Let an admin mark a corp project as **Track**, mirroring the existing **Special**
and **Fixed Price** toggles. Track projects are pinned to the top of the
Projects-page sort order (priority 1, already wired through `sort_priority` and
the purple "Track" badge). Today the set is hard-coded in `main.py`; this
moves it to the `project_settings` table so admins can edit it from the UI.

Second feature: when the admin types a project name into the "Add project"
input on the Project Settings panel, suggest matching names from the real EVE
corporation projects.

## Context

- `project_settings` (SQLite) currently has `project_name`, `price_excluded`,
  `special`, `updated_at`. Admin UI toggles `price_excluded` and `special` per
  row via `POST /api/admin/projects/settings`.
- `main.py:778` defines `PROJECT_TRACKING_NAMES = {"Remote Boost Shield",
  "Armor Remote Repair"}`. `_project_priority(p)` returns `1` for those names,
  and `projectPriorityBadge(1)` renders the purple "Track" badge on the card.
- `/api/projects` is where excluded/special are merged into the project list;
  the same place will load tracked names.
- `static/index.html`'s admin panel renders the project-settings table at
  `renderAdminProjects` (line 2716). The name input is a plain `<input>` with
  no suggestions.
- ESI corp projects are fetched in `esi.py:317`
  (`get_corp_projects(corporation_id, access_token)`), used by `/api/projects`.

## Components

### 1. Data model — `track` flag

Add a `track INTEGER DEFAULT 0` column to `project_settings`. Because the
table already exists in production, `init_db()` runs an idempotent migration
matching the `special` pattern (lines 95–100):

```python
if "track" not in _cols:
    await db.execute(
        "ALTER TABLE project_settings ADD COLUMN track INTEGER DEFAULT 0")
```

Seed the two legacy names with `track=1` exactly once over the lifetime of
each DB. Two seed paths, because fresh vs. pre-existing DBs traverse
different code:

- **Pre-existing prod DB** (table exists, `track` column missing): inside
  the `if "track" not in _cols:` block (right after the `ALTER TABLE
  ... ADD COLUMN track`), UPSERT both names with `track=1`. This block runs
  exactly once — on the boot that adds the column.
- **Fresh DB** (table just created): the `track` column exists from
  `CREATE TABLE`, so the migration block doesn't fire. Instead, extend the
  existing empty-table seed (`if row[0] == 0`) — which already seeds
  NPC-priced names with `price_excluded=1` — to also `INSERT` both legacy
  Track names with `track=1`. The empty-table seed already correctly
  gates on "no admin edits yet" (the count check).

Gating is critical: an unconditional seed on every startup would re-enable
Track for those names after an admin had disabled them. With these two gates
the legacy names are seeded at-most-once per DB, and admin edits stick across
restarts.

New `db.py` accessors (mirror `list_special_projects` / `set_project_special`
at lines 288–305):

- `list_tracked_projects() -> set[str]`
- `set_project_track(name: str, track: bool)`
- `list_project_settings()` SELECT extended to include `track`.

### 2. Admin — toggle

`POST /api/admin/projects/settings` (body now `{name, excluded?, special?,
track?}`): applies whichever of the three keys is present, so each toggle
posts independently. `name` stays required.

```python
if "track" in body:
    await database.set_project_track(name, bool(body["track"]))
```

The admin Project Settings panel in `static/index.html` gains a **"Track"**
column with a checkbox per project row, next to "Special". A `${list.filter(p
=> p.track).length} track` count badge is added next to the existing "fixed
price" and "special" badges. The descriptive `<p>` is extended:

> **Track** pins a project to the top of the Projects page sort order.

New JS function `adminToggleProjectTrack(name, track)` mirrors
`adminToggleProjectSpecial` exactly — POSTs `{name, track}`.

### 3. `/api/projects` enrichment

Replace the constant `PROJECT_TRACKING_NAMES` with a DB load.
`_project_priority` takes an extra parameter:

```python
def _project_priority(p: dict, tracked_names: set[str]) -> int:
    name = p.get("name") or ""
    warn = p.get("warn_level")
    if name in tracked_names:
        return 1
    if warn == "red":
        return 2
    ...
```

In `/api/projects` (line 678), load `tracked_names = await
database.list_tracked_projects()` alongside `excluded_names` and
`special_names`, then pass it into `_project_priority`. No extra field on
the project dict is needed — the existing `sort_priority` already carries the
information the frontend reads (`projectPriorityBadge(p.sort_priority)`).

### 4. Admin — name autocomplete

New endpoint `GET /api/admin/projects/available` (admin-only via
`require_admin`). Internally:

- Resolve the admin's access token (same way `/api/projects` does).
- Call `get_corp_projects(corporation_id, access_token)` (same source as the
  public projects list — covers active and closed).
- Return `{"names": [sorted, deduped list of project names]}`. On ESI failure,
  return `{"names": [], "error": "<message>"}` with HTTP 200 — the autocomplete
  is best-effort and must never break the admin panel.

Frontend changes inside `renderAdminProjects` (line 2736):

- Add a `<datalist id="admin-proj-names">` next to the existing
  `<input id="admin-proj-add">`.
- Add `list="admin-proj-names"` on the input.
- During `loadAdminProjects`, also fetch `/api/admin/projects/available` and
  populate the `<datalist>` with one `<option value="...">` per returned name.
- Fetch failure → leave the `<datalist>` empty; the input still works as a
  plain text field.

`<datalist>` is the right primitive here: no library, no z-index/positioning
work, browser-native filtering as the user types, and degrades gracefully.

### 5. Card rendering

No new code in `projectCard` — the "Track" purple badge is already produced
by `projectPriorityBadge(1)`, which now fires for DB-flagged names instead of
the hard-coded set. The visual outcome on the Projects page is unchanged for
existing track names; admins can now add or remove the flag.

## Data Flow

```
admin toggles "Track" -> POST /api/admin/projects/settings -> project_settings.track
GET /api/projects
  -> projects + list_tracked_projects()
  -> _project_priority(p, tracked_names) -> p.sort_priority
projects page client sort:
  priority => p.sort_priority (1 for track ⇒ first)
  badge    => projectPriorityBadge(p.sort_priority) ⇒ purple "Track"

admin opens Project Settings panel
  GET /api/admin/projects/settings    -> existing rows incl. track
  GET /api/admin/projects/available   -> ESI corp project names (best-effort)
    -> <datalist id="admin-proj-names"> populated
  user types in #admin-proj-add -> browser suggests names from <datalist>
```

## Error Handling

- `ALTER TABLE` on an already-migrated DB → guarded by the `"track" not in
  _cols` check (same shape as the `special` migration).
- ESI failure when fetching available project names → endpoint returns
  `{names: [], error}`; the admin panel renders with an empty datalist and
  the input works as plain text.
- Submitting an arbitrary name (not in the datalist) is still allowed —
  `<datalist>` is suggest-only, not validate-only. Admin can add names that
  don't yet appear in corp projects, same as today.
- A project that has both `track=1` and `special=1` → both flags apply
  independently. Priority 1 (track) wins for sorting; the special block on
  the card and modal still renders.

## Testing

- `db.py`:
  - `track` column migration is idempotent (running `init_db()` twice doesn't
    fail).
  - `set_project_track` + `list_tracked_projects` round-trip.
  - `list_project_settings()` includes the `track` field.
  - Fresh DB after `init_db()` has the two legacy names with `track=1`
    (empty-table seed path).
  - Pre-existing DB receiving the column also gets the seed (migration path),
    without clobbering existing `special` / `price_excluded` flags.
  - After the column exists and the DB is non-empty, re-running `init_db()`
    does NOT re-seed — an admin who sets `track=0` on a legacy name and
    restarts the app stays at `track=0`.
- `main.py`:
  - `POST /api/admin/projects/settings` with `{name, track}` flips the flag
    (admin-only — 401/403 without admin).
  - `/api/projects` returns `sort_priority=1` for names present in
    `project_settings` with `track=1` (and the hard-coded constant is gone).
  - `GET /api/admin/projects/available` returns a sorted name list; admin-only.
  - `GET /api/admin/projects/available` on ESI failure returns 200 with
    `names: []` and an `error` field.
- Frontend: no JS test harness (project convention). Verified manually: open
  the admin Project Settings panel, confirm the new Track column + count
  badge appear, confirm typing in "Project name" surfaces existing project
  names as suggestions, confirm toggling Track on a project pushes it to the
  top of the Projects page sort.

## Scope

In scope: the `track` flag (DB column + migration + seed), `db.py` accessors,
admin POST extension, the new admin autocomplete endpoint, removal of the
`PROJECT_TRACKING_NAMES` constant and its `_project_priority` integration, the
admin UI Track column, the autocomplete `<datalist>`.

Out of scope:
- The pre-existing badge-mapping issue at `index.html:2224` (`prio === 3`
  shows "Loot" but `_project_priority` returns 4 for loot).
- Making `PROJECT_LOOT_NAMES` DB-backed — same shape as TRACK but not asked
  for.
- Multi-select / fuzzy autocomplete UI — `<datalist>` is intentional.
- Auto-removing the `track` flag for projects that no longer exist in corp
  ESI data.
