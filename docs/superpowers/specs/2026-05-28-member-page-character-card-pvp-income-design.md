# Member Page Character Card + PVP Income — Design Spec

**Date:** 2026-05-28
**Status:** Approved

## Goal

Restructure the Member page so the **Character** card carries the
identity + activity + money fields at a glance, and the **Financials &
Skills** card collapses to just **Skills**. Specifically:

- Move **Last Login**, **Total Logins**, and **Wallet Balance** from the
  Financials & Skills card into the Character card.
- Add a new **PVP Income (30d)** metric to the Character card. It's the sum
  of wallet-journal entries with `ref_type == "corporation_account_withdrawal"`
  AND `"Cerberus Agnon"` substring in `description` — the corp's standing
  pattern for paying out PVP rewards via manual Corporation Account
  Withdrawal.
- Rename the remaining card from **"Financials & Skills"** to **"Skills"**.

## Context

- `/api/member` (main.py:1198) fans out across `_safe(...)` calls and returns
  per-section data wrapped via `_wrap(data, error)`. Sections used by the
  Character/Financials cards today: `profile`, `wallet`, `skills`, `online`.
- Frontend uses three render helpers in `static/index.html`:
  - `renderCharacterCard` (line ~3090): portrait + name/zkill + corp + sec
    status + online dot.
  - `renderFinancialsCard` (line ~3182): Wallet Balance + 3 SP metrics +
    2-col footer (Last Login, Total Logins).
  - `renderSkillCoverageCard` (out of scope here).
- The wallet journal scope `esi-wallet.read_character_wallet.v1` is **already
  granted** (auth.py:24) — no SSO change is needed.
- ESI keeps wallet-journal entries for ~30 days (per the official endpoint
  docs). Paginated via `?page=N`; total page count comes back in the
  `X-Pages` response header.
- An example journal entry shape (provided by the user) for a Corporation
  Account Withdrawal — note positive `amount` (the character received the
  payout), the descriptive `description`, and `ref_type`:

```json
{
  "amount": 371000000,
  "balance": 3443556967.5222,
  "context_id": 94409715,
  "context_id_type": "character_id",
  "date": "2026-05-26T17:26:04Z",
  "description": "Cerberus Agnon transferred cash from Grupa Operacyjna ZLY CHUJI's corporate account to Paolo Thompson's account",
  "first_party_id": 98340844,
  "id": 25617989006,
  "reason": "Astrahus",
  "ref_type": "corporation_account_withdrawal",
  "second_party_id": 2122515161
}
```

## Components

### 1. ESI — `get_character_wallet_journal`

Add to `esi.py` alongside `get_wallet`:

```python
async def get_character_wallet_journal(character_id: int, access_token: str) -> list[dict]:
    """All wallet-journal entries (ESI keeps ~30 days). Walks pages until empty."""
    result: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/characters/{character_id}/wallet/journal/",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"page": page},
            )
            r.raise_for_status()
            entries = r.json() or []
            if not entries:
                break
            result.extend(entries)
            total_pages = int(r.headers.get("X-Pages", "1"))
            if page >= total_pages:
                break
            page += 1
    return result
```

Pagination strategy mirrors `get_corp_contracts` / similar helpers already
in `esi.py`. The "empty page → stop" guard handles the case where ESI omits
or zeros `X-Pages`.

### 2. PVP-income filter + sum

Pure function added to `main.py`:

```python
def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(
        e.get("amount", 0) for e in journal
        if e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )
```

Predicate matches the user's specification verbatim — `ref_type` exact match
AND substring "Cerberus Agnon" in `description`. The substring approach is
deliberate: the actual ESI description always wraps the name in a fuller
sentence ("Cerberus Agnon transferred cash from … to …"), so a substring is
robust to wording variations across entries. `description` is `None`-safe
via `or ""`.

### 3. In-memory PVP-income cache

A 5-minute TTL cache in `main.py` to avoid hitting the (possibly slow,
multi-page) journal endpoint on every Member-page load:

```python
_pvp_income_cache: dict[int, tuple[float, float]] = {}  # cid -> (cached_at, amount)
_PVP_INCOME_TTL = 300.0


async def _cached_pvp_income(character_id: int, access_token: str) -> float:
    now = time.time()
    hit = _pvp_income_cache.get(character_id)
    if hit and (now - hit[0]) < _PVP_INCOME_TTL:
        return hit[1]
    journal = await get_character_wallet_journal(character_id, access_token)
    amount = _sum_pvp_income(journal)
    _pvp_income_cache[character_id] = (now, amount)
    return amount
```

Mirrors the existing `_contributors_cache` pattern in main.py (per-key
`(cached_at, value)` tuple, in-memory only — fine because the Member page
reload pattern is "one user, multiple loads in a session" and per-process
state is enough). Cache lives only for the process lifetime.

### 4. `/api/member` integration

Add one more `_safe` coroutine to the existing `asyncio.gather`:

```python
(pvp_income, pvp_income_err) = await _safe(_cached_pvp_income(character_id, access_token))
```

(Folded into the existing gather block at main.py:1207–1218 — one more
return-tuple, parallel to the others.)

Return-dict at main.py:1289 grows by one wrapped field:

```python
"pvp_income": _wrap(pvp_income, pvp_income_err),
```

`pvp_income` is a `float` (ISK) or `None` if the section errored.

### 5. Frontend — `renderCharacterCard`

Keep the existing horizontal `.char-card` block (portrait + name +
zkill link + corp + sec status + online dot+label). Append a 2×2 metrics
grid beneath it using the existing `.metrics` / `.metric` / `.label` /
`.val` styling already defined for the other cards:

- **Wallet Balance** — uses the existing `walletDisplay()` helper for value +
  size class + tooltip; renders `<unit>ISK</unit>`. Shows `–` on error.
- **PVP Income (30d)** — same `walletDisplay()` helper applied to the new
  `m.pvp_income` value. Tooltip text:
  "Corp Account Withdrawal from Cerberus Agnon, last 30 days". Shows `–`
  on error.
- **Last Login** — `online.last_login` rendered via `toLocaleString()`.
  Shows `–` on error or when missing.
- **Total Logins** — `online.logins` rendered via `fmtNum()`. Shows `–` on
  error.

`walletDisplay()` already handles 0 by emitting "0" — we treat 0 as a valid
"no PVP payouts in 30d" result, not as an error.

### 6. Frontend — `renderFinancialsCard` → `renderSkillsCard`

Rename the function. Card title goes from "Financials & Skills" to "Skills".
Drop the Wallet metric, the Last Login footer, the Total Logins footer, and
the `m.online` dependency. Retain:

- Used Skillpoints
- Unused Skillpoints
- Total Skillpoints

Also update:
- `renderMemberSkeleton` (line ~3018): the skeleton card's title goes from
  "Financials & Skills" to "Skills".
- `renderMemberProfile` (line ~3050): call `renderSkillsCard(m)` in place of
  `renderFinancialsCard(m)`.

## Data Flow

```
GET /api/member
  -> asyncio.gather of per-section ESI fetches (existing) PLUS
     _safe(_cached_pvp_income(cid, token))
       -> cache hit (within 300s) -> sum
       -> cache miss -> get_character_wallet_journal() -> _sum_pvp_income()
            -> filter ref_type=corporation_account_withdrawal
               AND description contains "Cerberus Agnon"
            -> sum amount across all matches
            -> cache result, return
  -> response includes pvp_income wrapped via _wrap

Member page render:
  Character card  := portrait + identity row + 2x2 metrics
                      (Wallet Balance, PVP Income (30d), Last Login, Total Logins)
  Skills card     := 3 SP metrics
  (Attributes / Skill Queue / Skill Coverage cards unchanged)
```

## Error Handling

- Journal fetch fails (HTTP error or scope issue) → `_safe` returns
  `(None, error)`, so `m.pvp_income.error` is set and the front-end shows
  `–` for that single metric. Other Character-card metrics (wallet, last
  login, total logins) are unaffected.
- Empty journal (e.g. fresh character, no transactions in 30 days) → list
  comprehension yields 0. Cache stores 0; UI renders "0 ISK". This is
  correct behaviour, not an error.
- Cache lifetime ends with the process (no persistence). A deploy/restart
  forces a fresh fetch.

## Testing

- `esi.py`:
  - `get_character_wallet_journal` with a single-page response (X-Pages=1
    or missing) returns the list and stops.
  - `get_character_wallet_journal` with multiple pages walks pages until
    one returns empty, accumulating results.
- `main.py`:
  - `_sum_pvp_income` returns 0 for an empty list.
  - `_sum_pvp_income` includes only entries where BOTH conditions match;
    same-`ref_type` entries without "Cerberus Agnon" are excluded; entries
    that mention "Cerberus Agnon" but have a different `ref_type` are
    excluded.
  - `_sum_pvp_income` handles `description: None` without raising.
  - `/api/member` includes `pvp_income` with the expected value when the
    journal mock returns the user's example payload.
  - Cache hit: a second `/api/member` call within 300s does NOT trigger
    another journal HTTP request (respx mock with `assert_all_called=False`
    that would raise on an unmocked second request).
- Frontend: no JS test harness (project convention). Verified manually:
  - Character card shows portrait + identity + 2×2 metrics (Wallet, PVP
    Income, Last Login, Total Logins).
  - Skills card shows only the 3 SP metrics, titled "Skills".
  - Skeleton card during initial load also says "Skills".

## Scope

In scope: the new ESI helper, the filter/sum function, the in-memory cache,
the `/api/member` wiring, and the two render-function changes (plus the
skeleton title + the renderMemberProfile call-site update).

Out of scope:
- Persisting wallet journal entries to SQLite (deliberately rejected during
  brainstorming — last-30-days from ESI is the chosen window).
- All-time cumulative PVP income.
- A separate Wallet Journal UI view.
- Caching the full journal (we cache only the per-character sum — YAGNI;
  no other consumer of the journal exists today).
- Reworking adjacent cards (Attributes, Skill Queue, Skill Coverage).
- Hiding the PVP Income metric when 0 (we render "0 ISK" as a valid
  state, mirroring how Wallet Balance handles 0).
