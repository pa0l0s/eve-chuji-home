# Mail Page + Unread Badge — Design Spec

**Date:** 2026-05-30
**Status:** Approved (pending written-spec review)

## Goal

Add an EVE Mail feature to the homepage:

1. **Task 1** — A "Mail" page reachable from the navbar, implementing all character mail
   actions from the ESI mail API: read inbox / folders / labels / mailing lists, open a
   mail, mark read/unread, set labels, create/delete labels, delete mail, and compose &
   send mail. Layout: **3-pane master-detail** (folders/labels/lists | message list |
   reading pane), with a Compose modal.
2. **Task 2** — A small **mail icon in the navbar** that lights up when there is unread
   mail in the **Inbox**, showing the unread count in a red badge. Hidden/dim when zero.

## Decisions (locked)

- Layout: 3-pane master-detail. Compose is a modal reusing the existing modal pattern.
- Actions: full read + send + organize + delete.
- Badge counts **Inbox unread only** (ESI `mail/labels` → label `Inbox` `unread_count`),
  **not** `total_unread_count` (which includes `[Corp]`, `[Alliance]`).
- No new DB tables. Mail is fetched live from ESI (ESI caches it server-side). Mail bodies
  are immutable, cached in-memory per process keyed by `(character_id, mail_id)`.
- Keep the existing `ESI_BASE = "https://esi.evetech.net/latest"` convention — verified the
  `/latest` paths return HTTP 200 for all mail endpoints; no compatibility-date header needed.

## Prerequisite: new OAuth scopes (requires re-consent)

Add to `auth.py:SCOPES`:

- `esi-mail.read_mail.v1`
- `esi-mail.organize_mail.v1`
- `esi-mail.send_mail.v1`

**Every existing user must log out and log back in** to grant the new scopes before mail
works. The frontend detects the missing scope (ESI 403) and shows a clear "log out and back
in to enable Mail" message rather than a generic error.

## Verified ESI response shapes

- `GET /characters/{cid}/mail/` → `[{from, labels:[id], mail_id, recipients:[{recipient_id,
  recipient_type}], subject, timestamp, is_read?}]`. `is_read` is **present only when read**;
  absent ⇒ unread. Paginated via `last_mail_id` query param (returns ≤50, older than the id).
- `GET /characters/{cid}/mail/{mail_id}/` → `{body, from, labels:[id], recipients, subject,
  timestamp}`. Body is EVE markup (`<font color>`, `<a href="killReport:...">`,
  `<a href="showinfo:...">`, `<a href="http...">`, `<loc>`).
- `PUT /characters/{cid}/mail/{mail_id}/` body `{read?:bool, labels?:[id]}` → 204.
- `DELETE /characters/{cid}/mail/{mail_id}/` → 204.
- `GET /characters/{cid}/mail/labels/` → `{labels:[{color, label_id, name, unread_count?}],
  total_unread_count}`. Inbox=`label_id 1`, Sent=`2`, `[Corp]`=`4`, `[Alliance]`=`8`.
- `POST /characters/{cid}/mail/labels/` body `{name, color}` → new `label_id`.
- `DELETE /characters/{cid}/mail/labels/{label_id}/` → 204.
- `GET /characters/{cid}/mail/lists/` → `[{mailing_list_id, name}]` (may be empty).
- `POST /characters/{cid}/mail/` body `{recipients:[{recipient_id, recipient_type}], subject,
  body, approved_cost?}` → new `mail_id`. CSPA/cost errors return 520/400 with detail.
- `POST /universe/ids` body `[names]` → `{characters:[{id,name}], corporations:[...],
  alliances:[...]}` (recipient resolution for compose).
- `POST /universe/names` body `[ids]` → `[{id, name, category}]` (resolve sender/recipient
  names for display).

## Backend

### `esi.py` — new thin async wrappers (mirror existing style/error propagation)

- `get_mail_headers(cid, token, labels=None, last_mail_id=None) -> list[dict]`
- `get_mail(cid, token, mail_id) -> dict`
- `set_mail(cid, token, mail_id, read=None, labels=None) -> None`  (PUT)
- `delete_mail(cid, token, mail_id) -> None`
- `get_mail_labels(cid, token) -> dict`
- `create_mail_label(cid, token, name, color) -> int`
- `delete_mail_label(cid, token, label_id) -> None`
- `get_mailing_lists(cid, token) -> list[dict]`
- `send_mail(cid, token, recipients, subject, body, approved_cost=0) -> int`
- `resolve_ids_from_names(token, names) -> dict`  (POST /universe/ids)
- Reuse/extend existing name-resolution helper for `POST /universe/names` if one exists;
  otherwise add `resolve_names_from_ids(ids) -> list[dict]`.

### `main.py` — new `/api/me/mail/...` routes

Each route: resolve session → `get_valid_token(character_id)` → call helper → established
403/502 handling (403 ⇒ "scope" hint, 502 ⇒ ESI unavailable).

- `GET  /api/me/mail` — query `labels`, `last_mail_id`; returns headers **enriched** with
  resolved `from_name` (and recipient names) via a batched `POST /universe/names`, so the
  frontend doesn't N+1.
- `GET  /api/me/mail/{mail_id}` — full body + resolved names. In-memory body cache.
- `PUT  /api/me/mail/{mail_id}` — body `{read?, labels?}`.
- `DELETE /api/me/mail/{mail_id}`.
- `GET  /api/me/mail/labels` — labels + lists combined (one call for the left pane), shaped
  `{labels:[...], lists:[...], total_unread_count}`.
- `POST /api/me/mail/labels` — `{name, color}`.
- `DELETE /api/me/mail/labels/{label_id}`.
- `POST /api/me/mail` — compose `{recipients, subject, body, approved_cost?}`.
- `POST /api/me/mail/resolve` — `{names:[...]}` → recipient candidates for compose search.
- `GET  /api/me/mail/unread` — lightweight: returns `{inbox_unread:int}` from the Inbox
  label's `unread_count`. Used by the badge poller.

## Frontend (`static/index.html`)

### Navbar

- New **mail icon button** `btn-mail` (inline SVG envelope) → `showView('mail')`, added to
  the `showView` view list and the nav-links row.
- A red badge `<span id="mail-badge">` absolutely positioned over the icon; text = inbox
  unread; `hidden` when 0; icon brightens (accent `#38bdf8`) when unread > 0, muted
  otherwise.
- `loadMailBadge()` polls `GET /api/me/mail/unread` every 60s, **pausing when tab hidden**
  (same visibility pattern as contracts) and runs regardless of the current view. Called
  once on login. Re-fetched after any read/unread/delete/send action that changes inbox
  unread, for immediacy.

### `#view-mail` (3-pane CSS grid)

Styled with existing dark-theme tokens (`#1e2130` surface, `#2d3348` border, 12px radius,
`#38bdf8` accent).

- **Left pane** — folders Inbox / Sent / `[Corp]` / `[Alliance]` (+ custom labels), each
  with unread count; "Labels" management (add via name+color, delete custom labels);
  "Mailing Lists" section. Selecting an entry loads the middle pane.
- **Middle pane** — message headers for the selection. Unread rows bold + dot. Columns:
  from, subject, date. "Load more" via `last_mail_id`. Selecting a row loads the right pane
  and marks it read (PUT) optimistically.
- **Right pane** — from / subject / timestamp + sanitized body. Toolbar: Reply (prefill
  compose), Mark unread, Set labels (multi-select chips), Delete (confirm).
- **Compose modal** — recipient chip input (type → `POST /api/me/mail/resolve` →
  pick character/corp/alliance/mailing-list), subject, body, Send. Surfaces ESI error
  detail (e.g. CSPA cost) on failure.

### Body sanitization

EVE mail body is rendered through a small client-side sanitizer:

- Allow: text, `<br>`, `<b>/<i>/<u>`, `<font color>` → mapped to a `<span style="color">`
  (ARGB `#aarrggbb` → `#rrggbb`).
- `<a href="http(s):...">` → real anchor, `target="_blank" rel="noopener"`.
- `<a href="killReport:...">` / `<a href="showinfo:...">` → non-clickable styled span
  (client-only links). `<loc>` unwrapped. Everything else stripped. No raw HTML injection
  (build via DOM, not `innerHTML` of untrusted content).

## Error handling

- Per-pane error banners (reusing `.banner` style). 403 → scope hint. 502 → ESI unavailable.
  Network error → generic retry message.
- Compose/Send failures show the ESI `error` detail string.
- Badge poll failures are silent (keep last known count); never block the UI.

## Testing (`pytest` + `respx`, matching existing tests)

- `tests/test_mail.py`:
  - `GET /api/me/mail` happy path (respx-mocked headers + `/universe/names`), 401 (no auth),
    403 (missing scope → ESI 403), 502 (ESI down).
  - `GET /api/me/mail/{id}` body returned + name resolution; in-memory cache hit (single ESI
    call on second request).
  - `PUT` read/labels → 204 passthrough; `DELETE` → success.
  - `GET /api/me/mail/labels` combined labels+lists shape.
  - `POST /api/me/mail/labels` + `DELETE label`.
  - `POST /api/me/mail` send: payload shaping (recipients/subject/body), ESI error
    propagation.
  - `GET /api/me/mail/unread` returns Inbox `unread_count` (and 0 when label absent).
  - `POST /api/me/mail/resolve` maps names → recipient candidates.
- `tests/test_esi.py` additions for new wrappers (pagination via `last_mail_id`,
  204-returning PUT/DELETE).

## Out of scope (YAGNI)

- Persisting mail to SQLite / offline history.
- Corp/alliance mail beyond what the character's own mail labels expose.
- Rich compose formatting (color/links) — plain-text body for sending.
- Attachments / contract/fitting embeds in compose.
