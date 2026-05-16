# Changing the Public Domain

How to move the site from one public domain to another (e.g. `chuji.swoojeff.online` → `chuji.pl`).
Written for both humans and agents. **Read the "Env values" section — it is the part most often gotten wrong.**

`OLD` = the current domain, `NEW` = the domain you are switching to.

---

## TL;DR checklist

- [ ] 1. Replace `OLD` → `NEW` in all repo files
- [ ] 2. Update `EVE_CALLBACK_URL` in `.env` and `.env.example`
- [ ] 3. Update the callback URL on the EVE Developer portal (manual, browser)
- [ ] 4. DNS: point `NEW` at the public IP
- [ ] 5. Nginx Proxy Manager: add proxy host `NEW` → `192.168.0.24:8760` + issue TLS cert
- [ ] 6. `./deploy.sh` (syncs files + pushes env into the Portainer stack + verifies)
- [ ] 7. Update the EVE SSO memory file
- [ ] 8. Verify login end-to-end at `https://NEW`

Steps 1–2, 6, 7 are agent-doable. Steps 3, 4, 5 require a human with portal/router/NPM access.

---

## Env values (READ THIS)

The only env value tied to the domain is **`EVE_CALLBACK_URL`** — the EVE SSO OAuth2 redirect URI. It must be **byte-identical** in three places or login breaks with an SSO error:

1. **`.env`** (local repo, gitignored) — `EVE_CALLBACK_URL=https://NEW/api/auth/callback`
2. **`.env.example`** (committed template) — same value, keeps the template honest
3. **EVE Developer portal** — the app's registered Callback URL (manual step 3)

**Critical gotcha:** the Portainer stack (`eve-chuji-homepage`, id 110) keeps its **own copy** of the env vars in its stack `Env` array, and that copy **overrides** the NAS `.env`. Editing `.env` and restarting the container is **not enough** — the container will keep serving the old callback URL.

`./deploy.sh` handles this correctly: it reads local `.env` and pushes the whole set into the Portainer stack via `PUT /api/stacks/110`, so **local `.env` is the single source of truth**. If you ever change env by hand, mirror it in the stack or just run `./deploy.sh`.

No other env var (`EVE_CLIENT_ID`, `EVE_CLIENT_SECRET`, `SECRET_KEY`, `CORP_ID`, `DATABASE_URL`, `JANICE_LINK_ID`, `ADMIN_CHARACTER_IDS`, `HOLDING_CORPORATION_IDS`) depends on the domain — leave them unchanged.

---

## Step 1 — Replace the domain in all repo files

Agent / shell:

```bash
grep -rl 'OLD' . 2>/dev/null | grep -v '/.git/' | \
  while read -r f; do sed -i 's/OLD/NEW/g' "$f"; done
# .env is hidden — confirm it too:
sed -i 's/OLD/NEW/g' .env
grep -rn 'OLD' . 2>/dev/null | grep -v '/.git/' || echo "clean"
```

Files this typically touches: `.env`, `.env.example`, `CLAUDE.md`, `tests/conftest.py`,
`docs/superpowers/specs/*`, `docs/superpowers/plans/*`. App code reads `EVE_CALLBACK_URL`
from the environment (`auth.py`) — no domain is hardcoded in Python.

## Step 2 — Confirm env files

```bash
grep EVE_CALLBACK_URL .env .env.example   # both must read https://NEW/api/auth/callback
```

## Step 3 — EVE Developer portal (HUMAN, browser)

1. Go to <https://developers.eveonline.com> → Applications → **Chuji Homepage**.
2. Set **Callback URL** to `https://NEW/api/auth/callback` (exact, no trailing slash).
3. Save. (Client ID / secret / scopes stay the same.)

## Step 4 — DNS (HUMAN)

Point `NEW` (A record) at the site's public IP — the same WAN address that already
forwards to the LAN host `192.168.0.24`. Wait for it to resolve.

## Step 5 — Nginx Proxy Manager (HUMAN)

1. Add a Proxy Host for `NEW` → forward to `192.168.0.24:8760` (scheme `http`).
2. Under SSL, request a new Let's Encrypt certificate for `NEW`, force SSL.
3. Optionally remove the old `OLD` proxy host once `NEW` is confirmed working.

## Step 6 — Deploy

```bash
./deploy.sh            # rsync + stack env sync + redeploy + verify
# ./deploy.sh --dry-run   # preview the rsync without changing anything
```

The script prints the container's `EVE_CALLBACK_URL` and the `https://NEW` HTTP status;
it exits non-zero if the site is not 200.

## Step 7 — Update memory

Update the EVE SSO memory file's **Callback URL** line to the new value
(`~/.claude/projects/<project>/memory/project_eve_sso.md`).

## Step 8 — Verify login end-to-end

Open `https://NEW`, click login, complete EVE SSO, confirm redirect back succeeds
and you land authenticated. An "Invalid redirect_uri" error means step 3 or the
Portainer stack env (step 6 / Env gotcha) is out of sync.
