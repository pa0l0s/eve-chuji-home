#!/usr/bin/env bash
# Deploy eve-chuji-homepage: rsync working tree to NAS, sync env into the
# Portainer stack (the stack Env array OVERRIDES the NAS .env — see CLAUDE.md),
# redeploy, and verify. Idempotent. Run from the repo root.
#
#   ./deploy.sh            full deploy
#   ./deploy.sh --dry-run  rsync preview only, no changes
set -euo pipefail

NAS_HOST="paolo@nasty"
NAS_PATH="/srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage"
PORTAINER="http://192.168.0.24:9000/api"
API_KEY="${PORTAINER_API_KEY:-ptr_7pN8chEVqg4qTohwXoWbmEuRdzO2kHBK7rGx3ORUo8Y=}"
STACK_ID=110
ENDPOINT_ID=2
PUBLIC_URL="https://chuji.pl"
DRY_RUN="${1:-}"

RSYNC_EXCLUDES=(--exclude '.git/' --exclude 'venv/' --exclude '__pycache__/'
  --exclude '*.pyc' --exclude '.pytest_cache/' --exclude 'data/' --exclude '*.db'
  --exclude '.claude/' --exclude 'node_modules/')

echo "==> rsync to ${NAS_HOST}:${NAS_PATH}"
if [[ "$DRY_RUN" == "--dry-run" ]]; then
  rsync -azn --delete "${RSYNC_EXCLUDES[@]}" --itemize-changes \
    ./ "${NAS_HOST}:${NAS_PATH}/"
  echo "==> dry-run only, stopping."
  exit 0
fi
rsync -az --delete "${RSYNC_EXCLUDES[@]}" --itemize-changes \
  ./ "${NAS_HOST}:${NAS_PATH}/"

echo "==> sync local .env into Portainer stack ${STACK_ID} and redeploy"
PORTAINER="$PORTAINER" API_KEY="$API_KEY" STACK_ID="$STACK_ID" \
ENDPOINT_ID="$ENDPOINT_ID" PUBLIC_URL="$PUBLIC_URL" python3 - <<'PY'
import json, os, re, urllib.request

B   = os.environ["PORTAINER"]
K   = os.environ["API_KEY"]
SID = os.environ["STACK_ID"]
EID = os.environ["ENDPOINT_ID"]
URL = os.environ["PUBLIC_URL"]

def req(method, path, body=None):
    r = urllib.request.Request(B + path, method=method,
        headers={"X-API-Key": K, "Content-Type": "application/json"})
    if body is not None:
        r.data = json.dumps(body).encode()
    return json.load(urllib.request.urlopen(r, timeout=60))

# local .env is the single source of truth for env vars
env = []
with open(".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.append({"name": k.strip(), "value": v.strip()})

filec = req("GET", f"/stacks/{SID}/file")["StackFileContent"]
res = req("PUT", f"/stacks/{SID}?endpointId={EID}",
          {"stackFileContent": filec, "env": env,
           "prune": False, "pullImage": False})
print("   stack status:", res.get("Status"))

# verify the running container picked up the env
import time
time.sleep(6)
conts = req("GET", f"/endpoints/{EID}/docker/containers/json?all=1")
cid = next(c["Id"] for c in conts
           if any("eve-chuji" in n for n in c["Names"]))
ins = req("GET", f"/endpoints/{EID}/docker/containers/{cid}/json")
cb = next((e for e in ins["Config"]["Env"]
           if e.startswith("EVE_CALLBACK_URL=")), "(missing)")
print("   container state:", ins["State"]["Status"])
print("   container", cb)
PY

echo "==> verify ${PUBLIC_URL}"
code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "${PUBLIC_URL}/")
echo "   ${PUBLIC_URL} -> HTTP ${code}"
[[ "$code" == "200" ]] && echo "==> deploy OK" || { echo "==> WARNING: non-200"; exit 1; }
