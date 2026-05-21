# Project Modal: Contributor Pie + Jita Price History — Design Spec

**Date:** 2026-05-21
**Status:** Approved

## Goal

Two enhancements to the project detail modal (opened by clicking a project
card on the Projects page):

1. Replace the ranked contributor list with a contributor **pie chart** + a
   compact legend.
2. For projects that carry a reward + resolved item type, add a **Jita price
   history line chart** for that item (last 90 days).

## Context

- The modal is rendered by `renderContributors(project, contributors)` in
  `static/index.html`, opened via `openProjectContributors(projectId)` which
  fetches `GET /api/projects/{id}/contributors`.
- Charts in this project are hand-rolled inline SVG (see `renderSkillRadar`) —
  no charting library. The new charts follow the same approach.
- A project gains a `market` block — `{type_id, jita_buy_max, target_price,
  project_price, diff_ratio, needs_update}` — only when it is Active and a
  reward + item type + Jita buy price all resolve. `project.market.type_id` is
  the presence test for "has Jita price info".
- Contributor records have `{id, name, contributed, last_modified}`.
- ESI market history: `GET /markets/{region_id}/history/?type_id=...` — public,
  no auth, ~13 months of daily `{date, average, highest, lowest, order_count,
  volume}`. The Forge region id is `10000002` (`esi.FORGE_REGION_ID`).

## Component 1 — Contributor pie chart

Replaces the ranked contributor rows in `renderContributors`. The existing
modal header (progress bar, contributor count, delivered %, reward pool line)
is kept unchanged.

**Chart:** a hand-rolled SVG donut pie. Each contributor is a slice sized by
`contributed / totalContributed`, sorted largest-first. Each slice carries an
SVG `<title>` tooltip: `"<name>: <contributed> (<pct>%)"`.

**Legend:** a compact list under/beside the donut — colour swatch · name · `%`.
Portraits, zkill links, mini progress bars and last-contribution dates from the
old list are dropped.

**Colours:** a fixed palette of 10 distinct dark-theme-friendly colours.

**"Others" grouping:** if there are more than 10 contributors, contributors
ranked 10+ collapse into one grey "Others" slice (also shown in the legend), so
the donut and legend stay readable.

**Edge cases:** 0 contributors → the existing "No contributors yet." message
(unchanged); 1 contributor → a full single-colour ring.

## Component 2 — Jita price history chart

Shown only when `project.market.type_id` exists. Rendered below the pie inside
the same modal body.

**Chart:** a hand-rolled SVG line chart of the **last 90 days** of Jita market
price for the reward item:

- Daily `average` price line (accent `#38bdf8`).
- A faint high–low shaded band (`lowest`..`highest`) behind the line.
- A dashed horizontal reference line at `project.market.project_price` (the
  project's reward ISK-per-unit) — shows reward vs. market at a glance.
- Minimal axes: a few date ticks on X, a few ISK ticks on Y, in the muted
  label style used elsewhere (`#475569` / `#64748b`).
- Text labels for latest price, 90-day high, and 90-day low.

No per-point hover in this version (kept lean; the labels cover the key values).

## Backend

**`esi.py` — `get_market_history(type_id: int) -> list[dict]`:**
Calls `GET {ESI_BASE}/markets/{FORGE_REGION_ID}/history/?type_id={type_id}`.
Returns the raw daily records (list of `{date, average, highest, lowest,
order_count, volume}`), sorted ascending by `date`. On `httpx.HTTPError`
(includes a 404 for an untraded type) returns `[]`.

**`main.py` — `GET /api/market/{type_id}/history`:**
Session-protected (consistent with `/api/projects/*`; returns 401 without a
valid session). Returns JSON `{"type_id": <id>, "history": [...]}` where
`history` is the **last 90 daily records** (`average`, `highest`, `lowest`,
`date` per entry). Backed by an in-memory cache `_market_history_cache:
dict[int, tuple[float, list]]` with a ~6 h TTL, mirroring the existing
`_jita_price_cache` pattern (history updates at most once per day).

## Data Flow

```
click project card
  -> openProjectContributors(projectId)
  -> GET /api/projects/{id}/contributors
  -> render modal: header + contributor pie + legend          (immediate)
  -> if project.market?.type_id:
       GET /api/market/{type_id}/history
       -> inject price-history chart below the pie            (deferred)
```

## Error Handling

- `/api/market/{type_id}/history` fetch fails or returns empty → the price
  chart area shows a small muted "Price history unavailable" note; the pie and
  the rest of the modal are unaffected.
- `get_market_history` ESI failure → returns `[]` (best-effort, same philosophy
  as the project market enrichment).
- A project with no `market` block → no price chart section at all.

## Testing

- `tests/` — `respx`-mocked test for `get_market_history` (returns sorted
  records; `[]` on HTTP error). Endpoint test for `GET /api/market/{type_id}/
  history`: 401 without session; with session returns the `{type_id, history}`
  shape, history capped at 90 entries; second call served from cache.
- Frontend: no JS test harness (project convention) — verified manually:
  open a project modal, confirm the donut + legend render and that a project
  with a reward shows the price chart with the reward reference line.

## Scope

In scope: the contributor pie + legend, the 90-day price history chart, the
`get_market_history` function, the `/api/market/{type_id}/history` endpoint and
its cache, the two backend tests.

Out of scope: price history for closed/non-market projects, per-point chart
hover tooltips, a price chart on the project cards themselves (modal only),
volume/order-count visualisation, configurable time ranges.
