# EVE Fitting Links → EFT Popup — Design Spec

**Date:** 2026-05-31
**Status:** Approved (pending written-spec review)

## Goal

In the Mail reader, EVE mail bodies can contain in-game **fitting links**:

```
<a href="fitting:621:31360;1:4395;1:...:28670;1::">ess</a>
```

When the user clicks such a link, open a popup showing the fit in **EFT / Pyfa-importable
text format**, e.g.:

```
[Caracal, ess]
Power Diagnostic System II
Reactor Control Unit II
Ballistic Control System II
Ballistic Control System II

Large Ancillary Shield Booster
Large Ancillary Shield Booster
100MN Y-S8 Compact Afterburner
Multispectrum Shield Hardener II
Dread Guristas EM Shield Amplifier

Rapid Light Missile Launcher II
Rapid Light Missile Launcher II
Rapid Light Missile Launcher II
Rapid Light Missile Launcher II
Rapid Light Missile Launcher II

Medium Ancillary Current Router I
Medium Processor Overclocking Unit I
Medium Ancillary Current Router II



Acolyte II x2

Caldari Navy Scourge Light Missile x1000
...
```

The popup contains the EFT text in a monospace block and a **Copy** button.

## Decisions (locked)

- Popup content: **EFT text + Copy button** (minimal, import-focused).
- Line order within a slot section follows the fitting link's encoding (grouping is what
  matters; EFT/Pyfa import is order-independent).
- **Fitted modules expand to one line per unit** (e.g. `1877;5` → five
  `Rapid Light Missile Launcher II` lines). **Drones and cargo use `Name xqty`.**
- The fit **name** comes from the anchor's display text (`ess` above). The **ship name**
  comes from resolving the ship type id.
- Charges/ammo go in the **Cargo** section (EFT-standard), not loaded into launchers.
- The endpoint is **session-gated** (`/api/me/...`), reached from the authenticated Mail
  page. ESI type data is public, so the user's token is not required.
- Compute the EFT on the **backend** (reuse type cache + name resolution, parallelized ESI
  lookups, unit-testable). Not client-side.

## Fitting link format (verified live)

`fitting:<shipTypeID>:<typeID>;<qty>:<typeID>;<qty>:…::`

A flat list of `typeID;qty` pairs after the ship id, terminated by `::`. **The list is NOT
in slot order** — verified: in the example the first pair `31360` is a *rig* (Medium
Ancillary Current Router I), not a low-slot module. Slot grouping must be derived from each
type's metadata.

## Slot / kind classification (verified via ESI `/universe/types/{id}`)

Each type's `dogma_effects` contains a slot effect:

| effect_id | slot |
|-----------|------|
| 11 | low |
| 13 | mid |
| 12 | high |
| 2663 | rig |
| 3772 | subsystem |

- A type with one of these effects is a **module**, assigned to that slot.
- A type with **no** slot effect is non-fitted: look up its group's `category_id` via
  `/universe/groups/{group_id}`. `category_id == 18` → **drone**; otherwise → **charge**
  (cargo).

## Backend

### `esi.py`

- `parse_fitting_link(href: str) -> tuple[int, list[tuple[int, int]]]` (pure):
  - Strip the leading `fitting:`. Split the remainder on `:`. First field = ship type id.
    Remaining non-empty fields are `typeID;qty` (default qty 1 if `;` missing). Ignore the
    trailing empty fields from `::`. Returns `(ship_type_id, [(type_id, qty), …])`.
  - Returns `(0, [])` if the string doesn't start with `fitting:` or the ship id isn't an int.
- `get_fitting_type_meta(type_id: int) -> dict` → `{"name", "slot", "kind"}`:
  - In-memory cache `_fitting_meta_cache: dict[int, dict]` (type data is static).
  - GET `/universe/types/{type_id}/` → name, group_id, dogma_effects. Map slot from the
    effect table above. If a slot effect is present → `kind="module"`, `slot=<slot>`.
  - Else GET `/universe/groups/{group_id}/` → category_id. `kind="drone"` if category 18 else
    `kind="charge"`; `slot=None`.
  - On any ESI error: `{"name": f"Type #{type_id}", "slot": None, "kind": "charge"}`
    (degrade to cargo so the fit still renders).
- `build_eft(ship_name: str, fit_name: str, items: list[dict]) -> str` (pure):
  - `items` is a list of `{name, slot, kind, qty}`.
  - Header: `[{ship_name}, {fit_name}]`.
  - Slot sections in order `low, mid, high, rig, subsystem`: each fitted module emitted
    `qty` times (one line each), in the order items were encountered.
  - Sections joined by a blank line; empty sections still contribute their separators
    (reproducing the example's gaps, including the empty-subsystem double blank).
  - Then **Drones** (`{name} x{qty}`) and **Cargo** (`{name} x{qty}`), each preceded by a
    blank line, in encounter order.

### `main.py`

- Route `POST /api/me/fitting/eft` (session-gated via `get_current_character_id`):
  - Body: `{"fitting": "<raw fitting: string>", "name": "<fit name>"}`.
  - `ship_id, pairs = parse_fitting_link(fitting)`. If `ship_id == 0` → 400 "Not a fitting link".
  - Resolve ship name via `get_type_info(ship_id)` and each pair's meta via
    `get_fitting_type_meta` using `asyncio.gather`.
  - Build `items` (attach qty), call `build_eft`. Return `{"eft": <text>, "ship": <ship_name>}`.
  - ESI failures are already swallowed per-type (degrade); only unexpected errors → 502.
  - Place this route with the other `/api/me/*` routes; no `{mail_id}`-style ordering concern
    (distinct path prefix `/api/me/fitting/`).

## Frontend (`static/index.html`)

- **Sanitizer branch** in `sanitizeEveMail`: for `<a href="fitting:…">`, build an anchor
  (`mail-fitting-link` class, accent-colored, cursor pointer) via DOM APIs. Stash the raw
  href and the link text, and set `el.onclick = (e) => { e.preventDefault();
  openFittingModal(rawHref, linkText); }`. No untrusted innerHTML (XSS-safe, consistent with
  the rest of the sanitizer).
- **`openFittingModal(fittingStr, fitName)`**:
  - `showModal('Fitting', '<loading>')`, then `POST /api/me/fitting/eft` with
    `{fitting: fittingStr, name: fitName}`.
  - On success: render a `<pre class="fit-eft">` with the EFT text (via `textContent`, not
    innerHTML) and a **Copy** button that uses `navigator.clipboard.writeText` (with a
    "Copied!" confirmation; fallback to a hidden textarea + `execCommand('copy')` if
    clipboard API unavailable).
  - On failure: friendly error in the modal body.
- **CSS**: `.mail-fitting-link` (accent, underline), `.fit-eft` (monospace, dark panel,
  scrollable, preserves whitespace), and a copy-button style reusing `.btn-action`.

## Error handling

- Non-fitting / malformed string → 400; modal shows "This isn't a valid fitting link."
- ESI unavailable for the ship type or a partial failure → the fit still renders with
  `Type #<id>` placeholders for unresolved items; total ESI outage → 502 and the modal shows
  "Could not load fitting (ESI unavailable)."
- Clipboard denied → show the text selected so the user can copy manually.

## Testing (`pytest` + `respx`)

- `tests/test_fitting.py`:
  - `parse_fitting_link`: well-formed string (ship + pairs, trailing `::`), missing-qty
    default, non-fitting input → `(0, [])`.
  - `get_fitting_type_meta`: mock `/universe/types` + `/universe/groups` for a low module
    (effect 11), high module (12), rig (2663), drone (group→category 18), charge
    (no slot effect, non-drone category). Assert slot/kind. Assert in-memory cache (second
    call doesn't refetch).
  - `build_eft`: golden test on a small synthetic fit — ship + 2 low + 1 high(qty 3) +
    1 rig + 1 drone(qty 2) + 1 charge(qty 1000). Assert header, module expansion (3 high
    lines), `xqty` for drone/cargo, and section grouping/order.
  - Route `POST /api/me/fitting/eft`: happy path (mock the type/group calls), 400 on
    non-fitting input, 401 without session.

## Out of scope (YAGNI)

- Other client-only link types (`showinfo:`, `contract:`) — unchanged (still non-clickable
  styled text). Only `fitting:` is handled here.
- Rendered slot-by-slot styled view (only the raw EFT text + Copy).
- Mutated/abyssal module attributes, charge-in-module association, ship subsystem ordering
  nuance beyond basic grouping.
- Persisting parsed fits to the DB (in-memory type-meta cache only).
