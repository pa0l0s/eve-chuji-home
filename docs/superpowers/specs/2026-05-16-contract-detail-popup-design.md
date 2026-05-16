# Contract Detail Popup — Design

**Date:** 2026-05-16
**Status:** Approved

## Goal

Clicking the icon of a contract that has items (the `item_exchange` and
`auction` contracts that already show an icon, on the Contracts page) opens a
modal showing:

- Contract meta: title, type, issuer, contract ISK price, status, expiry.
- The **included** items (what you receive) with, per item: name, quantity,
  Jita 4-4 best **buy** price, Jita 4-4 lowest **sell** price, and line totals
  (`unit price × quantity`).
- A summary row: total Jita **buy** value and total Jita **sell** value of the
  contract's included items.
- A large ship `render` image (up to 512px) when the contract's representative
  item is a ship; otherwise the 64px item `icon`.

## Non-Goals

- Requested / "you give" items (excluded items). Only items received are shown.
- The personal `/api/me/contracts` view.
- Courier contracts (no item bundle / no icon).
- Janice or derived pricing — prices are the literal Jita 4-4 order book.

## Prices

"Best buy" = highest buy order at Jita 4-4 (station `60003760`, The Forge
region `10000002`). "Lowest sell" = lowest sell order at the same station.
This mirrors the existing `get_jita_buy_max`. A missing price (no orders / ESI
failure) is `null` and is excluded from the summary totals — never an error.

## Backend

### `esi.py`

- `get_jita_sell_min(type_id) -> float | None`: lowest Jita 4-4 sell order,
  mirroring `get_jita_buy_max` (page 1, filter `location_id == 60003760`,
  `httpx.HTTPError → None`).
- `get_jita_prices(type_id) -> {"buy": float | None, "sell": float | None}`:
  runs `get_jita_buy_max` and `get_jita_sell_min` concurrently.

### `main.py`

- In-memory `_jita_price_cache: dict[int, tuple[float, dict]]` with
  `JITA_PRICE_TTL = 600` (10 min). Prices move often; 10 minutes balances
  freshness against ESI load.
- Pure helper
  `_build_contract_detail(items, names, prices, ship_type_ids) -> dict`:
  - `items`: raw contract items. Filter to `is_included` only.
  - For each included item: `{type_id, name (names.get(type_id) fallback
    "Type #id"), quantity, buy, sell, buy_total, sell_total}` where
    `*_total = price * quantity` (or `None` if that price is `None`).
  - `totals`: `{buy: sum of buy_total ignoring None, sell: same for sell}`.
  - `hero`: `{type_id, is_ship}` where `type_id` is
    `_select_contract_icon_type_id(items, ship_type_ids)` and `is_ship` is
    `type_id in ship_type_ids`.
  - No items → empty `items`, zero totals, `hero.type_id` may be `None`.
  - Contract meta is NOT handled here. The endpoint returns only
    `{items, totals, hero}`; the frontend already holds the contract object
    (title, type, price, issuer, dates, status) from the rendered list and
    supplies meta client-side. This keeps the endpoint focused and avoids a
    redundant corp-contracts refetch.
- New endpoint `GET /api/contracts/{contract_id}/detail`:
  - Auth via the existing session/token path used by `/api/contracts`.
  - Items: reuse the permanent `_contract_items_cache`; on miss call
    `get_corp_contract_items(CORP_ID, contract_id, token)` and cache.
  - Names: `get_type_info` per distinct `type_id` (SQLite-cached),
    concurrently.
  - Prices: per distinct `type_id`, use `_jita_price_cache` when fresh, else
    `get_jita_prices`; fetched concurrently. Best-effort — failures yield
    `null` prices.
  - Returns `{items, totals, hero}` from `_build_contract_detail(...)`. The
    endpoint never raises for enrichment failures; only auth can 4xx.

## Frontend (`static/index.html`)

- The icon cell (`contractIcon`) becomes clickable when `c.icon_type_id` is
  set: `cursor:pointer`, `title="View contract details"`,
  `onclick="openContractDetail(<contract_id>)"`. Applies in both the "Other"
  and "Abyssal / Auction" tables.
- `openContractDetail(id)`:
  1. Look up the contract object by `id` from the in-memory `contractsData`
     (already rendered) for all meta fields.
  2. `showModal(title, '<loading…>')` immediately.
  3. `fetch('/api/contracts/' + id + '/detail')` for items/totals/hero.
  4. On success, render into the modal body:
     - Hero image: `https://images.evetech.net/types/{hero.type_id}/render?size=512`
       when `hero.is_ship`, else `.../icon?size=64`; `onerror` hides it.
     - Meta block (title, type badge, issuer, contract price, status, expiry)
       reusing existing badge/format helpers (`fmtISK`, `statusBadge`,
       `timeLeft`, `typeLabel`).
     - Items table: Name · Qty · Jita Buy · Jita Sell · Buy Total · Sell Total.
       `null` prices render as a muted "–".
     - Footer row: summary `totals.buy` / `totals.sell`.
     - The existing "Open in EVE" button for this contract.
  5. On fetch failure, show an inline error message inside the modal (modal
     stays open; no app-level banner).
- Reuses existing `.modal` / `.modal-backdrop` styles and `closeModal()`.

## Error Handling

- Item-name or price lookup failure for a type → that field is `null`/fallback;
  other rows and the modal are unaffected.
- Contract-items fetch failure → endpoint returns the meta it has with empty
  items and zero totals; frontend still renders the meta and hero.
- Frontend fetch/network failure → error text inside the open modal.
- No new user-facing error banners; the contract list is unaffected.

## Testing (TDD)

- `get_jita_sell_min`: returns lowest 4-4 sell; ignores non-4-4 orders;
  `None` when no orders; `None` on ESI HTTP error (respx).
- `get_jita_prices`: combines buy + sell; partial (`buy` ok, `sell` None).
- `_jita_price_cache` TTL: fresh hit skips ESI; expired entry refetches.
- `_build_contract_detail`:
  - included-only filter (excluded items omitted),
  - per-item line totals and `None` propagation when a price is missing,
  - summary totals ignore `None`,
  - `hero` ship vs non-ship vs no-items,
  - name fallback when unresolved.
- Existing suite continues to pass.
</content>
