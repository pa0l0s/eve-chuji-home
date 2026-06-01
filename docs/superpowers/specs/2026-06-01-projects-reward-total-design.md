# Projects Page — Total Reward Pool — Design Spec

**Date:** 2026-06-01
**Status:** Approved

## Goal

On the Projects page, show the **sum of reward pools across all active projects**: the
current (remaining) total and the on-project-start (initial) total, in the form
`Total reward pool: <Σ remaining> / <Σ initial> ISK`.

## Decision

- **Scope:** Active tab only (the projects with live reward pools). The Closed pane is
  unchanged.
- Frontend-only. Both fields already arrive from `/api/projects`: each project has
  `reward.remaining` (current) and `reward.initial` (on start). No backend change.

## Implementation (`static/index.html`, `renderProjects`)

- Compute over `active`:
  - `sumRemaining = active.reduce((s, p) => s + (p.reward?.remaining || 0), 0)`
  - `sumInitial   = active.reduce((s, p) => s + (p.reward?.initial   || 0), 0)`
- Prepend a summary bar to `#pane-active` (above the existing market-error notice and the
  project list): `Total reward pool: <fmtISK(sumRemaining)> / <fmtISK(sumInitial)> ISK`,
  using `fmtISK` and the existing dark-theme tokens (label muted `#64748b`/uppercase,
  values accent `#38bdf8`, matching the per-card "Reward Pool" style). New CSS class
  `.proj-reward-total`.
- Shown only when there is at least one active project (skip the bar when `active` is empty,
  so the "No active projects." message stands alone).

## Error handling

None beyond existing — purely derived from already-loaded data; missing `reward` on a
project contributes 0 via the `|| 0` guards.

## Testing

No JS test harness in this repo. Verify by server-start smoke check and manual inspection
of the Active pane total. (The summed fields are already rendered per-card, so correctness
is visually checkable.)

## Out of scope (YAGNI)

- Closed-tab total, combined header total, per-tab toggle.
- Item-reward projects (rewards here are ISK pools via `reward.initial/remaining`).
