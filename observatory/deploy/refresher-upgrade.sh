#!/usr/bin/env bash
# refresher-upgrade.sh — move the refresher to the last good image, but only
# while it is idle. Run every 5 minutes by plover-refresher-upgrade.timer.
#
# Recreating the refresher on every deploy would cut its work short again and
# again (an equity slice has run 97 minutes), so a deploy only records the
# image it should move to (LAST_GOOD_IMAGE_TAG) and this waits for the state
# file app/refresher.py writes to say "idle".
set -euo pipefail
STATE=/opt/plover/deploy.env
COMPOSE_FILE=/opt/plover/repo/observatory/deploy/docker-compose.yml
get() { grep -E "^$1=" "$STATE" | tail -1 | cut -d= -f2- || true; }

WANT=$(get LAST_GOOD_IMAGE_TAG)
HAVE=$(get REFRESHER_IMAGE_TAG)
DATA=$(get DATA_DIR)
[ -n "$WANT" ] || exit 0
[ "$WANT" = "$HAVE" ] && exit 0
state=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('state',''))" \
        "$DATA/refresher-state.json" 2>/dev/null || echo unknown)
if [ "$state" != "idle" ]; then
  echo "refresher is $state; staying on $HAVE until it is idle"
  exit 0
fi
export IMAGE_TAG="$WANT" REFRESHER_IMAGE_TAG="$WANT" DATA_DIR="$DATA"
docker compose -f "$COMPOSE_FILE" -p plover up -d --no-deps refresher
if grep -qE '^REFRESHER_IMAGE_TAG=' "$STATE"; then
  sed -i "s|^REFRESHER_IMAGE_TAG=.*|REFRESHER_IMAGE_TAG=$WANT|" "$STATE"
else
  echo "REFRESHER_IMAGE_TAG=$WANT" >> "$STATE"
fi
echo "refresher moved from ${HAVE:-none} to $WANT"
