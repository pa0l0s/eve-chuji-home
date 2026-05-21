# Cycling Background Images — Design Spec

**Date:** 2026-05-21
**Status:** Approved

## Goal

Give the Chuji Homepage a rotating set of EVE Online wallpaper backgrounds.
Each browser session shows one image, scaled to fill the viewport. The image
is stable for at least 5 minutes; once a session has been alive longer than
5 minutes, the next page load swaps in a fresh random image.

## Source Material

61 wallpaper images currently in `D:\Download\` (mix of `.jpg` and `.png`,
~33 MB total, mostly 1920×1080-class). Filenames are inconsistent and include
spaces and one duplicate (`wp8755431-eve-online-wallpapers (1).jpg`).

## Components

### 1. Image pipeline (one-off, build time)

A throwaway optimization step processes the 61 source files into
`static/backgrounds/`:

- Downscale to a maximum width of 1920px (preserve aspect ratio; never upscale).
- Recompress every image to JPG at roughly quality 80, stripping metadata.
- Output with clean sequential names: `bg-01.jpg` … `bg-61.jpg`
  (zero-padded, sorted by source filename for deterministic numbering).
- Tool: **`ffmpeg`** (already installed — no new dependency). Per-file:
  `ffmpeg -i <src> -vf "scale='min(1920,iw)':-2" -map_metadata -1 -q:v 5 <dst>`.

Expected result: ~33 MB → roughly 5–8 MB.

The conversion script is kept at `scripts/optimize_backgrounds.sh` for
reproducibility (re-runnable when new wallpapers are added). The optimized
`static/backgrounds/*.jpg` files are committed to git.

### 2. Backend — background list endpoint

A single FastAPI route in `main.py`, defined alongside the other `/api/` routes
(before the `StaticFiles` mount at line 1246):

```
GET /api/backgrounds  ->  ["bg-01.jpg", "bg-02.jpg", ...]
```

Implementation: list `static/backgrounds/`, filter to image files, return the
sorted filenames as JSON. ~8 lines. Returns `[]` if the folder is missing.

Rationale over a static manifest file: dropping new wallpapers into the folder
(after running the optimize script) requires no code change or regeneration.

The image files themselves are served by the existing `StaticFiles` mount at
`/backgrounds/bg-NN.jpg`.

### 3. Frontend — session selection & rendering

All changes live in `static/index.html` (CSS in `<style>`, logic in `<script>`).

**Storage:** one `sessionStorage` key `chuji-bg` holding JSON
`{ file: "bg-NN.jpg", chosenAt: <epoch-ms> }`. `sessionStorage` is per-tab and
cleared when the tab closes, so a brand-new tab always starts fresh.

**Selection logic, run once on page load:**

1. Read `chuji-bg` from `sessionStorage`.
2. If absent → fetch `/api/backgrounds`, pick a random filename, store it with
   `chosenAt = Date.now()`.
3. If present and `Date.now() - chosenAt < 5 min` → reuse `file` as-is.
4. If present and `Date.now() - chosenAt >= 5 min` → fetch `/api/backgrounds`,
   pick a new random filename, store it with `chosenAt = Date.now()`.

The 5-minute clock is measured from when the current image was chosen; it only
resets when the image actually changes, not on every reload. There is no
mid-session auto-rotation — the image changes only on a load/reload event.

**Rendering:** the chosen file is applied to `<body>` via a CSS custom property
`--bg-image`, using a layered `background` shorthand:

```css
body {
  background:
    linear-gradient(rgba(15,17,23,0.86), rgba(15,17,23,0.9)),  /* dark overlay  */
    var(--bg-image) center / cover no-repeat fixed,             /* fill viewport */
    #0f1117;                                                    /* fallback     */
}
```

- `cover` scales the image to fill the entire viewport in both dimensions.
- `fixed` keeps it filling the viewport while the page scrolls.
- The `linear-gradient` is the dark overlay that keeps card text readable; it is
  listed first so it paints on top of the image.
- `#0f1117` is the fallback if `--bg-image` is unset or the fetch fails.

A short opacity fade-in (~0.4s) on a body class avoids a flash when the image
loads. The image is preloaded via `new Image()` before `--bg-image` is set.

Cards remain opaque `#1e2130`; the design system is otherwise untouched. The
wallpaper shows through the page margins and the gaps between cards.

## Data Flow

```
page load
  -> read sessionStorage 'chuji-bg'
  -> (need new image?) fetch GET /api/backgrounds -> JSON filename list
  -> pick random filename, write sessionStorage with chosenAt
  -> preload /backgrounds/<file> via new Image()
  -> set CSS var --bg-image on <body>, add fade-in class
```

## Error Handling

- `/api/backgrounds` fetch fails or returns `[]` → skip setting `--bg-image`;
  body falls back to solid `#0f1117`. No error shown to the user.
- Malformed `chuji-bg` JSON in `sessionStorage` → treat as absent (re-select).
- Image fails to load → the gradient + `#0f1117` fallback still render.

## Deploy

`static/backgrounds/` is covered by the existing `rsync` in `deploy.sh` (no new
exclusion needed; `data/` remains the only data exclusion). No env var or
Portainer stack changes. The optimized image payload (~5–8 MB) is acceptable for
both git and the rsync sync.

## Testing

- `tests/` — add a test for `GET /api/backgrounds`: returns a JSON list, and
  every entry resolves to a real file under `static/backgrounds/`.
- Manual: load the page, confirm a wallpaper fills the viewport behind the dark
  overlay and cards stay readable; reload within 5 min → same image; reload
  after 5 min (or with `chosenAt` backdated) → new image; open a new tab →
  independent random image.

## Scope

In scope: the optimize script, `static/backgrounds/`, the `/api/backgrounds`
route, the CSS + JS in `index.html`, one backend test.

Out of scope: mid-session auto-rotation, per-page distinct backgrounds, a
user-facing wallpaper picker, WebP/AVIF formats, responsive image variants.
