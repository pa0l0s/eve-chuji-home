# Contract Item Icons — Design

**Date:** 2026-05-16
**Status:** Approved

## Goal

On the corp **Contracts page**, show an item icon as the first column for
contracts that carry items: `item_exchange` (the "Other" tab) and `auction`
(the "Abyssal / Auction" tab).

Icon selection per contract:

1. If any **included** item's `type_id` is a ship → use that ship's `type_id`
   (first ship found).
2. Else if there is exactly **one** included item → use that item's `type_id`.
3. Else (multiple included items, none a ship) → use the included item with the
   **highest quantity** (ties → first listed).
4. No items, or item fetch failed → no icon (`icon_type_id: null`).

Icon image: `https://images.evetech.net/types/{type_id}/icon` (the `/icon`
endpoint, not the larger 3D `/render`).

"Highest quantity" is a deliberate cheap stand-in for "most valuable" — true
value would require per-item market price lookups, which we do not fetch.

## Non-Goals

- Courier contracts (no item bundle to depict).
- The personal `/api/me/contracts` view. Scope is the corp Contracts page only.
- Per-item market valuation.

## Backend

### Ship-type id set (`esi.py` + `db.py`)

- New `get_ship_type_ids() -> set[int]`: all ship `type_id`s, built from ESI
  category 6: `GET /universe/categories/6/` → `groups`, then
  `GET /universe/groups/{group_id}/` → `types`, unioned.
- Persisted in SQLite. New table:

  ```sql
  CREATE TABLE IF NOT EXISTS ship_types_cache (
      id          INTEGER PRIMARY KEY CHECK (id = 1),
      type_ids    TEXT NOT NULL,   -- JSON array of ints
      fetched_at  REAL NOT NULL    -- epoch seconds
  );
  ```

- On call: if a row exists and `now - fetched_at < 2 days`, return the cached
  set. Otherwise refetch from ESI, overwrite the row, return the fresh set.
  First-ever call populates it. Ship list changes rarely, so a 2-day TTL is
  ample.
- If the refetch fails but a stale row exists, return the stale set (best
  effort — never block contract rendering on this).

### Contract items (`esi.py`)

- New `get_corp_contract_items(corp_id, contract_id, access_token) -> list`:
  `GET /corporations/{corp_id}/contracts/{contract_id}/items/`.
  Each entry includes `type_id`, `quantity`, `is_included`, `is_singleton`.
- Contract contents are immutable once created, so cache per `contract_id`
  **permanently in-memory**:

  ```python
  _contract_items_cache: dict[int, list] = {}
  ```

  After a contract's items are fetched once, it never hits ESI again. (Cache is
  process-lifetime; acceptable — the same pattern as other in-memory caches in
  `main.py`.)

### Enrichment (`/api/contracts` in `main.py`)

- After the existing split into `hauling` / `abyssal` / `others`:
  - Collect contracts from `others` **and** `abyssal` where
    `type in ("item_exchange", "auction")`.
  - For those not already in `_contract_items_cache`, fetch items concurrently
    via `asyncio.gather`, wrapped so individual failures don't abort the batch
    (reuse the existing `_safe` helper pattern).
  - Load the ship-type set once via `get_ship_type_ids()`.
  - For each such contract, compute `icon_type_id` using the selection rules
    above and attach it to the contract dict.
- Enrichment is best-effort: any failure leaves `icon_type_id` absent/null and
  the contract list still renders. Mirrors how location/name enrichment already
  degrades.

## Frontend (`static/index.html`)

- Add a new **first column** to two tables:
  - "Other" table (`pane-others`) — before the Title column.
  - "Abyssal / Auction" table (`pane-abyssal`) — before the Title column.
- Header cell is blank (`<th></th>`), not sortable.
- Cell renders a ~32px image:
  `https://images.evetech.net/types/{c.icon_type_id}/icon`, rounded corners,
  vertically centered, with `onerror` to hide a broken image.
- When `icon_type_id` is null/absent, render a neutral placeholder of the same
  box size (keeps row heights and column alignment consistent).
- Keep rows compact; the icon must not increase row height noticeably.

## Error Handling

- Ship-type fetch failure with stale cache → use stale; with no cache → treat
  set as empty (icon logic degrades to single-item / highest-quantity only).
- Contract-items fetch failure for a contract → that contract gets no icon;
  others unaffected.
- Image 404 at the CDN → `onerror` hides the `<img>`, leaving the placeholder
  spacing.
- None of the above produce a user-facing error banner; the contract list is
  the priority.

## Testing

- `get_ship_type_ids`: fresh fetch populates cache; second call within 2 days
  hits cache (no ESI call); call after >2 days refetches; ESI failure with
  stale row returns stale set.
- Icon selection: ship-present, single-item, multi-item-no-ship
  (highest-quantity, and tie → first), empty items, fetch-failure → null.
- `/api/contracts`: `item_exchange` and `auction` contracts gain
  `icon_type_id`; `courier` contracts do not; enrichment failure still returns
  the full list.
- Existing contract tests continue to pass.
</content>
