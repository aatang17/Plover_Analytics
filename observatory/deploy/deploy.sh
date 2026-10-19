#!/usr/bin/env bash
# deploy.sh [ref] — put a commit live on the DigitalOcean server with no outage.
#
#   ssh root@<server> /opt/plover/repo/observatory/deploy/deploy.sh main
#   ssh root@<server> /opt/plover/repo/observatory/deploy/deploy.sh image:sha-<old>
#
# The second form puts an image already on the server live without touching
# the checkout: a rollback by hand, or a rehearsal of uncommitted work.
#
# Blue/green, two slots (web-a, web-b):
#   1. sync the checkout to the commit and build plover:<sha> (cached layers);
#   2. start the new version in the empty slot, out of rotation (READY_GATE),
#      and wait for its healthcheck;
#   3. smoke-check it from inside; on failure it is removed and the live slot
#      was never touched;
#   4. release it, and check THROUGH CADDY that readers reach it;
#   5. mark the old slot as draining, check through Caddy that every reader
#      now reaches the new copy, and only then stop the old one;
#   6. record the new image as last good, and as the image the refresher
#      should move to (refresher-upgrade.sh moves it once it is idle).
# Throughout, an origin probe asks Caddy for the home page every second, the
# way a reader would.
#
# The rule every failure path keeps: a copy that is serving is never stopped
# unless the other copy is confirmed, through Caddy, to be serving instead.
#
# 2026-10-11: two hand-run deploys of the image already live left the only
# copy marked as draining and took the site down for 16 minutes. The old
# script checked each container, never what a reader gets through Caddy.
# One deploy runs at a time (flock), whoever starts it.
#
# State lives in $PLOVER_STATE (default /opt/plover/deploy.env): ACTIVE_SLOT,
# WEB_A_TAG, WEB_B_TAG, LAST_GOOD_IMAGE_TAG, REFRESHER_IMAGE_TAG, DATA_DIR and,
# for a private test copy, ENV_FILE, HTTP_BIND, HTTPS_BIND, NO_REFRESHER.
# PLOVER_PROJECT names the Compose project (default plover); PLOVER_REPO the
# checkout (default /opt/plover/repo).
set -euo pipefail

# Run from a temp copy: the git reset below must not rewrite a running script.
if [ -z "${PLOVER_DEPLOY_REEXEC:-}" ]; then
  tmp=$(mktemp /tmp/plover-deploy.XXXXXX.sh)
  cp "$0" "$tmp"
  PLOVER_DEPLOY_REEXEC=1 exec bash "$tmp" "$@"
fi

REF="${1:-main}"
REPO="${PLOVER_REPO:-/opt/plover/repo}"
STATE="${PLOVER_STATE:-/opt/plover/deploy.env}"
P="${PLOVER_PROJECT:-plover}"
DEPLOY_DIR="$REPO/observatory/deploy"
touch "$STATE"
chmod 600 "$STATE"

say() { echo "==> $*"; }

exec 8>"/run/plover-deploy.$P.lock"
if ! flock -n 8; then
  say "another deploy is running; waiting for it (up to 15 minutes)"
  flock -w 900 8 || { say "gave up waiting for the other deploy"; exit 3; }
fi

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
running() { docker ps -q -f "name=^$1$" | grep -q .; }
released() { docker exec "$1" test -f /tmp/plover-released 2>/dev/null \
               && ! docker exec "$1" test -f /tmp/plover-draining 2>/dev/null; }
host_of() { docker inspect -f '{{.Config.Hostname}}' "$1" 2>/dev/null; }

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

# everything Compose interpolates, from the state file
export DATA_DIR="$(state_get DATA_DIR)"; DATA_DIR="${DATA_DIR:-$(readlink -f /srv/plover-data)}"
export ENV_FILE="$(state_get ENV_FILE)"; ENV_FILE="${ENV_FILE:-/opt/plover/.env}"
export HTTP_BIND="$(state_get HTTP_BIND)"; HTTP_BIND="${HTTP_BIND:-80}"
export HTTPS_BIND="$(state_get HTTPS_BIND)"; HTTPS_BIND="${HTTPS_BIND:-443}"
export WEB_A_TAG="$(state_get WEB_A_TAG)" WEB_B_TAG="$(state_get WEB_B_TAG)"
export REFRESHER_IMAGE_TAG="$(state_get REFRESHER_IMAGE_TAG)"
COMPOSE="docker compose -f $DEPLOY_DIR/docker-compose.yml -p $P"

# Which copy is serving now? Read from the running containers, not only the
# state file: a copy that is released and not draining is the live one.
OLD_C=""
for c in "$P-web-$(state_get ACTIVE_SLOT)-1" "$P-web-a-1" "$P-web-b-1" "$P-web-1"; do
  if running "$c" && released "$c"; then OLD_C="$c"; break; fi
done
case "$OLD_C" in
  "$P-web-a-1") NEW=b ;;
  *) NEW=a ;;          # b live, the old single "web" container, or nothing yet
esac
NEW_C="$P-web-$NEW-1"
if [ "$NEW" = a ]; then export WEB_A_TAG="$TAG"; else export WEB_B_TAG="$TAG"; fi

# --- reading the site the way a reader does: through Caddy ---------------
PORT="${HTTPS_BIND##*:}"
CADDY_UP=""
via_caddy() {  # via_caddy <path> [curl -w format]: one request through Caddy
  curl -sk -m 5 --resolve "ploveranalytics.com:$PORT:127.0.0.1" -o /dev/null \
       -w "${2:-%{http_code}}" "https://ploveranalytics.com:$PORT$1" 2>/dev/null || echo 000
}
who_via_caddy() {  # which container answered /healthz through Caddy
  curl -sk -m 5 --resolve "ploveranalytics.com:$PORT:127.0.0.1" \
       "https://ploveranalytics.com:$PORT/healthz" 2>/dev/null | awk '{print $2}'
}
reaches() {  # reaches <container> all|some: within 20 s, are readers reaching it?
  local want; want=$(host_of "$1"); local mode=$2 tries=0 seen=0 other=0 h
  while [ "$tries" -lt 40 ]; do
    tries=$((tries + 1)); h=$(who_via_caddy)
    if [ "$h" = "$want" ]; then seen=$((seen + 1)); else other=$((other + 1)); fi
    if [ "$mode" = some ] && [ "$seen" -ge 2 ]; then return 0; fi
    if [ "$mode" = all ] && [ "$seen" -ge 8 ] && [ "$other" -eq 0 ]; then return 0; fi
    [ "$mode" = all ] && [ "$h" != "$want" ] && seen=0 && other=0
    sleep 0.5
  done
  return 1
}

PROBE_LOG=$(mktemp /tmp/plover-probe.XXXXXX)
PROBE_PID=""
start_probe() {  # the home page every second; 2xx and 3xx are up
  ( while :; do echo "$(date +%s) $(via_caddy /)" >> "$PROBE_LOG"; sleep 1; done ) &
  PROBE_PID=$!
}
stop_probe() { [ -n "$PROBE_PID" ] && kill "$PROBE_PID" 2>/dev/null; PROBE_PID=""; }
probe_down_run() {  # the longest run of seconds the home page was not 2xx/3xx
  awk '{ if ($2 ~ /^[23]/) run = 0; else { run++; if (run > m) m = run } } END { print m + 0 }' "$PROBE_LOG"
}
probe_report() {
  local n bad; n=$(wc -l < "$PROBE_LOG"); bad=$(awk '$2 !~ /^[23]/' "$PROBE_LOG" | wc -l)
  say "origin probe: $n checks of the home page through Caddy, $bad not up (longest gap $(probe_down_run) s)"
}

smoke() {  # from inside a container: the pages and data a reader hits first
  # -i: the check is fed on stdin; without it python reads nothing and "passes"
  docker exec -i "$1" python - <<'PY'
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

# Caddy probes /healthz/ready every second and needs two passes to use a copy.
# A copy is marked as about to stop, never taken off the network: that leaves
# requests already inside it hanging.
release() { docker exec "$1" touch /tmp/plover-released; }
drain() { docker exec "$1" touch /tmp/plover-draining >/dev/null 2>&1 || true; }
undrain() { docker exec "$1" rm -f /tmp/plover-draining >/dev/null 2>&1 || true; }
remove() { docker stop -t 30 "$1" >/dev/null 2>&1 || true; docker rm -f "$1" >/dev/null 2>&1 || true; }

caddy_current() {  # running, on this Caddyfile; started once the certificate exists
  if ! [ -f /opt/plover/certs/origin.pem ] || ! [ -f /opt/plover/certs/origin.key ]; then
    say "no origin certificate in /opt/plover/certs yet; Caddy not started"
    return 0
  fi
  if running "$P-caddy-1"; then
    # Copied in, not read through the mount: the Caddyfile is a single-file
    # bind mount, and git reset replaces the file, so the running container
    # would go on seeing the old one. A restart picks up the new file itself.
    docker cp "$DEPLOY_DIR/Caddyfile" "$P-caddy-1:/tmp/Caddyfile" \
      && docker exec "$P-caddy-1" caddy reload --config /tmp/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || { say "Caddy reload failed"; return 1; }
  else
    $COMPOSE up -d --no-deps caddy >/dev/null
  fi
  CADDY_UP=1
}

STAGE=start
on_exit() {
  status=$?
  stop_probe
  [ "$status" -eq 0 ] && { rm -f "$PROBE_LOG"; return; }
  case "$STAGE" in
    start|checked)
      say "FAILED — the new copy is removed; the live copy was never touched"
      remove "$NEW_C" ;;
    released)
      say "FAILED after release — taking the new copy back out; the old copy kept serving"
      drain "$NEW_C"; sleep 3; remove "$NEW_C" ;;
    draining)
      say "FAILED while switching — putting the old copy back"
      undrain "$OLD_C"
      if [ -z "$CADDY_UP" ] || reaches "$OLD_C" some; then
        drain "$NEW_C"; sleep 3; remove "$NEW_C"
        say "the old copy is serving again; the new copy is removed"
      else
        say "ATTENTION the old copy is not answering through Caddy either; both left running — check the server now"
      fi ;;
    *) say "FAILED after the switch (stage $STAGE) — check the server" ;;
  esac
  [ -n "$CADDY_UP" ] && probe_report
  rm -f "$PROBE_LOG"
  exit "$status"
}
trap on_exit EXIT

if running "$P-caddy-1" && [ -f /opt/plover/certs/origin.pem ]; then CADDY_UP=1; start_probe; fi

say "start $TAG in slot $NEW (serving now: ${OLD_C:-nothing})"
remove "$NEW_C"   # a leftover from an interrupted deploy, never the live copy
$COMPOSE up -d --no-deps --wait --force-recreate "web-$NEW" >/dev/null

say "smoke check (before any reader reaches it)"
smoke "$NEW_C"
STAGE=checked

release "$NEW_C"
STAGE=released
caddy_current
[ -n "$CADDY_UP" ] && [ -z "$PROBE_PID" ] && start_probe
if [ -n "$CADDY_UP" ]; then
  reaches "$NEW_C" some || { say "readers do not reach the new copy through Caddy"; exit 1; }
  say "checked through Caddy: readers reach the new copy"
fi

if [ -n "$OLD_C" ] && running "$OLD_C"; then
  say "switch: $(docker inspect -f '{{.Config.Image}}' "$OLD_C") → $TAG"
  STAGE=draining
  drain "$OLD_C"
  if [ -n "$CADDY_UP" ]; then
    reaches "$NEW_C" all || { say "readers do not all reach the new copy"; exit 1; }
    sleep 2   # requests already inside the old copy finish
    reaches "$NEW_C" all || { say "readers stopped reaching the new copy"; exit 1; }
    say "checked twice through Caddy: every reader reaches the new copy"
  else
    sleep 3
  fi
  remove "$OLD_C"
fi
STAGE=switched

if [ -n "$CADDY_UP" ]; then
  sleep 2
  probe_report
  if [ "$(probe_down_run)" -ge 3 ] && [ "$(via_caddy /)" != 200 ]; then
    say "ATTENTION the site is not answering through Caddy after the switch"
    exit 1
  fi
fi

state_set ACTIVE_SLOT "$NEW"
if [ "$NEW" = a ]; then state_set WEB_A_TAG "$TAG"; else state_set WEB_B_TAG "$TAG"; fi
state_set IMAGE_TAG "$TAG"
state_set LAST_GOOD_IMAGE_TAG "$TAG"
state_set DATA_DIR "$DATA_DIR"

if [ "$(state_get NO_REFRESHER)" != 1 ] && ! running "$P-refresher-1"; then
  say "start refresher on $TAG"
  export REFRESHER_IMAGE_TAG="$TAG"
  $COMPOSE up -d --no-deps refresher >/dev/null
  state_set REFRESHER_IMAGE_TAG "$TAG"
fi

# keep the five newest images, and every image a container still runs
keep=" $TAG $(state_get REFRESHER_IMAGE_TAG) $(docker ps --format '{{.Image}}' | sed -n 's/^plover://p' | tr '\n' ' ') "
docker images plover --format '{{.CreatedAt}}|{{.Tag}}' | sort -r | awk -F'|' 'NR>5 {print $2}' \
  | while read -r old; do
      case "$keep" in *" $old "*) ;; *) docker rmi "plover:$old" >/dev/null 2>&1 || true ;; esac
    done

trap - EXIT
stop_probe; rm -f "$PROBE_LOG"
say "live: $TAG in slot $NEW (refresher on $(state_get REFRESHER_IMAGE_TAG); it moves to $TAG once idle)"
