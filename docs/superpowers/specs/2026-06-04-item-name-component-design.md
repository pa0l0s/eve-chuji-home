# Shared Item-Name Component with In-Game Info / Market Icons

**Date:** 2026-06-04
**Status:** Approved (design)

## Goal

Replace the ad-hoc rendering of EVE item/type names across the SPA with one shared
component that renders the item name plus two clickable icons:

- **Info** icon → opens the in-game *Show Info* window for the type via
  `https://esi.evetech.net/ui/openwindow/information`.
- **Market** icon → opens the in-game *Market Details* window for the type via
  `https://esi.evetech.net/ui/openwindow/marketdetails`.

The component is used everywhere a game item/type name is displayed.

## Background / Current State

- Frontend is a single plain-JS SPA, `static/index.html` (~4381 lines). Rendering is
  done with template-literal functions returning HTML strings, injected via
  `innerHTML`. Handlers are wired with inline `onclick`.
- An in-game UI proxy already exists and is the pattern to mirror:
  - `esi.py` → `open_contract_in_client(contract_id, access_token)` POSTs to
    `https://esi.evetech.net/ui/openwindow/contract`.
  - `main.py` → `POST /api/ui/open-contract/{contract_id}`, guarded by
    `get_current_character_id`, mapping ESI 403 → "Missing scope" and other errors → 502.
  - `static/index.html` → `openInEveBtn(contractId)` renders a `btn-ingame` button;
    `openContractInEve(btn, contractId)` does the fetch and toggles `btn-ok`/`btn-fail`
    + `title`, auto-resetting after 2.5s.
- The `esi-ui.open_window.v1` scope is already requested (`auth.py:23`).
- Every item-name render site already has a `type_id` available (in frontend data or in
  the backend payload), so **no backend data-shape changes are required** — only two new
  proxy routes.
- The two icon assets currently live in the repo root and must be moved under `static/`
  to be served by `StaticFiles`.

## Decisions

- **Display:** both icons are **always visible** inline after the item name (user choice),
  in every location including dense tables.
- **Approach:** a shared JS helper function returning an HTML string (matches existing
  codebase style), not a custom element and not `data-`-attribute event delegation.
- **Graceful degrade:** when no `type_id` is available, render the plain (escaped) name
  with no icons rather than broken/non-functional icons.

## Design

### Backend

**`esi.py`** — two new functions mirroring `open_contract_in_client`:

```python
async def open_information_in_client(type_id: int, access_token: str) -> None:
    # POST https://esi.evetech.net/ui/openwindow/information?target_id={type_id}

async def open_market_in_client(type_id: int, access_token: str) -> None:
    # POST https://esi.evetech.net/ui/openwindow/marketdetails?type_id={type_id}
```

Both send `Authorization: Bearer {token}` and `X-Compatibility-Date: 2026-01-01`, and
call `raise_for_status()`.

Note the param names differ between the two ESI endpoints: `information` uses
`target_id`, `marketdetails` uses `type_id`.

**`main.py`** — two new routes, each a copy of the `open_contract` handler shape:

```python
@app.post("/api/ui/open-information/{type_id}")
@app.post("/api/ui/open-market/{type_id}")
```

Each: resolve character via `get_current_character_id(session)`, get a valid token,
call the matching esi function, map `HTTPStatusError` 403 → 403 "Missing scope:
esi-ui.open_window.v1", other ESI errors → 502, return `{"ok": True}`.

### Assets

- `info-icon_no_background.png` → `static/info-icon.png`
- `market-icon.png` → `static/market-icon.png`

(Move via `git mv` so history is preserved.)

### Frontend component (`static/index.html`)

**Renderer:**

```js
// Returns an inline span: escaped name + (if typeId) info & market icon buttons.
function itemName(typeId, name, opts = {}) { ... }
```

- No `typeId` → returns `escHtml(name)` only.
- With `typeId` → `<span class="eve-item"><span class="eve-item-name">{escaped name}</span>{infoBtn}{marketBtn}</span>`.
- Each button: `<button class="eve-item-icon" onclick="event.stopPropagation(); openItemInfo(this, {typeId})" title="..." aria-label="..."><img src="/info-icon.png" alt=""></button>`
  (market button analogous). `stopPropagation` prevents triggering an enclosing
  clickable row/card.

**Handlers** (reuse the `openContractInEve` feedback pattern):

```js
async function openItemInfo(btn, typeId)   { /* POST /api/ui/open-information/{typeId} */ }
async function openItemMarket(btn, typeId) { /* POST /api/ui/open-market/{typeId} */ }
```

Disable button, clear state, fetch; on failure add `btn-fail` + error `title`, on success
add `btn-ok` + "Sent to EVE client"; `setTimeout` reset after 2500ms.

**CSS:** `.eve-item` inline-flex, gap; `.eve-item-icon` ~14px transparent button, muted
opacity by default, full opacity / accent on hover, vertically centered with text;
`btn-ok`/`btn-fail` tints reused.

### Call sites converted to `itemName(...)`

| # | Location | type id source | name source | approx line |
|---|----------|---------------|-------------|-------------|
| 1 | Contract detail — items table | `i.type_id` | `i.name` | ~2015 |
| 2 | Contract detail — hero name | `hero.type_id` | hero name | ~1987 |
| 3 | Contract list (ships tab) — title cell | `c.icon_type_id` | `c.title` | ~1812 |
| 4 | Citadels table — Type cell | `s.type_id` | `s.type_name` | ~2227 |
| 5 | POS table — Type cell | `s.type_id` | `s.type_name` | ~2248 |
| 6 | POS fuel detail | `f.type_id` | `f.type_name` | ~2332 |
| 7 | Project market name | `project.market.type_id` | market name | ~2661 |
| 8 | Member "Active Ship" name | `ship.type_id` | `ship.type_name` | ~3872 |

For the contract-list title cell (#3) the title is free text; the icons act on the
contract's main item (`icon_type_id`) and degrade to no icons when it is absent.

## Error Handling

- Backend: 401 when unauthenticated, 403 with the scope message when ESI returns 403,
  502 for other ESI failures (same as the existing contract route).
- Frontend: failed/network calls surface as a transient `btn-fail` tint + `title`
  message on the clicked icon; no modal/alert.
- Missing `type_id`: plain name, no icons (no console error, no dead buttons).

## Testing

- **pytest** (`tests/`): add coverage for the two new routes mirroring existing
  contract-route tests — unauthenticated → 401; ESI 403 → 403 with scope message;
  happy path (mocked esi call) → `{"ok": true}`.
- **Frontend:** plain JS with no test harness; verify manually that icons render at all
  eight sites, degrade without `type_id`, and trigger the correct endpoint with feedback.

## Out of Scope

- No `data-`-attribute event-delegation refactor of existing handlers.
- No changes to backend payload shapes (all `type_id`s already present).
- No new dependency, build step, or framework.
