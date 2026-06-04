# Character Fittings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Fittings page that lists the logged-in character's EVE fittings with ship icons, a grouped detail popup, EFT export, EFT import, and delete.

**Architecture:** Thin async ESI proxy helpers in `esi.py` (list/create/delete) plus pure functions (EFT parse/build, copy-name + clone-payload) reusing the existing `build_eft` / `get_fitting_type_meta` / `resolve_type_ids` / `resolve_names`. Five `/api/me/fittings*` routes in `main.py` (list, import, delete, duplicate, rename). A new `view-fittings` in the single-page `static/index.html` that renders list cards, a slot-grouped detail modal (export/rename/duplicate/delete), and an import modal.

**Tech Stack:** FastAPI, httpx, plain-JS SPA, pytest + respx.

Spec: `docs/superpowers/specs/2026-06-05-fittings-page-design.md`

---

## File Structure

- `esi.py` — add 3 ESI wrappers (`list_character_fittings`, `create_character_fitting`, `delete_character_fitting`) and the pure helpers (`flag_to_slot_kind`, `eft_from_esi_fitting`, `parse_eft`, `eft_to_esi_fitting`, `fitting_to_create_payload`, `next_copy_name`). Placed next to the existing `build_eft` (esi.py:957).
- `main.py` — add imports + 5 routes (`GET /api/me/fittings`, `POST /api/me/fittings/import`, `DELETE /api/me/fittings/{fitting_id}`, `POST /api/me/fittings/{fitting_id}/duplicate`, `POST /api/me/fittings/{fitting_id}/rename`) plus a `_fit_http_error` helper, directly above the static mount (main.py:1705).
- `static/index.html` — nav button, `view-fittings` markup, and the JS (`loadFittings`, `openFitDetail`, `openImportFitting`, helpers).
- `tests/test_fittings_page.py` — new test file (pure-function + route tests). Existing `tests/test_fitting.py` is untouched.

### Conventions to follow (already in the repo)

- ESI fitting endpoints use the **compatibility-date** base (no `/latest`), exactly like the user's working curl and `open_contract_in_client`: full URL `https://esi.evetech.net/characters/{id}/fittings/` with headers `Authorization: Bearer {token}` and `X-Compatibility-Date: 2026-01-01`.
- Name/id resolution helpers (`resolve_names`, `resolve_type_ids`) use `ESI_BASE = "https://esi.evetech.net/latest"` and are reused as-is.
- Routes resolve the caller with `await get_current_character_id(session)` (raises 401 when unauthenticated) then `await get_valid_token(character_id)`. Map ESI 403 → 403 scope message, other `httpx.HTTPError` → 502. This mirrors the `open_information` route (main.py ~96).
- Test auth pattern: `upsert_token(...expires_at=time.time()+1200...)` + `make_session_cookie(character_id)` cookie (see `tests/test_open_window.py`).

---

## Task 1: ESI wrappers + flag mapping + EFT export helper

**Files:**
- Modify: `esi.py` (append after `build_eft`, esi.py:957)
- Test: `tests/test_fittings_page.py` (create)

- [ ] **Step 1: Write failing tests for `flag_to_slot_kind` and `eft_from_esi_fitting`**

Create `tests/test_fittings_page.py`:

```python
import time
import respx
from httpx import Response

from esi import (
    flag_to_slot_kind, eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
    list_character_fittings, create_character_fitting, delete_character_fitting,
    _fitting_meta_cache,
)
from db import init_db, upsert_token
from auth import make_session_cookie

import pytest


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _auth_cookie(character_id=123) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Test Pilot",
        access_token="valid_acc", refresh_token="valid_ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


def test_flag_to_slot_kind_modules():
    assert flag_to_slot_kind("HiSlot0") == ("high", "module")
    assert flag_to_slot_kind("MedSlot3") == ("mid", "module")
    assert flag_to_slot_kind("LoSlot7") == ("low", "module")
    assert flag_to_slot_kind("RigSlot1") == ("rig", "module")
    assert flag_to_slot_kind("SubSystemSlot2") == ("subsystem", "module")


def test_flag_to_slot_kind_drone_and_cargo():
    assert flag_to_slot_kind("DroneBay") == (None, "drone")
    assert flag_to_slot_kind("Cargo") == (None, "charge")
    assert flag_to_slot_kind("FighterBay") == (None, "charge")


def test_eft_from_esi_fitting_groups_and_names():
    fitting = {
        "name": "My Rifter", "ship_type_id": 587,
        "items": [
            {"type_id": 100, "quantity": 1, "flag": "LoSlot0"},
            {"type_id": 200, "quantity": 1, "flag": "HiSlot0"},
            {"type_id": 300, "quantity": 5, "flag": "DroneBay"},
            {"type_id": 400, "quantity": 1000, "flag": "Cargo"},
        ],
    }
    name_map = {587: "Rifter", 100: "Damage Control II", 200: "125mm Gatling AutoCannon II",
                300: "Hobgoblin II", 400: "EMP S"}
    eft = eft_from_esi_fitting(fitting, name_map)
    assert eft.splitlines()[0] == "[Rifter, My Rifter]"
    assert "Damage Control II" in eft
    assert "125mm Gatling AutoCannon II" in eft
    assert "Hobgoblin II x5" in eft
    assert "EMP S x1000" in eft


def test_eft_from_esi_fitting_unknown_name_degrades():
    fitting = {"name": "F", "ship_type_id": 9, "items": [{"type_id": 8, "quantity": 1, "flag": "LoSlot0"}]}
    eft = eft_from_esi_fitting(fitting, {})
    assert eft.splitlines()[0] == "[Type #9, F]"
    assert "Type #8" in eft
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fittings_page.py -k "flag_to_slot_kind or eft_from_esi" -v`
Expected: FAIL with `ImportError: cannot import name 'flag_to_slot_kind'`.

- [ ] **Step 3: Implement the wrappers + helpers in `esi.py`**

Append after `build_eft` (after esi.py:957). Note: `import re` — check the top of `esi.py`; if `re` is not already imported, add `import re` near the other stdlib imports (esi.py:1-6).

```python
# ── Character fittings (ESI write API; compatibility-date base, no /latest) ────
_FITTINGS_BASE = "https://esi.evetech.net/characters"
_FIT_COMPAT = "2026-01-01"


def _fit_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "X-Compatibility-Date": _FIT_COMPAT}


async def list_character_fittings(character_id: int, access_token: str) -> list[dict]:
    """GET the character's saved fittings (raw ESI list)."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_FITTINGS_BASE}/{character_id}/fittings/",
                             headers=_fit_headers(access_token))
        r.raise_for_status()
        return r.json()


async def create_character_fitting(character_id: int, access_token: str, fitting: dict) -> dict:
    """POST a new fitting; returns {'fitting_id': int}."""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_FITTINGS_BASE}/{character_id}/fittings/",
                              json=fitting, headers=_fit_headers(access_token))
        r.raise_for_status()
        return r.json()


async def delete_character_fitting(character_id: int, access_token: str, fitting_id: int) -> None:
    """DELETE one saved fitting."""
    async with httpx.AsyncClient() as client:
        r = await client.delete(f"{_FITTINGS_BASE}/{character_id}/fittings/{fitting_id}/",
                               headers=_fit_headers(access_token))
        r.raise_for_status()


def flag_to_slot_kind(flag: str) -> tuple[str | None, str]:
    """Map an ESI fitting item flag to (slot, kind) for build_eft.
    slot ∈ high/mid/low/rig/subsystem/None; kind ∈ module/drone/charge."""
    if flag.startswith("HiSlot"):        return ("high", "module")
    if flag.startswith("MedSlot"):       return ("mid", "module")
    if flag.startswith("LoSlot"):        return ("low", "module")
    if flag.startswith("RigSlot"):       return ("rig", "module")
    if flag.startswith("SubSystemSlot"): return ("subsystem", "module")
    if flag == "DroneBay":               return (None, "drone")
    return (None, "charge")              # Cargo / FighterBay / anything else


def eft_from_esi_fitting(fitting: dict, name_map: dict[int, str]) -> str:
    """Render an ESI fitting dict to EFT text. name_map: type_id -> name
    (must cover the ship and every item; missing ids degrade to 'Type #<id>')."""
    items = []
    for it in fitting.get("items", []):
        slot, kind = flag_to_slot_kind(it.get("flag", ""))
        items.append({
            "name": name_map.get(it["type_id"], f"Type #{it['type_id']}"),
            "slot": slot, "kind": kind, "qty": it.get("quantity", 1),
        })
    ship_id = fitting["ship_type_id"]
    ship_name = name_map.get(ship_id, f"Type #{ship_id}")
    return build_eft(ship_name, fitting.get("name", "Fitting"), items)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fittings_page.py -k "flag_to_slot_kind or eft_from_esi" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fittings_page.py
git commit -m "feat: ESI fitting wrappers + flag mapping + EFT export helper"
```

---

## Task 2: `parse_eft` pure parser

**Files:**
- Modify: `esi.py` (append after `eft_from_esi_fitting`)
- Test: `tests/test_fittings_page.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_fittings_page.py`:

```python
def test_parse_eft_basic_groups_and_charge():
    text = (
        "[Rifter, My Rifter]\n"
        "Damage Control II\n"
        "\n"
        "200mm AutoCannon II, EMP S\n"
        "\n"
        "Small Projectile Burst Aerator I\n"
        "\n"
        "Hobgoblin II x5\n"
        "EMP S x1000\n"
    )
    p = parse_eft(text)
    assert p["ship_name"] == "Rifter"
    assert p["fit_name"] == "My Rifter"
    names = [l["name"] for l in p["lines"]]
    assert names == ["Damage Control II", "200mm AutoCannon II",
                     "Small Projectile Burst Aerator I", "Hobgoblin II", "EMP S"]
    ac = next(l for l in p["lines"] if l["name"] == "200mm AutoCannon II")
    assert ac["charge"] == "EMP S"
    drone = next(l for l in p["lines"] if l["name"] == "Hobgoblin II")
    assert drone["qty"] == 5


def test_parse_eft_skips_empty_slot_markers_and_blank_lines():
    text = "[Rifter, X]\n[Empty High slot]\n\nDamage Control II\n"
    p = parse_eft(text)
    assert [l["name"] for l in p["lines"]] == ["Damage Control II"]


def test_parse_eft_defaults_fit_name():
    p = parse_eft("[Rifter, ]\nDamage Control II")
    assert p["fit_name"] == "Imported Fit"


def test_parse_eft_rejects_missing_header():
    import pytest
    with pytest.raises(ValueError):
        parse_eft("Damage Control II\n200mm AutoCannon II")
    with pytest.raises(ValueError):
        parse_eft("")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_fittings_page.py -k parse_eft -v`
Expected: FAIL with `ImportError` / `AttributeError` (function not defined).

- [ ] **Step 3: Implement `parse_eft`**

Append after `eft_from_esi_fitting` in `esi.py`:

```python
def parse_eft(text: str) -> dict:
    """Parse EFT text into {ship_name, fit_name, lines:[{name, charge, qty}]}.

    The first non-empty line must be '[Ship Name, Fit Name]'. Subsequent
    non-empty lines are modules/drones/cargo; 'Name, Charge' splits an inline
    loaded charge; a trailing ' xN' sets quantity. Lines beginning with '['
    after the header (e.g. '[Empty High slot]') and blank lines are ignored —
    slot is derived later from dogma, not from EFT line position.
    Raises ValueError if the header is missing or malformed.
    """
    lines_in = (text or "").splitlines()
    header = None
    body_start = 0
    for i, raw in enumerate(lines_in):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]") and "," in s:
            header = s
            body_start = i + 1
        break  # first non-empty line decides: header or error
    if header is None:
        raise ValueError("Not a valid EFT fit (missing [Ship, Name] header)")
    ship_name, _, fit_name = header[1:-1].partition(",")
    ship_name = ship_name.strip()
    fit_name = fit_name.strip() or "Imported Fit"
    if not ship_name:
        raise ValueError("Not a valid EFT fit (missing ship name)")
    out = []
    for raw in lines_in[body_start:]:
        s = raw.strip()
        if not s or s.startswith("["):
            continue
        name_part = s
        charge = None
        if "," in s:
            name_part, _, charge_part = s.partition(",")
            name_part = name_part.strip()
            charge = charge_part.strip() or None
        qty = 1
        m = re.search(r"\sx(\d+)$", name_part)
        if m:
            qty = int(m.group(1))
            name_part = name_part[:m.start()].strip()
        if not name_part:
            continue
        out.append({"name": name_part, "charge": charge, "qty": qty})
    return {"ship_name": ship_name, "fit_name": fit_name, "lines": out}
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_fittings_page.py -k parse_eft -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fittings_page.py
git commit -m "feat: parse_eft EFT-text parser"
```

---

## Task 3: `eft_to_esi_fitting` (resolve names → ESI create payload)

**Files:**
- Modify: `esi.py` (append after `parse_eft`)
- Test: `tests/test_fittings_page.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_fittings_page.py`. These mock the two resolution endpoints `resolve_type_ids` (`POST /latest/universe/ids/`) and `get_fitting_type_meta` (`GET /latest/universe/types/{id}/`) use:

```python
IDS = "https://esi.evetech.net/latest/universe/ids/"
TYPES = "https://esi.evetech.net/latest/universe/types"


@respx.mock
async def test_eft_to_esi_fitting_assigns_flags_and_charge():
    _fitting_meta_cache.clear()
    respx.post(IDS).mock(return_value=Response(200, json={"inventory_types": [
        {"id": 587, "name": "Rifter"},
        {"id": 100, "name": "Damage Control II"},
        {"id": 200, "name": "200mm AutoCannon II"},
        {"id": 210, "name": "EMP S"},
        {"id": 300, "name": "Hobgoblin II"},
    ]}))
    # Damage Control II -> low module
    respx.get(f"{TYPES}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11}]}))
    # 200mm AutoCannon II -> high module
    respx.get(f"{TYPES}/200/").mock(return_value=Response(200, json={
        "name": "200mm AutoCannon II", "group_id": 55,
        "dogma_effects": [{"effect_id": 12}]}))
    # EMP S -> charge (no slot dogma, not a drone group)
    respx.get(f"{TYPES}/210/").mock(return_value=Response(200, json={
        "name": "EMP S", "group_id": 83, "dogma_effects": []}))
    respx.get("https://esi.evetech.net/latest/universe/groups/83/").mock(
        return_value=Response(200, json={"category_id": 8}))
    # Hobgoblin II -> drone
    respx.get(f"{TYPES}/300/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get("https://esi.evetech.net/latest/universe/groups/100/").mock(
        return_value=Response(200, json={"category_id": 18}))

    parsed = {"ship_name": "Rifter", "fit_name": "My Rifter", "lines": [
        {"name": "Damage Control II", "charge": None, "qty": 1},
        {"name": "200mm AutoCannon II", "charge": "EMP S", "qty": 1},
        {"name": "Hobgoblin II", "charge": None, "qty": 5},
    ]}
    payload = await eft_to_esi_fitting(parsed)
    assert payload["ship_type_id"] == 587
    assert payload["name"] == "My Rifter"
    flags = {(i["type_id"], i["flag"], i["quantity"]) for i in payload["items"]}
    assert (100, "LoSlot0", 1) in flags
    assert (200, "HiSlot0", 1) in flags
    assert (210, "HiSlot0", 1) in flags     # charge inherits the module's flag
    assert (300, "DroneBay", 5) in flags


@respx.mock
async def test_eft_to_esi_fitting_unresolved_name_raises():
    import pytest
    respx.post(IDS).mock(return_value=Response(200, json={"inventory_types": [
        {"id": 587, "name": "Rifter"}]}))
    parsed = {"ship_name": "Rifter", "fit_name": "X", "lines": [
        {"name": "Nonexistent Module", "charge": None, "qty": 1}]}
    with pytest.raises(ValueError) as exc:
        await eft_to_esi_fitting(parsed)
    assert "Nonexistent Module" in str(exc.value)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_fittings_page.py -k eft_to_esi -v`
Expected: FAIL (`ImportError` / function not defined).

- [ ] **Step 3: Implement `eft_to_esi_fitting`**

Append after `parse_eft` in `esi.py`:

```python
_SLOT_FLAG_PREFIX = {"high": "HiSlot", "mid": "MedSlot", "low": "LoSlot",
                     "rig": "RigSlot", "subsystem": "SubSystemSlot"}
_SLOT_MAX = {"high": 8, "mid": 8, "low": 8, "rig": 3, "subsystem": 5}


async def eft_to_esi_fitting(parsed: dict) -> dict:
    """Build an ESI create payload {name, description, ship_type_id, items} from
    a parse_eft() result. Resolves names → type_ids, derives each module's slot
    from dogma, and assigns sequential ESI slot flags. Loaded charges get their
    module's flag; drones → DroneBay; everything else → Cargo. Slot overflow
    spills to Cargo. Raises ValueError listing any names that cannot be resolved.
    """
    names = {parsed["ship_name"]}
    for ln in parsed["lines"]:
        names.add(ln["name"])
        if ln.get("charge"):
            names.add(ln["charge"])
    id_map = await resolve_type_ids(list(names))
    missing = sorted(n for n in names if n not in id_map)
    if missing:
        raise ValueError("Could not resolve: " + ", ".join(missing))
    counters = {k: 0 for k in _SLOT_FLAG_PREFIX}
    items: list[dict] = []
    for ln in parsed["lines"]:
        tid = id_map[ln["name"]]
        meta = await get_fitting_type_meta(tid)
        slot, kind = meta["slot"], meta["kind"]
        qty = ln.get("qty", 1)
        if kind == "module" and slot in _SLOT_FLAG_PREFIX:
            last_flag = None
            for _ in range(qty):
                idx = counters[slot]
                if idx >= _SLOT_MAX[slot]:
                    items.append({"type_id": tid, "quantity": 1, "flag": "Cargo"})
                    continue
                last_flag = f"{_SLOT_FLAG_PREFIX[slot]}{idx}"
                counters[slot] += 1
                items.append({"type_id": tid, "quantity": 1, "flag": last_flag})
            if ln.get("charge") and last_flag:
                items.append({"type_id": id_map[ln["charge"]],
                              "quantity": 1, "flag": last_flag})
        elif kind == "drone":
            items.append({"type_id": tid, "quantity": qty, "flag": "DroneBay"})
        else:
            items.append({"type_id": tid, "quantity": qty, "flag": "Cargo"})
    return {"name": parsed["fit_name"], "description": "",
            "ship_type_id": id_map[parsed["ship_name"]], "items": items}
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_fittings_page.py -k eft_to_esi -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fittings_page.py
git commit -m "feat: eft_to_esi_fitting name resolution + flag assignment"
```

---

## Task 4: Backend routes

**Files:**
- Modify: `main.py` — import block (main.py:30-38) and new routes above the static mount (main.py:1705)
- Test: `tests/test_fittings_page.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_fittings_page.py`. Reuse the `client` fixture from `tests/conftest.py` (auto-available) and the local `_auth_cookie`:

```python
FITS = "https://esi.evetech.net/characters/123/fittings"
NAMES = "https://esi.evetech.net/latest/universe/names/"


async def test_my_fittings_requires_auth(client):
    r = await client.get("/api/me/fittings")
    assert r.status_code == 401


@respx.mock
async def test_my_fittings_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 1, "name": "My Rifter", "description": "",
        "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    respx.post(NAMES).mock(return_value=Response(200, json=[
        {"id": 587, "name": "Rifter"}, {"id": 100, "name": "Damage Control II"}]))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 200
    fits = r.json()["fittings"]
    assert fits[0]["ship_name"] == "Rifter"
    assert fits[0]["items"][0]["name"] == "Damage Control II"
    assert fits[0]["eft"].splitlines()[0] == "[Rifter, My Rifter]"


@respx.mock
async def test_my_fittings_403_maps_to_scope(client):
    respx.get(f"{FITS}/").mock(return_value=Response(403, json={}))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 403
    assert "esi-fittings.read_fittings.v1" in r.json()["detail"]


@respx.mock
async def test_my_fittings_502(client):
    respx.get(f"{FITS}/").mock(return_value=Response(500, json={}))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 502


@respx.mock
async def test_import_fitting_happy_path(client):
    _fitting_meta_cache.clear()
    respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
        return_value=Response(200, json={"inventory_types": [
            {"id": 587, "name": "Rifter"},
            {"id": 100, "name": "Damage Control II"}]}))
    respx.get("https://esi.evetech.net/latest/universe/types/100/").mock(
        return_value=Response(200, json={"name": "Damage Control II",
            "group_id": 60, "dogma_effects": [{"effect_id": 11}]}))
    respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 42}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/import",
                          json={"eft": "[Rifter, My Rifter]\nDamage Control II"},
                          cookies=cookies)
    assert r.status_code == 200
    assert r.json()["fitting_id"] == 42


async def test_import_fitting_rejects_junk(client):
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/import",
                          json={"eft": "just some text\nno header"}, cookies=cookies)
    assert r.status_code == 400


@respx.mock
async def test_delete_fitting_happy_path(client):
    respx.delete(f"{FITS}/7/").mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.delete("/api/me/fittings/7", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_delete_fitting_requires_auth(client):
    r = await client.delete("/api/me/fittings/7")
    assert r.status_code == 401
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_fittings_page.py -k "fittings and (happy or auth or 403 or 502 or junk or delete)" -v`
Expected: FAIL with 404 (routes not defined) / import errors.

- [ ] **Step 3: Add imports to `main.py`**

Modify the `esi` import block. Change the `parse_fitting_link, get_fitting_type_meta, build_eft,` line (main.py:37) to also pull the new symbols:

```python
    parse_fitting_link, get_fitting_type_meta, build_eft,
    list_character_fittings, create_character_fitting, delete_character_fitting,
    eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
```

(`resolve_names` is already imported at main.py:32; reuse it for bulk type-name resolution.)

- [ ] **Step 4: Add the three routes in `main.py`**

Insert directly above the `# ── Static files (must be last)` comment (main.py:1704):

```python
@app.get("/api/me/fittings")
async def my_fittings(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(status_code=403,
                                detail="Missing scope: esi-fittings.read_fittings.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    ids = {f["ship_type_id"] for f in fits}
    ids |= {it["type_id"] for f in fits for it in f.get("items", [])}
    name_map = await resolve_names(list(ids))
    for f in fits:
        f["ship_name"] = name_map.get(f["ship_type_id"], f"Type #{f['ship_type_id']}")
        for it in f.get("items", []):
            it["name"] = name_map.get(it["type_id"], f"Type #{it['type_id']}")
        f["eft"] = eft_from_esi_fitting(f, name_map)
    return {"fittings": fits}


@app.post("/api/me/fittings/import")
async def import_fitting(body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        parsed = parse_eft(body.get("eft") or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="Not a valid EFT fit")
    try:
        payload = await eft_to_esi_fitting(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    try:
        created = await create_character_fitting(character_id, token, payload)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(status_code=403,
                                detail="Missing scope: esi-fittings.write_fittings.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"fitting_id": created.get("fitting_id"), "name": payload["name"]}


@app.delete("/api/me/fittings/{fitting_id}")
async def remove_fitting(fitting_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    token = await get_valid_token(character_id)
    try:
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(status_code=403,
                                detail="Missing scope: esi-fittings.write_fittings.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/test_fittings_page.py -v`
Expected: PASS (all tests). Then run the full suite: `pytest -q` — expected: all green.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_fittings_page.py
git commit -m "feat: /api/me/fittings list, import, delete routes"
```

---

## Task 5: Backend — duplicate & rename routes

**Files:**
- Modify: `esi.py` (append `fitting_to_create_payload` + `next_copy_name` after `eft_to_esi_fitting`)
- Modify: `main.py` — add `_fit_http_error` helper + two routes next to the Task 4 routes
- Test: `tests/test_fittings_page.py`

- [ ] **Step 1: Write failing tests for the pure copy helpers**

Append to `tests/test_fittings_page.py`:

```python
from esi import fitting_to_create_payload, next_copy_name


def test_next_copy_name_first_copy():
    assert next_copy_name("Rifter", set()) == "Rifter copy"
    assert next_copy_name("Rifter", {"Rifter"}) == "Rifter copy"


def test_next_copy_name_increments():
    assert next_copy_name("Rifter", {"Rifter copy"}) == "Rifter copy2"
    assert next_copy_name("Rifter", {"Rifter copy", "Rifter copy2"}) == "Rifter copy3"


def test_next_copy_name_caps_50_chars():
    base = "x" * 60
    assert len(next_copy_name(base, set())) == 50


def test_fitting_to_create_payload_copies_items_only():
    fit = {
        "fitting_id": 9, "name": "Orig", "ship_name": "Rifter", "eft": "...",
        "description": "<p>d</p>", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 2, "flag": "LoSlot0", "name": "DCU II"}],
    }
    p = fitting_to_create_payload(fit, "New Name")
    assert p == {
        "name": "New Name", "description": "<p>d</p>", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 2, "flag": "LoSlot0"}],
    }
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_fittings_page.py -k "next_copy_name or create_payload" -v`
Expected: FAIL with `ImportError` (functions not defined).

- [ ] **Step 3: Implement the pure helpers in `esi.py`**

Append after `eft_to_esi_fitting`:

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

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_fittings_page.py -k "next_copy_name or create_payload" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write failing route tests**

Append to `tests/test_fittings_page.py` (`FITS` and `_auth_cookie` already defined earlier in the file):

```python
@respx.mock
async def test_duplicate_fitting_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 5, "name": "Rifter", "description": "", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    create = respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 88}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/duplicate", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"fitting_id": 88, "name": "Rifter copy"}
    import json as _json
    sent = _json.loads(create.calls.last.request.content)
    assert sent["name"] == "Rifter copy"
    assert sent["ship_type_id"] == 587
    assert sent["items"] == [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}]


@respx.mock
async def test_duplicate_fitting_unknown_id_404(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[]))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/999/duplicate", cookies=cookies)
    assert r.status_code == 404


@respx.mock
async def test_rename_fitting_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 5, "name": "Rifter", "description": "", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    create = respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 90}))
    delete = respx.delete(f"{FITS}/5/").mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/rename",
                          json={"name": "Better Rifter"}, cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"fitting_id": 90, "name": "Better Rifter"}
    assert create.called and delete.called


async def test_rename_fitting_empty_name_400(client):
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/rename", json={"name": "  "}, cookies=cookies)
    assert r.status_code == 400


async def test_duplicate_requires_auth(client):
    r = await client.post("/api/me/fittings/5/duplicate")
    assert r.status_code == 401
```

- [ ] **Step 6: Run to verify they fail**

Run: `pytest tests/test_fittings_page.py -k "duplicate or rename" -v`
Expected: FAIL with 404 (routes not defined).

- [ ] **Step 7: Add imports + routes to `main.py`**

Extend the esi import block (the lines added in Task 4) to also import the two new helpers — change:

```python
    eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
```

to:

```python
    eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
    fitting_to_create_payload, next_copy_name,
```

Then add, directly below the `remove_fitting` route from Task 4 (still above the static mount):

```python
def _fit_http_error(e: httpx.HTTPError, scope: str) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
        return HTTPException(status_code=403, detail=f"Missing scope: {scope}")
    return HTTPException(status_code=502, detail="ESI unavailable")


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
        raise HTTPException(status_code=404, detail="Fitting not found")
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
        raise HTTPException(status_code=400, detail="Name required")
    try:
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    src = next((f for f in fits if f["fitting_id"] == fitting_id), None)
    if src is None:
        raise HTTPException(status_code=404, detail="Fitting not found")
    # Create the renamed copy FIRST; only delete the original once it exists.
    try:
        created = await create_character_fitting(
            character_id, token, fitting_to_create_payload(src, new_name))
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    try:
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=502,
                            detail="Renamed copy created, but the original could not be removed")
    return {"fitting_id": created.get("fitting_id"), "name": new_name}
```

- [ ] **Step 8: Run to verify they pass**

Run: `pytest tests/test_fittings_page.py -v`
Expected: PASS (all). Then `pytest -q` — all green.

- [ ] **Step 9: Commit**

```bash
git add esi.py main.py tests/test_fittings_page.py
git commit -m "feat: duplicate & rename fitting routes (clone source items)"
```

---

## Task 6: Frontend — nav tab, view, list rendering

**Files:**
- Modify: `static/index.html` — nav (index.html:966), view markup (after `view-member` block), `showView` (index.html:1223-1238), and JS (add near the mail/projects loaders).

- [ ] **Step 1: Add the nav button**

After the Member nav button (index.html:966) add:

```html
      <button class="nav-btn" id="btn-fittings" onclick="showView('fittings')">Fittings</button>
```

- [ ] **Step 2: Register the view in `showView`**

In `showView` (index.html:1223) add `'fittings'` to the view list array, so it reads:

```javascript
    ['contracts', 'projects', 'structures', 'fleet', 'member', 'admin', 'mail', 'fittings'].forEach(v => {
```

And add a dispatch line alongside the others (after `if (view === 'member') loadMember();`, index.html:1236):

```javascript
    if (view === 'fittings') loadFittings();
```

- [ ] **Step 3: Add the view markup**

Insert a new view block in `<main>` after the `view-member` block (which ends near index.html:1084's div; place it right before the `view-mail` block or after `view-member`'s closing `</div>`):

```html
    <!-- Fittings view -->
    <div id="view-fittings" class="hidden">
      <div id="fittings-error" class="banner"></div>
      <div style="display:flex;align-items:center;justify-content:space-between;
                  gap:1rem;max-width:1400px;margin:0 auto 1.5rem auto">
        <div class="page-title" style="margin:0">My Fittings</div>
        <button class="btn-action" onclick="openImportFitting()">Import EFT</button>
      </div>
      <div id="fittings-grid"
           style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
                  gap:1rem;max-width:1400px;margin:0 auto"></div>
    </div>
```

- [ ] **Step 4: Add `loadFittings` + list rendering JS**

Add near the other view loaders (e.g. after `loadProjects`, or anywhere in the main `<script>`). `fittingsData` is the module-level cache the detail popup reads:

```javascript
  // ── Fittings ────────────────────────────────────────────────────────────────
  let fittingsData = [];

  async function loadFittings() {
    const grid = document.getElementById('fittings-grid');
    const err = document.getElementById('fittings-error');
    err.classList.remove('show'); err.textContent = '';
    grid.innerHTML = '<div style="color:#66768c;padding:1rem">Loading fittings…</div>';
    try {
      const r = await fetch('/api/me/fittings');
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        err.textContent = d.detail || 'Could not load fittings.';
        err.classList.add('show'); grid.innerHTML = '';
        return;
      }
      const data = await r.json();
      fittingsData = data.fittings || [];
      renderFittingsList();
    } catch {
      err.textContent = 'Network error loading fittings.';
      err.classList.add('show'); grid.innerHTML = '';
    }
  }

  function renderFittingsList() {
    const grid = document.getElementById('fittings-grid');
    if (!fittingsData.length) {
      grid.innerHTML = '<div style="color:#66768c;padding:1rem">No fittings found.</div>';
      return;
    }
    grid.innerHTML = fittingsData.map(f => `
      <div class="card" style="display:flex;gap:0.85rem;align-items:center;cursor:pointer"
           onclick="openFitDetail(${f.fitting_id})">
        <img src="https://images.evetech.net/types/${f.ship_type_id}/render?size=64"
             alt="" width="48" height="48" style="border-radius:6px;flex:none">
        <div style="min-width:0">
          <div style="font-weight:600;color:#f8fafc;white-space:nowrap;overflow:hidden;
                      text-overflow:ellipsis">${escHtml(f.name || 'Fitting')}</div>
          <div style="font-size:0.8rem;color:#808fa6">${escHtml(f.ship_name || '')}</div>
          <div style="font-size:0.72rem;color:#66768c">${(f.items || []).length} items</div>
        </div>
      </div>`).join('');
  }
```

- [ ] **Step 5: Validate JS parses**

Run (extracts the inline script and syntax-checks it):

```bash
python3 - <<'PY'
import re, subprocess, sys, tempfile, os
html = open('static/index.html').read()
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
src = max(scripts, key=len)
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
    f.write(src); path = f.name
r = subprocess.run(['node', '--check', path])
os.unlink(path); sys.exit(r.returncode)
PY
```

Expected: exit 0 (no syntax errors).

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: Fittings nav tab, view, and list rendering"
```

---

## Task 7: Frontend — detail popup, EFT export, rename, duplicate, delete, import modal

**Files:**
- Modify: `static/index.html` — add JS after the `loadFittings`/`renderFittingsList` block from Task 6. Reuses `showModal`, `setModalBody` (index.html:2464-2469), `renderFittingModal` (index.html:1421), `copyFitEft` (index.html:1437), `itemName` (index.html:2077), `escHtml`.

- [ ] **Step 1: Add the slot grouping helper + detail popup**

```javascript
  // ESI fitting item flag -> display group key.
  function fitSlotOf(flag) {
    if (flag.startsWith('HiSlot')) return 'high';
    if (flag.startsWith('MedSlot')) return 'mid';
    if (flag.startsWith('LoSlot')) return 'low';
    if (flag.startsWith('RigSlot')) return 'rig';
    if (flag.startsWith('SubSystemSlot')) return 'subsystem';
    if (flag === 'DroneBay') return 'drones';
    return 'cargo';
  }

  const FIT_GROUP_LABELS = [
    ['high', 'High Slots'], ['mid', 'Mid Slots'], ['low', 'Low Slots'],
    ['rig', 'Rigs'], ['subsystem', 'Subsystems'],
    ['drones', 'Drone Bay'], ['cargo', 'Cargo'],
  ];

  function openFitDetail(fittingId) {
    const fit = fittingsData.find(f => f.fitting_id === fittingId);
    if (!fit) return;
    const groups = {};
    (fit.items || []).forEach(it => {
      const g = fitSlotOf(it.flag || '');
      (groups[g] = groups[g] || []).push(it);
    });
    const sections = FIT_GROUP_LABELS.filter(([k]) => groups[k]).map(([k, label]) => {
      const rows = groups[k].map(it => `
        <div style="display:flex;justify-content:space-between;gap:1rem;
                    padding:0.25rem 0;border-bottom:1px solid #1e2130">
          <span>${itemName(it.type_id, it.name)}</span>
          <span style="color:#66768c;flex:none">${it.quantity > 1 ? '×' + it.quantity : ''}</span>
        </div>`).join('');
      return `<div style="margin-bottom:0.9rem">
          <div class="card-title" style="margin-bottom:0.4rem">${label}</div>${rows}</div>`;
    }).join('');
    const body = `
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;flex-wrap:wrap;
                  margin-bottom:0.75rem">
        <button class="btn-action" onclick="exportFitEft(${fit.fitting_id})">Export EFT</button>
        <button class="btn-action" onclick="openRenameFitting(${fit.fitting_id})">Rename</button>
        <button class="btn-action" id="fit-dup-btn"
                onclick="duplicateFit(${fit.fitting_id})">Duplicate</button>
        <button class="btn-action" id="fit-del-btn"
                onclick="confirmDeleteFit(${fit.fitting_id})">Delete</button>
      </div>
      <div id="fit-detail-err" class="banner"></div>
      ${sections || '<div style="color:#66768c">No items.</div>'}`;
    showModal(escHtml(fit.name || 'Fitting'), body);
  }

  function exportFitEft(fittingId) {
    const fit = fittingsData.find(f => f.fitting_id === fittingId);
    if (fit) renderFittingModal(fit.eft);   // existing helper: EFT <pre> + Copy
  }
```

- [ ] **Step 2: Add delete (inline two-step confirm)**

```javascript
  function confirmDeleteFit(fittingId) {
    const btn = document.getElementById('fit-del-btn');
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.textContent = 'Confirm delete?';
      btn.style.color = '#f87171';
      setTimeout(() => {
        if (btn) { btn.dataset.armed = ''; btn.textContent = 'Delete'; btn.style.color = ''; }
      }, 3000);
      return;
    }
    doDeleteFit(fittingId);
  }

  async function doDeleteFit(fittingId) {
    const err = document.getElementById('fit-detail-err');
    try {
      const r = await fetch('/api/me/fittings/' + fittingId, { method: 'DELETE' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        if (err) { err.textContent = d.detail || 'Delete failed.'; err.classList.add('show'); }
        return;
      }
      closeModal();
      loadFittings();
    } catch {
      if (err) { err.textContent = 'Network error.'; err.classList.add('show'); }
    }
  }
```

Note: the modal close function is `closeModal()` (static/index.html:2472) — used in `doDeleteFit`, `duplicateFit`, `submitRenameFitting`, and `submitImportFitting`. The `.btn-action` class already styles `:disabled` (index.html:836), so disabling buttons in-flight works without extra CSS.

- [ ] **Step 3: Add duplicate + rename handlers**

```javascript
  async function duplicateFit(fittingId) {
    const btn = document.getElementById('fit-dup-btn');
    const err = document.getElementById('fit-detail-err');
    if (btn) btn.disabled = true;
    try {
      const r = await fetch('/api/me/fittings/' + fittingId + '/duplicate', { method: 'POST' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        if (err) { err.textContent = d.detail || 'Duplicate failed.'; err.classList.add('show'); }
        if (btn) btn.disabled = false;
        return;
      }
      closeModal();
      loadFittings();
    } catch {
      if (err) { err.textContent = 'Network error.'; err.classList.add('show'); }
      if (btn) btn.disabled = false;
    }
  }

  function openRenameFitting(fittingId) {
    const fit = fittingsData.find(f => f.fitting_id === fittingId);
    if (!fit) return;
    const body = `
      <div id="fit-rename-err" class="banner"></div>
      <label style="display:block;color:#808fa6;font-size:0.8rem;margin-bottom:0.4rem">
        New fitting name</label>
      <input id="fit-rename-input" type="text" maxlength="50"
             value="${escHtml(fit.name || '')}"
             style="width:100%;padding:0.5rem;background:#0f1117;border:1px solid #2d3348;
                    border-radius:6px;color:#f8fafc">
      <div style="display:flex;justify-content:flex-end;margin-top:0.75rem">
        <button class="btn-action" onclick="submitRenameFitting(this, ${fittingId})">Save</button>
      </div>`;
    showModal('Rename Fitting', body);
    const input = document.getElementById('fit-rename-input');
    if (input) input.focus();
  }

  async function submitRenameFitting(btn, fittingId) {
    const err = document.getElementById('fit-rename-err');
    const name = (document.getElementById('fit-rename-input') || {}).value || '';
    err.classList.remove('show'); err.textContent = '';
    if (!name.trim()) {
      err.textContent = 'Name required.'; err.classList.add('show');
      return;
    }
    btn.disabled = true;
    try {
      const r = await fetch('/api/me/fittings/' + fittingId + '/rename', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        err.textContent = d.detail || 'Rename failed.'; err.classList.add('show');
        btn.disabled = false;
        return;
      }
      closeModal();
      loadFittings();
    } catch {
      err.textContent = 'Network error.'; err.classList.add('show');
      btn.disabled = false;
    }
  }
```

- [ ] **Step 4: Add the import modal**

```javascript
  function openImportFitting() {
    const body = `
      <div id="fit-import-err" class="banner"></div>
      <p style="color:#808fa6;font-size:0.82rem;margin:0 0 0.5rem">
        Paste an EFT-format fitting (starts with <code>[Ship, Name]</code>).</p>
      <textarea id="fit-import-text" class="fit-eft"
                style="width:100%;height:240px;resize:vertical"
                placeholder="[Rifter, My Rifter]&#10;Damage Control II&#10;..."></textarea>
      <div style="display:flex;justify-content:flex-end;margin-top:0.75rem">
        <button class="btn-action" onclick="submitImportFitting(this)">Save Fitting</button>
      </div>`;
    showModal('Import EFT Fitting', body);
  }

  async function submitImportFitting(btn) {
    const err = document.getElementById('fit-import-err');
    const eft = (document.getElementById('fit-import-text') || {}).value || '';
    err.classList.remove('show'); err.textContent = '';
    btn.disabled = true;
    try {
      const r = await fetch('/api/me/fittings/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ eft }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        err.textContent = d.detail || 'Import failed.'; err.classList.add('show');
        btn.disabled = false;
        return;
      }
      closeModal();
      loadFittings();
    } catch {
      err.textContent = 'Network error.'; err.classList.add('show');
      btn.disabled = false;
    }
  }
```

- [ ] **Step 5: Validate JS parses**

Run the same extract-and-`node --check` snippet from Task 6 Step 5.
Expected: exit 0.

- [ ] **Step 6: Manual smoke (document, do not block on it)**

With the app running and logged in: open Fittings → cards list with ship icons; click a card → grouped item table; **Export EFT** → EFT text + Copy; **Rename** → change name → saves and reloads; **Duplicate** → creates a "… copy" and reloads; **Import EFT** → paste a fit → saves and reloads; **Delete** → two-step confirm → removes. (Requires the EVE account's real fittings; safe to verify post-merge.)

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: fitting detail popup with export, rename, duplicate, delete, and import"
```

---

## Final Verification

- [ ] Run the full backend suite: `pytest -q` — expected: all tests pass (existing + new fittings tests).
- [ ] Run the JS syntax check snippet once more — expected: exit 0.
- [ ] Confirm no stray files were added: `git status` is clean.

## Notes / Known Limitations (from the spec)

- ESI saved fittings cannot mark a charge as "loaded into" a module; on import a loaded charge is stored with its module's slot flag (closest faithful round-trip).
- Rename and duplicate clone the source's raw items server-side (lossless). Rename creates the renamed copy before deleting the original, so a failure never loses data.
- No in-place item editing (delete + re-import to change modules), no fit stats/simulation, no corporation fittings (no such ESI endpoint).
