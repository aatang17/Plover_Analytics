#!/usr/bin/env bash
# deploy.sh [ref] — put a commit live on the DigitalOcean server with no outage.
#
#   ssh root@<server> /opt/plover/repo/observatory/deploy/deploy.sh main
#   ssh root@<server> /opt/plover/repo/observatory/deploy/deploy.sh image:sha-<old>
#
# The second form puts an image already on the server live without touching
# the checkout: a rollback by hand, or a rehearsal of uncommitted work.
#
# A cut-down copy of GSP's deploy/scripts/deploy-production.sh:
#   1. sync the checkout to the commit and build plover:<sha> (cached layers);
#   2. start web-bridge on the last good image — it answers to "web" too, so
#      Caddy always has a healthy copy to send readers to;
#   3. recreate web on the new image and wait for its healthcheck;
#   4. smoke-check it from inside; stop the bridge;
#   5. record the new image as last good, and as the image the refresher
#      should move to (refresher-upgrade.sh moves it once it is idle).
# Any failure after the switch starts rolls web back to the last good image
# the same way, and the script exits non-zero.
#
# State lives in /opt/plover/deploy.env (IMAGE_TAG, LAST_GOOD_IMAGE_TAG,
# REFRESHER_IMAGE_TAG, DATA_DIR). Settings for the app are /opt/plover/.env.
set -euo pipefail

# Run from a temp copy: the git reset below must not rewrite a running script.
if [ -z "${PLOVER_DEPLOY_REEXEC:-}" ]; then
  tmp=$(mktemp /tmp/plover-deploy.XXXXXX.sh)
  cp "$0" "$tmp"
  PLOVER_DEPLOY_REEXEC=1 exec bash "$tmp" "$@"
fi

REF="${1:-main}"
REPO=/opt/plover/repo
STATE=/opt/plover/deploy.env
DEPLOY_DIR="$REPO/observatory/deploy"
touch "$STATE"
chmod 600 "$STATE"

say() { echo "==> $*"; }
state_get() { grep -E "^$1=" "$STATE" | tail -1 | cut -d= -f2- || true; }
state_set() {
  if grep -qE "^$1=" "$STATE"; then sed -i "s|^$1=.*|$1=$2|" "$STATE"; else echo "$1=$2" >> "$STATE"; fi
}
retry() {  # retry <what> <command...>: 3 tries, 15 s then 30 s apart
  local what=$1; shift
  for wait in 15 30 0; do
    "$@" && return 0
    [ "$wait" -eq 0 ] && break
    echo "   $what failed; retrying in ${wait}s"; sleep "$wait"
  done
  return 1
}

case "$REF" in
  image:*)
    TAG="${REF#image:}"
    docker image inspect "plover:$TAG" >/dev/null 2>&1 || { echo "no image plover:$TAG on this server"; exit 1; }
    say "deploy existing image plover:$TAG (checkout untouched)"
    ;;
  *)
    say "sync $REF"
    retry "git fetch" git -C "$REPO" fetch -q --depth 50 origin "$REF"
    SHA=$(git -C "$REPO" rev-parse --short=12 FETCH_HEAD)
    git -C "$REPO" reset -q --hard FETCH_HEAD
    TAG="sha-$SHA"
    say "commit $(git -C "$REPO" log -1 --format='%h %s' | cut -c1-90)"
    if ! docker image inspect "plover:$TAG" >/dev/null 2>&1; then
      say "build plover:$TAG"
      docker build -q -t "plover:$TAG" "$REPO/observatory" >/dev/null
    fi
    ;;
esac

export DATA_DIR="$(state_get DATA_DIR)"
DATA_DIR="${DATA_DIR:-$(readlink -f /srv/plover-data)}"
PREV_TAG="$(state_get LAST_GOOD_IMAGE_TAG)"
export REFRESHER_IMAGE_TAG="$(state_get REFRESHER_IMAGE_TAG)"
COMPOSE="docker compose -f $DEPLOY_DIR/docker-compose.yml -p plover"

set_tag() {  # the shell's value beats any .env: export it (GSP lost a rollback to this)
  export IMAGE_TAG="$1"
  export REFRESHER_IMAGE_TAG="${REFRESHER_IMAGE_TAG:-$1}"
}

smoke() {  # from inside the new web container: the pages and data a reader hits first
  # -i: the check is fed on stdin; without it python reads nothing and "passes"
  docker exec -i plover-web-1 python - <<'PY'
import json, sys, urllib.request
B = "http://127.0.0.1:8080"
def get(path):
    with urllib.request.urlopen(B + path, timeout=30) as r:
        return r.status, r.read()
checks = [
    ("catalog", "/api/v1/catalog/datasets", lambda b: len(json.loads(b)["datasets"]) > 20),
    ("health", "/api/v1/catalog/health", lambda b: "datasets" in json.loads(b)),
    ("CPI YoY", "/api/v1/cpi-jp/observations?series=0001&measure=yoy&start=2025-01",
     lambda b: len(json.dumps(json.loads(b))) > 200),
    ("home page", "/", lambda b: b"<html" in b.lower()),
    ("company page", "/api/v1/company/7203", lambda b: b"7203" in b),
    ("sitemap", "/sitemap.xml", lambda b: b"<urlset" in b or b"<sitemapindex" in b),
]
bad = 0
for name, path, ok in checks:
    try:
        status, body = get(path)
        good = status == 200 and ok(body)
    except Exception as exc:
        good, status = False, exc
    print("   %-12s %s" % (name, "ok" if good else "FAILED (%s)" % status))
    bad += not good
sys.exit(1 if bad else 0)
PY
}

# Mark a copy as about to stop, then wait: its /healthz answers 503, Caddy
# (probing every second) sends it nothing more, and requests already inside
# finish. Without this the 2026-10-10 trial saw requests wait up to 12 s at
# each stop. A copy is never taken off the network instead: that leaves the
# requests already inside hanging.
DRAIN_SECONDS=3
drain() {
  docker exec "$1" touch /tmp/plover-draining >/dev/null 2>&1 || true
  sleep "$DRAIN_SECONDS"
}
release() {  # a checked copy joins the rotation; Caddy finds it within ~2 s
  docker exec "$1" touch /tmp/plover-released
  sleep "$DRAIN_SECONDS"
}
retire_bridge() {
  if docker ps -q -f name=^plover-web-bridge-1$ | grep -q .; then
    drain plover-web-bridge-1
  fi
  $COMPOSE --profile deploy-bridge stop web-bridge >/dev/null 2>&1 || true
  $COMPOSE --profile deploy-bridge rm -f web-bridge >/dev/null 2>&1 || true
}

SWITCH_STARTED=""
rollback_on_exit() {
  status=$?
  [ "$status" -eq 0 ] && return
  if [ -n "$SWITCH_STARTED" ] && [ -n "$PREV_TAG" ]; then
    say "FAILED — rolling web back to $PREV_TAG"
    set_tag "$PREV_TAG"
    if ! docker ps -q -f name=^plover-web-bridge-1$ | grep -q .; then
      BRIDGE_IMAGE_TAG="$PREV_TAG" $COMPOSE --profile deploy-bridge up -d --no-deps --wait web-bridge || true
      release plover-web-bridge-1 || true
    fi
    drain plover-web-1
    $COMPOSE up -d --no-deps --wait web || true
    smoke && release plover-web-1 || true
    retire_bridge
    if [ "$(docker inspect -f '{{.Config.Image}}' plover-web-1 2>/dev/null)" = "plover:$PREV_TAG" ] \
        && [ -n "$(docker exec plover-web-1 ls /tmp/plover-released 2>/dev/null)" ]; then
      say "rolled back to $PREV_TAG; the failed commit is not live"
    else
      say "ATTENTION rollback could not be verified — check the server now"
    fi
  else
    say "FAILED before the switch; nothing live changed"
  fi
  exit "$status"
}
trap rollback_on_exit EXIT

if [ -n "$PREV_TAG" ] && docker ps -q -f name=^plover-web-1$ | grep -q .; then
  say "bridge on $PREV_TAG"
  set_tag "$PREV_TAG"
  BRIDGE_IMAGE_TAG="$PREV_TAG" $COMPOSE --profile deploy-bridge up -d --no-deps --wait web-bridge
  release plover-web-bridge-1   # the image already live: checked before
fi

say "switch web to $TAG"
SWITCH_STARTED=1
set_tag "$TAG"
docker ps -q -f name=^plover-web-bridge-1$ | grep -q . && drain plover-web-1
$COMPOSE up -d --no-deps --wait web

say "smoke check (before any reader reaches it)"
smoke
release plover-web-1

retire_bridge

state_set IMAGE_TAG "$TAG"
state_set LAST_GOOD_IMAGE_TAG "$TAG"
state_set DATA_DIR "$DATA_DIR"
# first deploy: there is no refresher yet; start it on this image
if ! docker ps -q -f name=^plover-refresher-1$ | grep -q .; then
  say "start refresher on $TAG"
  export REFRESHER_IMAGE_TAG="$TAG"
  $COMPOSE up -d --no-deps refresher
  state_set REFRESHER_IMAGE_TAG "$TAG"
fi

if [ -f /opt/plover/certs/origin.pem ] && [ -f /opt/plover/certs/origin.key ]; then
  if docker ps -q -f name=^plover-caddy-1$ | grep -q .; then
    docker exec plover-caddy-1 caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
      || $COMPOSE up -d --no-deps --force-recreate caddy
  else
    $COMPOSE up -d --no-deps caddy
  fi
else
  say "no origin certificate in /opt/plover/certs yet; Caddy not started"
fi

# keep the five newest images
docker images plover --format '{{.Tag}} {{.CreatedAt}}' | sort -k2 -r | awk 'NR>5 {print $1}' \
  | while read -r old; do
      [ "$old" = "$TAG" ] || [ "$old" = "$REFRESHER_IMAGE_TAG" ] || docker rmi "plover:$old" >/dev/null 2>&1 || true
    done

trap - EXIT
say "live: $TAG (refresher on ${REFRESHER_IMAGE_TAG:-$TAG}; it moves to $TAG once idle)"
