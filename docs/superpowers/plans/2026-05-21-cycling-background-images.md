# Cycling Background Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Chuji Homepage a per-session EVE wallpaper background that scales to fill the viewport and re-rolls once a session has been alive longer than 5 minutes.

**Architecture:** A one-off `ffmpeg` script optimises 61 source wallpapers into `static/backgrounds/`. A tiny FastAPI route lists that folder as JSON. Frontend JS picks a random wallpaper, stores the choice + timestamp in `sessionStorage`, and renders it on a fixed `body::before` layer behind a dark overlay.

**Tech Stack:** FastAPI (Python), `ffmpeg`, plain HTML/CSS/JS in `static/index.html`, `pytest` + `httpx.AsyncClient`.

---

## Preconditions

- `ffmpeg` must be installed (`command -v ffmpeg` — confirmed available).
- The 61 source files exist in `/mnt/d/Download/` (confirmed).
- **Working-tree note:** `main.py` and `static/index.html` already have unrelated uncommitted changes from prior work. The commit steps below `git add` only the specific files for each task; those pre-existing changes will be carried along in the Task 2 / Task 3 commits. If that is not desired, stash them (`git stash`) before starting, or commit them separately first.

## File Structure

- **Create** `scripts/optimize_backgrounds.sh` — re-runnable image optimisation script.
- **Create** `static/backgrounds/bg-01.jpg` … `bg-61.jpg` — optimised wallpapers (generated, committed).
- **Modify** `main.py` — add `BACKGROUNDS_DIR` constant + `GET /api/backgrounds` route.
- **Create** `tests/test_backgrounds.py` — endpoint test.
- **Modify** `static/index.html` — `body::before` CSS layer + session-selection `<script>`.

---

## Task 1: Generate optimised background images

**Files:**
- Create: `scripts/optimize_backgrounds.sh`
- Create: `static/backgrounds/bg-01.jpg` … `static/backgrounds/bg-61.jpg` (script output)

- [ ] **Step 1: Create the optimisation script**

Create `scripts/optimize_backgrounds.sh` with exactly this content:

```bash
#!/usr/bin/env bash
# One-off (re-runnable): optimise EVE wallpaper sources into static/backgrounds/bg-NN.jpg
# Requires ffmpeg. Run from anywhere:  ./scripts/optimize_backgrounds.sh
set -euo pipefail

SRC="/mnt/d/Download"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/static/backgrounds"

SOURCES=(
  "Desktop_1920x1080.jpg"
  "wUKReBj-eve-online-backgrounds.jpg"
  "skwEkVA-eve-online-wallpapers.jpg"
  "V7Q54ro-eve-online-wallpapers.jpg"
  "0Pn5YJb-eve-online-wallpapers.jpg"
  "UlyK49y-eve-online-wallpapers.jpg"
  "ZmDyMUQ-eve-online-wallpaper.jpg"
  "umfcZTa-eve-online-backgrounds.jpg"
  "5QcLo52-eve-online-backgrounds.jpg"
  "qbz4Txu-eve-online-wallpaper.jpg"
  "wp8755526-eve-online-wallpapers.jpg"
  "wp8755517-eve-online-wallpapers.jpg"
  "wp8755511-eve-online-wallpapers.jpg"
  "wp8755507-eve-online-wallpapers.jpg"
  "wp8755499-eve-online-wallpapers.jpg"
  "wp8755481-eve-online-wallpapers.jpg"
  "wp8755470-eve-online-wallpapers.jpg"
  "wp8755469-eve-online-wallpapers.jpg"
  "wp8755468-eve-online-wallpapers.jpg"
  "wp8755467-eve-online-wallpapers.jpg"
  "wp8755466-eve-online-wallpapers.jpg"
  "wp2006066-anathema-wallpapers.jpg"
  "wp8755464-eve-online-wallpapers.jpg"
  "wp8755463-eve-online-wallpapers.png"
  "wp8755462-eve-online-wallpapers.jpg"
  "wp8755461-eve-online-wallpapers.png"
  "wp8755459-eve-online-wallpapers.jpg"
  "wp8755457-eve-online-wallpapers.jpg"
  "wp8755456-eve-online-wallpapers.jpg"
  "wp8755455-eve-online-wallpapers.jpg"
  "wp8755454-eve-online-wallpapers.jpg"
  "wp8755453-eve-online-wallpapers.jpg"
  "wp8755449-eve-online-wallpapers.jpg"
  "wp8755446-eve-online-wallpapers.jpg"
  "xVtADSP-eve-online-backgrounds.jpg"
  "wp8755445-eve-online-wallpapers.png"
  "wp8755444-eve-online-wallpapers.jpg"
  "wp8755439-eve-online-wallpapers.jpg"
  "wp8755437-eve-online-wallpapers.jpg"
  "wp8755434-eve-online-wallpapers.jpg"
  "wp8755433-eve-online-wallpapers.jpg"
  "wp8755432-eve-online-wallpapers.jpg"
  "wp8755431-eve-online-wallpapers (1).jpg"
  "wp8755431-eve-online-wallpapers.jpg"
  "wp8755429-eve-online-wallpapers.jpg"
  "wp8755428-eve-online-wallpapers.jpg"
  "oVFi7Bf-eve-online-backgrounds.jpg"
  "wp8755423-eve-online-wallpapers.jpg"
  "wp8755412-eve-online-wallpapers.jpg"
  "wp8755411-eve-online-wallpapers.jpg"
  "wp8755410-eve-online-wallpapers.jpg"
  "6lJPB1t-eve-online-backgrounds.jpg"
  "wp8755409-eve-online-wallpapers.jpg"
  "background.jpg"
  "10-Screenshot_Copy_1920x1080.jpg"
  "12-Screenshot_Copy_1920x1080.jpg"
  "4-Screenshot_Copy_1920x1080.jpg"
  "14-Screenshot_Copy_1920x1080.jpg"
  "15-Screenshot_Copy_1920x1080.jpg"
  "16-Screenshot_Copy_1920x1080.jpg"
  "13-Screenshot_Copy_1920x1080.jpg"
)

mkdir -p "$DEST"
rm -f "$DEST"/bg-*.jpg

# Sort for deterministic bg-NN numbering, independent of array order.
mapfile -t SORTED < <(printf '%s\n' "${SOURCES[@]}" | LC_ALL=C sort)

i=1
for f in "${SORTED[@]}"; do
  if [[ ! -f "$SRC/$f" ]]; then
    echo "ERROR: missing source: $SRC/$f" >&2
    exit 1
  fi
  out=$(printf '%s/bg-%02d.jpg' "$DEST" "$i")
  ffmpeg -y -loglevel error -i "$SRC/$f" \
    -vf "scale='min(1920,iw)':-2" -map_metadata -1 -pix_fmt yuvj420p -q:v 5 "$out"
  echo "  $f -> bg-$(printf '%02d' "$i").jpg"
  i=$((i + 1))
done

echo "Done: $((i - 1)) images -> $DEST"
du -sh "$DEST"
```

- [ ] **Step 2: Make the script executable and run it**

Run:
```bash
chmod +x scripts/optimize_backgrounds.sh
./scripts/optimize_backgrounds.sh
```
Expected: 60 lines of `<source> -> bg-NN.jpg`, then `Done: 60 images -> .../static/backgrounds` and a `du -sh` total (roughly 5–15 MB). (60, not 61: `wp8755431-eve-online-wallpapers (1).jpg` is a byte-identical duplicate and is excluded from `SOURCES`.)

- [ ] **Step 3: Verify the output**

Run:
```bash
ls static/backgrounds/ | wc -l
ls static/backgrounds/ | head -1
ls static/backgrounds/ | tail -1
```
Expected: `60`, then `bg-01.jpg`, then `bg-60.jpg`.

- [ ] **Step 4: Commit**

```bash
git add scripts/optimize_backgrounds.sh static/backgrounds/
git commit -m "$(cat <<'EOF'
feat: optimised EVE wallpaper backgrounds + build script

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add the `/api/backgrounds` endpoint

**Files:**
- Modify: `main.py` (add a constant after `app = FastAPI(...)` near line 49; add a route after the `/api/status` handler)
- Test: `tests/test_backgrounds.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backgrounds.py` with exactly this content:

```python
import os

import pytest

from db import init_db

BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "backgrounds")


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def test_backgrounds_returns_json_list(client):
    r = await client.get("/api/backgrounds")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 60


async def test_backgrounds_entries_are_real_image_files(client):
    r = await client.get("/api/backgrounds")
    for name in r.json():
        assert name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        assert os.path.isfile(os.path.join(BACKGROUNDS_DIR, name))


async def test_backgrounds_list_is_sorted(client):
    r = await client.get("/api/backgrounds")
    data = r.json()
    assert data == sorted(data)
```

The `client` fixture is provided by `tests/conftest.py` (an `httpx.AsyncClient` over the ASGI app).

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_backgrounds.py -v`
Expected: FAIL — the route does not exist yet, so the mounted SPA catch-all returns the `index.html` page; `r.json()` raises a JSON decode error or the status assertion fails.

- [ ] **Step 3: Add the constant in `main.py`**

In `main.py`, immediately after the line `app = FastAPI(lifespan=lifespan)` (near line 49), add:

```python
BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "static", "backgrounds")
_BG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
```

(`os` is already imported at the top of `main.py`.)

- [ ] **Step 4: Add the route in `main.py`**

In `main.py`, immediately after the end of the `/api/status` route handler (the function decorated with `@app.get("/api/status")`, which begins near line 89) and before `@app.get("/api/auth/login")`, add:

```python
@app.get("/api/backgrounds")
async def list_backgrounds():
    """Return the sorted list of available background image filenames."""
    try:
        names = os.listdir(BACKGROUNDS_DIR)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if n.lower().endswith(_BG_EXTS))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_backgrounds.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 6: Run the full test suite**

Run: `pytest`
Expected: all tests pass (no regressions in the existing suite).

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_backgrounds.py
git commit -m "$(cat <<'EOF'
feat: add /api/backgrounds endpoint listing wallpaper files

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Render the cycling background in the frontend

**Files:**
- Modify: `static/index.html` (add CSS after the `body { ... }` rule near line 24; add a `<script>` block just before `</body>`)

No automated test — `static/index.html` is plain HTML/JS with no test harness. Verified manually in Step 3.

- [ ] **Step 1: Add the background CSS**

In `static/index.html`, immediately after the closing `}` of the existing `body { ... }` rule (the rule ends at line 24, just before the `/* ── Nav ── */` comment), add:

```css
    /* ── Cycling background image ── */
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      z-index: -1;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(15, 17, 23, 0.86), rgba(15, 17, 23, 0.92)),
        var(--bg-image, none);
      background-size: cover;
      background-position: center;
      background-repeat: no-repeat;
      opacity: 0;
      transition: opacity 0.4s ease;
    }
    body.bg-ready::before { opacity: 1; }
```

This fixed, full-viewport layer sits behind all content (`z-index: -1`) and above the body's solid `#0f1117`. `background-size: cover` scales the wallpaper to fill the viewport; the `linear-gradient` is the dark overlay (listed first → painted on top of the image) that keeps card text readable. The layer fades in once JS adds `bg-ready`. When `--bg-image` is unset it resolves to `none`, so the page looks unchanged until an image is applied.

- [ ] **Step 2: Add the session-selection script**

In `static/index.html`, immediately before the closing `</body>` tag, add:

```html
  <script>
  /* Cycling per-session background image. */
  (function () {
    var KEY = 'chuji-bg';
    var MAX_AGE = 5 * 60 * 1000; // 5 minutes

    function apply(file) {
      var img = new Image();
      img.onload = function () {
        document.body.style.setProperty('--bg-image', "url('/backgrounds/" + file + "')");
        document.body.classList.add('bg-ready');
      };
      img.src = '/backgrounds/' + file;
    }

    function pickAndApply() {
      fetch('/api/backgrounds')
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (list) {
          if (!Array.isArray(list) || list.length === 0) return;
          var file = list[Math.floor(Math.random() * list.length)];
          sessionStorage.setItem(KEY, JSON.stringify({ file: file, chosenAt: Date.now() }));
          apply(file);
        })
        .catch(function () { /* no background on failure */ });
    }

    var stored = null;
    try { stored = JSON.parse(sessionStorage.getItem(KEY)); } catch (e) { stored = null; }

    if (stored && stored.file && typeof stored.chosenAt === 'number'
        && Date.now() - stored.chosenAt < MAX_AGE) {
      apply(stored.file);            // reuse: image is less than 5 min old
    } else {
      pickAndApply();                // new session, or 5+ min old -> re-roll
    }
  })();
  </script>
```

- [ ] **Step 3: Manual verification**

Run the app:
```bash
uvicorn main:app --reload --port 8000
```
Open `http://localhost:8000` and confirm:
1. A wallpaper fills the entire viewport behind the dark overlay; cards and text remain clearly readable.
2. DevTools console: `sessionStorage.getItem('chuji-bg')` returns JSON with `file` and `chosenAt`.
3. Reload (F5) within 5 minutes → the **same** wallpaper appears.
4. In the console, backdate the timestamp and reload:
   ```js
   var s = JSON.parse(sessionStorage.getItem('chuji-bg'));
   s.chosenAt -= 6 * 60 * 1000;
   sessionStorage.setItem('chuji-bg', JSON.stringify(s));
   ```
   Then reload → a **different** wallpaper appears (re-rolled).
5. Open the site in a new tab → an independent random wallpaper (separate `sessionStorage`).

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: cycling per-session wallpaper background

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Out of Scope / Follow-up

- Deployment: the user runs `./deploy.sh` separately; `static/backgrounds/` is already covered by the existing rsync (no exclusion change needed).
- Not included: mid-session auto-rotation, per-page distinct backgrounds, a user-facing wallpaper picker, WebP/AVIF variants, responsive image sizes.

## Self-Review

- **Spec coverage:** image pipeline → Task 1; `/api/backgrounds` route → Task 2; sessionStorage selection with the 5-minute rule → Task 3 Step 2; layered `cover` background + dark overlay + fade-in → Task 3 Step 1; error handling (empty list / failed fetch / bad JSON) → Task 3 Step 2 logic; backend test → Task 2; deploy note → Out of Scope. All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step contains complete content.
- **Type consistency:** `BACKGROUNDS_DIR` / `_BG_EXTS` defined in Task 2 Step 3 and used in Step 4; the `sessionStorage` shape `{ file, chosenAt }` is written and read consistently in Task 3 Step 2; `--bg-image` CSS var set by JS matches the `var(--bg-image, none)` consumer in Task 3 Step 1; `bg-ready` class added by JS matches the `body.bg-ready::before` selector.
