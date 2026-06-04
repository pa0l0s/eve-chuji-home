# Character Fittings Page (list, detail, EFT export/import, delete, rename, duplicate)

**Date:** 2026-06-05
**Status:** Approved (design)

## Goal

Add a **Fittings** page to the SPA that shows the logged-in character's personal
EVE fittings. Each fit lists with a ship icon and opens a detail popup (grouped
item table, like ship-contract details). The user can export a fit to EFT text,
import a new fit from EFT text, delete a fit, rename a fit, and duplicate a fit.

Personal fittings only — ESI exposes no corporation fittings endpoint. The page
shows the fittings of whoever is logged in.

## Background / Current State

- Frontend is a single plain-JS SPA, `static/index.html`. Views are `div#view-<name>`
  toggled by `showView(view)` (index.html ~1221), which also calls the view's
  `load<View>()` loader. The nav bar (index.html ~959) holds one `nav-btn` per view.
- A modal helper exists: `showModal(title, body)` / `setModalBody(html)`
  (index.html ~2464), plus `renderFittingModal(eft)` and `copyFitEft(text, btn)`
  (index.html ~1421–1448) used by the mail fitting-link popup.
- `itemName(typeId, name)` (index.html ~2077) renders a type name with an in-game
  market icon; used across contract/project/structure tables.
- Ship icons render from `https://images.evetech.net/types/{type_id}/render?size=64`
  (used at index.html ~3955 for the member active ship).
- EFT machinery already exists in `esi.py`:
  - `build_eft(ship_name, fit_name, items)` (esi.py ~929) renders
    `items=[{name, slot, kind, qty}]` to EFT/Pyfa text. `slot ∈
    low/mid/high/rig/subsystem`; `kind ∈ module/drone/charge`.
  - `get_fitting_type_meta(type_id)` (esi.py ~899) returns `{name, slot, kind}`
    by reading dogma effects; degrades to cargo on ESI error.
  - `resolve_type_ids(names)` (esi.py ~615) bulk-resolves item names → type_ids
    via `POST /universe/ids/`.
  - `get_type_info(type_id)` resolves a single type's name.
- The in-game UI proxy pattern (`open_contract_in_client` + `POST
  /api/ui/open-contract/{id}`) is the template for new ESI calls: send
  `Authorization: Bearer {token}` + `X-Compatibility-Date: 2026-01-01`, call
  `raise_for_status()`, and map ESI 403 → 403 scope message, other errors → 502.
- The token already carries `esi-fittings.read_fittings.v1` and
  `esi-fittings.write_fittings.v1` scopes (confirmed from the user's live token).

## Decisions

- **Structure:** Option A — thin ESI proxy helpers + reuse of the existing EFT
  core. EFT parse/build logic lives as pure functions in `esi.py` next to
  `build_eft`. The detail popup is rendered client-side from the list payload.
- **Detail popup content:** structured, slot-grouped item table (like ship
  contract details), NOT raw EFT. EFT is reached via an **Export EFT** button.
- **List fetch is self-sufficient:** `GET /api/me/fittings` returns each fit
  already enriched with `ship_name` and a prebuilt `eft` string, so the popup and
  the EFT export need no extra round-trip.
- **Delete:** included, with a confirm step in the UI.
- **Import scope:** full EFT — ship line, modules, rigs, drones, cargo, and inline
  loaded charges (`Module Name, Charge Name`).
- **Loaded charges on import:** assigned the **same ESI flag** as the module they
  are loaded into (closest faithful round-trip; mirrors the in-game fitting store).
- **Rename / Duplicate:** ESI has no rename/edit/single-GET endpoint — only
  list/create/delete. Both operations therefore **copy the source fit's raw items
  server-side** (lossless, no EFT round-trip), keyed off the fitting list:
  - **Rename:** create a new fit with the source's items + the new name, then —
    only after the create succeeds — delete the source. Create-before-delete so a
    failure never loses the fit. If the create succeeds but the delete fails, the
    rename returns a 502 noting the original could not be removed (a harmless
    duplicate remains rather than data loss).
  - **Duplicate:** create a new fit with the source's items, keeping the original.
    New name = `"{source name} copy"`; if that name already exists, try
    `"{source name} copy2"`, `copy3`, … until free. (Repeatedly duplicating the
    same fit yields `copy`, `copy2`, `copy3`.) Names are capped at EVE's 50-char
    fitting-name limit.
- **Navigation:** new top-level `Fittings` nav tab.

## ESI Data Shapes

`GET /characters/{character_id}/fittings/` →

```json
[
  {
    "fitting_id": 1,
    "name": "Awesome Fit",
    "description": "<p>desc</p>",
    "ship_type_id": 123,
    "items": [ { "type_id": 2456, "quantity": 1, "flag": "LoSlot0" } ]
  }
]
```

`POST /characters/{character_id}/fittings/` body (same shape minus `fitting_id`)
→ `{ "fitting_id": 42 }`.

`DELETE /characters/{character_id}/fittings/{fitting_id}/` → 204.

There is **no** rename/edit endpoint and **no** single-fitting GET. Rename and
duplicate are composed from list + create (+ delete for rename); the source fit is
found by scanning the `GET …/fittings/` list for the matching `fitting_id`.

**Item `flag` values** used here: `HiSlot0..7`, `MedSlot0..7`, `LoSlot0..7`,
`RigSlot0..2`, `SubSystemSlot0..4`, `DroneBay`, `Cargo`. Any other flag
(`FighterBay`, etc.) is treated as cargo for display/export.

## Design

### Backend — ESI helpers (`esi.py`)

Three thin wrappers (each `async with httpx.AsyncClient()`, bearer header +
`X-Compatibility-Date: 2026-01-01`, `raise_for_status()`):

```python
async def list_character_fittings(character_id: int, access_token: str) -> list[dict]:
    # GET {ESI_BASE}/characters/{character_id}/fittings/  → r.json()

async def create_character_fitting(character_id: int, access_token: str, fitting: dict) -> dict:
    # POST {ESI_BASE}/characters/{character_id}/fittings/  json=fitting → r.json()

async def delete_character_fitting(character_id: int, access_token: str, fitting_id: int) -> None:
    # DELETE {ESI_BASE}/characters/{character_id}/fittings/{fitting_id}/
```

**Flag → slot/kind mapping** (pure helper):

```python
# ESI fitting item flag → (slot, kind) for build_eft.
def flag_to_slot_kind(flag: str) -> tuple[str | None, str]:
    if flag.startswith("HiSlot"):        return ("high", "module")
    if flag.startswith("MedSlot"):       return ("mid", "module")
    if flag.startswith("LoSlot"):        return ("low", "module")
    if flag.startswith("RigSlot"):       return ("rig", "module")
    if flag.startswith("SubSystemSlot"): return ("subsystem", "module")
    if flag == "DroneBay":               return (None, "drone")
    return (None, "charge")              # Cargo / anything else
```

**EFT export from an ESI fitting** (pure; names pre-resolved by the caller):

```python
def eft_from_esi_fitting(fitting: dict, name_map: dict[int, str]) -> str:
    """fitting: ESI fitting dict. name_map: type_id -> name (ship + all items).
    Returns EFT text via build_eft."""
    items = []
    for it in fitting.get("items", []):
        slot, kind = flag_to_slot_kind(it.get("flag", ""))
        items.append({
            "name": name_map.get(it["type_id"], f"Type #{it['type_id']}"),
            "slot": slot, "kind": kind, "qty": it.get("quantity", 1),
        })
    ship_name = name_map.get(fitting["ship_type_id"], f"Type #{fitting['ship_type_id']}")
    return build_eft(ship_name, fitting.get("name", "Fitting"), items)
```

**EFT parse** (pure, no I/O):

```python
def parse_eft(text: str) -> dict:
    """Parse EFT text into {ship_name, fit_name, lines:[{name, charge, qty}]}.
    Raises ValueError if the [Ship, Name] header is missing/malformed.
    - First non-empty line must be '[Ship Name, Fit Name]'.
    - Each subsequent non-empty line: 'Item Name' or 'Item Name, Charge Name'
      or 'Item Name xN' (drones/cargo). Blank lines are group separators and
      are ignored for parsing (slot is derived later from dogma, not position).
    - Lines starting with '[' after the header (e.g. '[Empty Low slot]') are skipped.
    """
```

Returned `lines` entries: `{name, charge|None, qty}`. `qty` parsed from a trailing
` xN`; default 1. `charge` is the text after the first comma, trimmed.

**EFT → ESI items** (async; resolves names and assigns flags):

```python
async def eft_to_esi_fitting(parsed: dict) -> dict:
    """Returns an ESI create payload {name, description, ship_type_id, items:[...]}.
    Raises ValueError(detail) listing any names that could not be resolved."""
```

Algorithm:
1. Collect all names (ship + every module + every charge), call
   `resolve_type_ids(names)`. If any are unresolved, raise
   `ValueError("Could not resolve: <names>")`.
2. Resolve ship_type_id from the ship name.
3. For each parsed module line, `get_fitting_type_meta(type_id)` → slot/kind:
   - `kind == "module"` with a slot → take the next free index for that slot,
     assign flag `{High→HiSlot, Mid→MedSlot, Low→LoSlot, Rig→RigSlot,
     Subsystem→SubSystemSlot}{n}`, `quantity: 1` per unit (qty>1 emits qty items).
   - `kind == "drone"` → flag `DroneBay`, `quantity: qty`.
   - else (charge/unknown) → flag `Cargo`, `quantity: qty`.
   - If the line has an inline `charge`, resolve it and append an item with the
     **same flag** as its module, `quantity: 1`.
4. `name` = parsed fit_name; `description` = "" .

Slot index counters are capped at the EVE maximums (Hi/Med/Lo 8, Rig 3,
SubSystem 5); overflow modules fall back to `Cargo` so a malformed/overfull paste
still imports rather than erroring.

**Copy helpers for rename/duplicate** (pure):

```python
def fitting_to_create_payload(fit: dict, name: str) -> dict:
    """Build an ESI create payload that clones `fit` under a new name (≤50 chars).
    Copies only the ESI item fields, ignoring any enrichment (ship_name/eft/name)."""
    return {
        "name": name[:50],
        "description": fit.get("description") or "",
        "ship_type_id": fit["ship_type_id"],
        "items": [{"type_id": it["type_id"], "quantity": it["quantity"], "flag": it["flag"]}
                  for it in fit.get("items", [])],
    }


def next_copy_name(base: str, existing: set[str]) -> str:
    """'{base} copy', else '{base} copy2', 'copy3', … avoiding `existing`. ≤50 chars."""
    candidate = f"{base} copy"
    if candidate not in existing:
        return candidate[:50]
    n = 2
    while f"{base} copy{n}" in existing:
        n += 1
    return f"{base} copy{n}"[:50]
```

### Backend — routes (`main.py`)

```python
@app.get("/api/me/fittings")
async def my_fittings(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(403, "Missing scope: esi-fittings.read_fittings.v1")
        raise HTTPException(502, "ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(502, "ESI unavailable")
    # Resolve every type_id once (ships + items), enrich with ship_name + eft.
    ids = {f["ship_type_id"] for f in fits} | {
        it["type_id"] for f in fits for it in f.get("items", [])
    }
    name_map = await resolve_names(list(ids))   # {id: name}; degrades to {} on error
    for f in fits:
        f["ship_name"] = name_map.get(f["ship_type_id"], f"Type #{f['ship_type_id']}")
        for it in f.get("items", []):
            it["name"] = name_map.get(it["type_id"], f"Type #{it['type_id']}")
        f["eft"] = eft_from_esi_fitting(f, name_map)
    return {"fittings": fits}
```

`resolve_names` (esi.py ~390) already resolves type_ids → names via
`POST /universe/names/` and returns `{id: name}`, degrading to `{}` on ESI error;
missing ids fall back to `Type #<id>` at the call site. No new helper needed.

```python
@app.post("/api/me/fittings/import")
async def import_fitting(body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        parsed = parse_eft(body.get("eft") or "")
    except ValueError:
        raise HTTPException(400, "Not a valid EFT fit")
    try:
        payload = await eft_to_esi_fitting(parsed)
    except ValueError as e:
        raise HTTPException(400, str(e))         # surfaces unresolved names
    except httpx.HTTPError:
        raise HTTPException(502, "ESI unavailable")
    try:
        created = await create_character_fitting(character_id, token, payload)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(403, "Missing scope: esi-fittings.write_fittings.v1")
        raise HTTPException(502, "ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(502, "ESI unavailable")
    return {"fitting_id": created.get("fitting_id"), "name": payload["name"]}


@app.delete("/api/me/fittings/{fitting_id}")
async def remove_fitting(fitting_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(403, "Missing scope: esi-fittings.write_fittings.v1")
        raise HTTPException(502, "ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(502, "ESI unavailable")
    return {"ok": True}
```

**Duplicate** and **rename** both fetch the fitting list, find the source by id,
and clone it via `create_character_fitting`. A small internal helper centralises
the read-then-find and the ESI-error → HTTP mapping (shared by all the new routes):

```python
def _fit_http_error(e: httpx.HTTPError, scope: str) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
        return HTTPException(403, f"Missing scope: {scope}")
    return HTTPException(502, "ESI unavailable")


@app.post("/api/me/fittings/{fitting_id}/duplicate")
async def duplicate_fitting(fitting_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    src = next((f for f in fits if f["fitting_id"] == fitting_id), None)
    if src is None:
        raise HTTPException(404, "Fitting not found")
    name = next_copy_name(src.get("name") or "Fitting", {f.get("name") for f in fits})
    try:
        created = await create_character_fitting(
            character_id, token, fitting_to_create_payload(src, name))
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    return {"fitting_id": created.get("fitting_id"), "name": name}


@app.post("/api/me/fittings/{fitting_id}/rename")
async def rename_fitting(fitting_id: int, body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "Name required")
    try:
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    src = next((f for f in fits if f["fitting_id"] == fitting_id), None)
    if src is None:
        raise HTTPException(404, "Fitting not found")
    # Create the renamed copy FIRST; only delete the original once it exists.
    try:
        created = await create_character_fitting(
            character_id, token, fitting_to_create_payload(src, new_name))
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    try:
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPError:
        # New fit exists but the old one survives — report rather than lose data.
        raise HTTPException(502, "Renamed copy created, but the original could not be removed")
    return {"fitting_id": created.get("fitting_id"), "name": new_name}
```

### Frontend (`static/index.html`)

**Nav + view wiring:**
- Add `<button class="nav-btn" id="btn-fittings" onclick="showView('fittings')">Fittings</button>`
  to the nav-links block (after Member).
- Add `'fittings'` to the `showView()` view list and `if (view === 'fittings') loadFittings();`.
- Add `<div id="view-fittings" class="hidden">…</div>` in `<main>`: a page title,
  an **Import EFT** button, and a `#fittings-grid` container (reuse the existing
  card grid CSS used by other views).

**`loadFittings()`:**
- `GET /api/me/fittings`; on error show a banner in the view.
- Cache the returned `fittings` array in a module-level `let fittingsData = []`
  (so the popup reads from it without a refetch).
- Render one card per fit: ship render icon
  (`https://images.evetech.net/types/${ship_type_id}/render?size=64`), fit name
  (escaped), ship name, item count. Card `onclick` → `openFitDetail(fitting_id)`.
- Empty state: a muted "No fittings found." message.

**`openFitDetail(fittingId)`:**
- Find the fit in `fittingsData`. `showModal(escHtml(fit.name), bodyHtml)`.
- `bodyHtml`: a button bar (**Export EFT**, **Rename**, **Duplicate**, **Delete**)
  + an inline banner area + a slot-grouped table. Group `fit.items` by
  `flag_to_slot_kind` equivalent in JS (a small `fitSlotOf(flag)` helper) into
  High/Mid/Low/Rig/Subsystem/Drones/Cargo; render only non-empty sections. Each
  row: quantity + `itemName(it.type_id, it.name)`.
- **Export EFT** button → `renderFittingModal(fit.eft)` (existing helper: shows the
  EFT `<pre>` + Copy button).
- **Rename** button → `openRenameFitting(fittingId)`: swaps in a small text input
  (prefilled with the current name) + Save. Save → `POST
  /api/me/fittings/${id}/rename` with `{name}` → on success close modal +
  `loadFittings()`; on error show the banner.
- **Duplicate** button → `duplicateFit(fittingId)`: `POST
  /api/me/fittings/${id}/duplicate` (no body) → on success close modal +
  `loadFittings()`; on error show the banner. Button disables while in flight.
- **Delete** button → `confirmDeleteFit(fittingId)`: a confirm (inline two-step:
  button text changes to "Confirm delete?" and a second click performs it,
  matching the app's no-`window.confirm` style) → `DELETE
  /api/me/fittings/${fittingId}` → on success close modal + `loadFittings()`; on
  error show the banner.

**Import modal (`openImportFitting()`):**
- `showModal('Import EFT Fitting', html)` where `html` is a `<textarea>` +
  an inline banner area + a **Save** button.
- Save → `POST /api/me/fittings/import` with `{eft: textarea.value}`. On success
  close modal + `loadFittings()`. On error show `detail` in the modal banner.

## Error Handling

- Backend: 401 unauthenticated (via `get_current_character_id`); 403 with the
  matching scope message on ESI 403; 400 "Not a valid EFT fit" when `parse_eft`
  fails; 400 with the unresolved-names list when name resolution fails; 400 "Name
  required" on an empty rename; 404 "Fitting not found" when a rename/duplicate
  source id is missing; 502 for other ESI failures. Rename returns 502 "Renamed
  copy created, but the original could not be removed" if the new fit is created
  but the source delete fails (no data loss — a duplicate remains).
- Frontend: failures surface as banners inside the relevant view/modal — no
  `alert()`/`window.confirm()`. Delete uses an inline two-step confirm.
- Missing/unknown type names degrade to `Type #<id>` rather than failing the list.

## Testing

**pytest** (`tests/test_fittings_page.py` — new; existing `tests/test_fitting.py`
covers the mail EFT path and stays as-is):

- Pure `parse_eft`: header parsing; `ValueError` on missing/malformed header;
  inline charge split (`Module, Charge`); ` xN` quantity; drone/cargo lines;
  skipping `[Empty … slot]` lines and blank lines.
- Pure `flag_to_slot_kind` / `eft_from_esi_fitting`: each flag prefix → correct
  slot/kind; full fixture fitting → expected EFT text (asserts ordering and
  `build_eft` reuse).
- `eft_to_esi_fitting` (respx-mocked `resolve_type_ids`/`get_fitting_type_meta`):
  slot-flag index assignment, inline charge gets the module's flag, unresolved
  name → `ValueError`.
- Pure `next_copy_name`: `base` → `base copy`; `base copy` taken → `base copy2`;
  `base copy` + `base copy2` taken → `base copy3`. Pure `fitting_to_create_payload`:
  copies `ship_type_id` + only `{type_id, quantity, flag}` item fields, drops
  enrichment keys, caps name at 50 chars.
- Routes (respx, mirroring existing route tests + `tests/conftest.py` auth
  fixtures): `GET /api/me/fittings` happy path (mock ESI list + names) →
  enriched payload with `ship_name`/`eft`/item `name`; `POST .../import` happy
  path → `{fitting_id}`; `DELETE .../{id}` happy path → `{ok: true}`;
  `POST .../{id}/duplicate` happy path (mock list + create) → new `{fitting_id,
  name}` with the `copy` name and the source's items in the create body;
  `POST .../{id}/rename` happy path (mock list + create + delete) → calls create
  then delete and returns the new id; rename with empty name → 400; rename/
  duplicate with unknown id → 404; unauthenticated → 401 for each; ESI 403 → 403
  scope message; ESI 5xx → 502; import of junk text → 400.

**Frontend:** plain JS, no harness — validate by extracting inline `<script>` and
running `node --check`, plus manual verification that the page lists fits, the
popup groups items, export shows EFT, import round-trips, and delete confirms.

## Out of Scope

- No corporation fittings (no such ESI endpoint).
- No in-place item editing. Renaming is supported (via recreate); changing a fit's
  modules means delete + re-import, or duplicate + edit in-game.
- No fit simulation / stats (EHP, DPS) — display and EFT round-trip only.
- No changes to the existing mail fitting-link popup path.
