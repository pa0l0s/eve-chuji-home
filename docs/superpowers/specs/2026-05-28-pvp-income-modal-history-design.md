# PVP Income Modal + All-Time History — Design Spec

**Date:** 2026-05-28
**Status:** Approved

## Goal

Make the **PVP Income (30d)** metric on the Character card clickable. Clicking
opens a modal showing the **all-time** list of matching wallet-journal entries
for the current character, with date / amount / reason columns, pagination, and
a totals row.

ESI keeps only ~30 days of wallet journal, so "all-time" means "from the first
deploy of this feature forward" — every time we fetch the journal, matching
entries are persisted to SQLite (idempotent on the ESI journal id). The modal
reads from that table.

The card-metric label and its 30-day scope are unchanged.

## Context

- The Character card already exposes `m.pvp_income.data` (a single float — the
  30-day sum) via `/api/member`. The metric is rendered in
  `renderCharacterCard` (`static/index.html` around line 3134).
- `_cached_pvp_income` (`main.py`) wraps `get_character_wallet_journal` +
  `_sum_pvp_income` with a 5-minute per-character cache.
- The modal pattern already in use: `openProjectContributors` opens a project
  modal via `fetch /api/projects/{id}/contributors` then renders into a
  shared modal container. Same pattern is reused here for `/api/me/pvp-income`.
- SSO scope `esi-wallet.read_character_wallet.v1` is already granted; no
  scope change.

## Components

### 1. Data model — `pvp_income_entries` table

Add to `db.py`'s `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS pvp_income_entries (
    character_id INTEGER NOT NULL,
    journal_id   INTEGER NOT NULL,
    date         TEXT    NOT NULL,
    amount       REAL    NOT NULL,
    reason       TEXT,
    description  TEXT,
    PRIMARY KEY (character_id, journal_id)
);
CREATE INDEX IF NOT EXISTS idx_pvp_income_char_date
    ON pvp_income_entries (character_id, date DESC);
```

Idempotent via `IF NOT EXISTS`. `(character_id, journal_id)` is the natural
PK — ESI journal IDs are unique per character. The index covers the modal's
"newest-first paged scan" query.

`description` is persisted for diagnostics but not surfaced in the modal —
keeps the table self-contained should we want it later.

### 2. `db.py` accessors

```python
async def upsert_pvp_entries_batch(character_id: int, entries: list[dict]) -> None:
    """Bulk INSERT ... ON CONFLICT DO NOTHING for matching journal entries.
    Each entry dict carries: id (-> journal_id), date, amount, reason, description."""

async def list_pvp_entries_page(
    character_id: int, limit: int, offset: int
) -> tuple[list[dict], int]:
    """Returns (rows_newest_first, total_count). Each row is {date, amount, reason}.
    Total count comes from a SELECT COUNT(*) on the same character."""

async def sum_pvp_entries(character_id: int) -> float:
    """SELECT COALESCE(SUM(amount), 0) FROM pvp_income_entries WHERE character_id = ?"""
```

`upsert_pvp_entries_batch` short-circuits on an empty list (no DB connection
opened). `ON CONFLICT DO NOTHING` means a re-seen entry never overwrites
existing data — the first-write wins (relevant if ESI ever reshapes a row;
we keep the canonical version we first saw).

### 3. PVP-entry predicate refactor

Extract the predicate from the existing `_sum_pvp_income` in `main.py` into
a reusable helper:

```python
def _is_pvp_entry(e: dict) -> bool:
    return (
        e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )


def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(e.get("amount", 0) for e in journal if _is_pvp_entry(e))
```

Behaviour of `_sum_pvp_income` is unchanged; the predicate just becomes
named and shared.

### 4. Persistence inside `_cached_pvp_income`

Extend the existing cache-miss path (no signature change, no cache-key change):

```python
async def _cached_pvp_income(character_id: int, access_token: str) -> float:
    now = time.time()
    hit = _pvp_income_cache.get(character_id)
    if hit and (now - hit[0]) < _PVP_INCOME_TTL:
        return hit[1]
    journal = await get_character_wallet_journal(character_id, access_token)
    matches = [e for e in journal if _is_pvp_entry(e)]
    if matches:
        await database.upsert_pvp_entries_batch(character_id, matches)
    amount = sum(e.get("amount", 0) for e in matches)
    _pvp_income_cache[character_id] = (now, amount)
    return amount
```

Persistence runs at most once per 5 minutes per character (only on cache miss).
The card metric's contract (30-day sum) is unchanged — the persistence step
is purely a side effect for the all-time table.

### 5. New endpoint — `GET /api/me/pvp-income`

```python
@app.get("/api/me/pvp-income")
async def my_pvp_income(page: int = 1, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    page = max(1, page)
    page_size = 20
    entries, total = await database.list_pvp_entries_page(
        character_id, limit=page_size, offset=(page - 1) * page_size
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    total_amount = await database.sum_pvp_entries(character_id)
    return {
        "entries": entries,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total,
        "total_amount": total_amount,
    }
```

Auth via the standard session cookie. `page_size` is fixed at 20 — keeps the
modal a comfortable height; tunable later via a constant if needed.

`total_amount` is the all-time sum across every persisted entry for the
character — it is **independent of pagination** and is the source for the
modal's totals row.

### 6. Frontend — modal

Card-side change in `renderCharacterCard`:

```html
<div class="metric" style="cursor:pointer" onclick="openPVPIncomeModal()"
     title="Click for full all-time history">
  <div class="label">PVP Income (30d)</div>
  ...
</div>
```

Only the "happy path" cell becomes clickable. The error cell (`pvpErr`) stays
non-clickable — clicking a "–" placeholder shouldn't open an empty modal.

New JS — modeled after `openProjectContributors`:

```javascript
async function openPVPIncomeModal(page = 1) {
  // 1. Show modal skeleton.
  // 2. fetch(`/api/me/pvp-income?page=${page}`).
  // 3. Render table + totals row + pagination buttons.
}
```

Modal contents:
- Header: `PVP Income — all-time history`.
- Table: 3 columns (Date, Amount, Reason). Date rendered via
  `new Date(e.date).toLocaleString()`, amount via `fmtISKLong`, reason via
  `escHtml(e.reason || '–')`.
- Totals row beneath the table (sticky footer style, same `.muted` colour as
  the other footer captions): `Total over ${total_count} entries:
  ${fmtISKLong(total_amount)} ISK`.
- Pagination strip: Prev / Next buttons + `Page ${page} of ${total_pages}`.
  Prev disabled on page 1; Next disabled on the last page.
- Empty state (when `total_count === 0`): the table renders one row
  `No PVP income recorded yet.`; totals row reads
  `Total over 0 entries: 0 ISK`.
- Error state: replace table body with `Couldn't load PVP income — try again.`,
  leave totals row at its previous value or `–` if never loaded.
- Close on click-outside / Esc / explicit close button (existing modal
  close infrastructure).

## Data Flow

```
GET /api/member
  -> _cached_pvp_income (cache miss path):
       get_character_wallet_journal -> filter _is_pvp_entry
       upsert_pvp_entries_batch     -> writes new rows (ignores dupes)
       sum 30d matches              -> cached, returned for the card metric

User clicks card "PVP Income (30d)"
  -> openPVPIncomeModal(page=1)
  -> GET /api/me/pvp-income?page=1
       list_pvp_entries_page(cid, limit=20, offset=0) -> rows + total_count
       sum_pvp_entries(cid)                            -> total_amount
  -> modal renders rows + totals + pager
  -> Next/Prev -> openPVPIncomeModal(page=N+/-1) -> same flow with new offset
```

## Error Handling

- ESI journal fetch fails → `_safe` already swallows it inside `/api/member`;
  PVP card cell shows `–`; no DB write happens; modal can still be opened
  manually but the table just shows what's already in the DB.
- DB write fails inside `_cached_pvp_income` → the exception propagates;
  `_safe` in `/api/member` catches it and the card cell shows `–`. We don't
  attempt to "succeed partially" with persistence broken — better to surface
  the issue.
- Endpoint fetch fails on the frontend → modal shows the error message; the
  table doesn't get cleared if a previous page had rendered.
- Negative or out-of-range `page` query param → clamped to `>= 1`; an
  out-of-range page returns an empty `entries` list with the same
  `total_count` / `total_amount` (frontend disables Next when on/past the
  last page).

## Testing

- `db.py`:
  - `init_db()` is idempotent for the new table + index (running twice is a
    no-op).
  - `upsert_pvp_entries_batch` round-trips an empty list (no-op) and a
    populated list (rows visible via the listing accessor).
  - Re-running `upsert_pvp_entries_batch` with the same `journal_id` does NOT
    overwrite the existing row (ON CONFLICT DO NOTHING).
  - `list_pvp_entries_page` orders by date DESC, paginates correctly,
    returns the right total_count.
  - `sum_pvp_entries` returns 0 for an empty character, returns the correct
    sum after upsert.
- `main.py`:
  - `_cached_pvp_income` on cache miss calls `upsert_pvp_entries_batch` with
    only matching entries (other ref_type / description rows are filtered
    out).
  - `_cached_pvp_income` on cache hit does NOT fetch the journal and does
    NOT call the upsert (respx mock with no journal route registered will
    raise if it tried).
  - `GET /api/me/pvp-income` requires auth (401 without session).
  - `GET /api/me/pvp-income?page=1` returns entries with the right shape
    `{date, amount, reason}` and correct `total_count` / `total_pages` /
    `total_amount`.
  - `GET /api/me/pvp-income?page=2` returns the next page (offset 20).
  - `GET /api/me/pvp-income?page=999` returns `entries: []` with
    `total_pages` and `total_amount` unchanged from the actual data.
- Frontend: no JS test harness. Verified manually:
  - Clicking the PVP Income cell opens the modal.
  - Modal shows newest-first entries with Date / Amount / Reason.
  - Totals row reads `Total over N entries: X ISK` and stays the same
    across pagination clicks.
  - Empty state and error state render as specified.
  - Close via click-outside / Esc / X button.

## Scope

In scope: the new table + accessors, the predicate refactor + persistence in
`_cached_pvp_income`, the new endpoint, the modal + clickable metric.

Out of scope:
- Showing other characters' entries (this endpoint is "me" only).
- Filtering / searching / sorting inside the modal.
- Backfilling entries older than 30 days at deploy time (impossible — ESI
  doesn't keep them).
- CSV export.
- Deleting or editing persisted entries.
- A separate "PVP Income (all-time)" card metric (the card stays at 30-day
  per the brainstorming choice).
- Per-character cache invalidation on demand (cache flips every 5 minutes
  naturally).
