#!/bin/bash
# One archive capture on this server, started by its systemd timer
# (plover-capture-<job>.timer → plover-capture@<job>.service → this script).
#
# The capture jobs ran on Railway until 2026-10-24. They write the raw archive
# (EDINET, TDnet, BOJ, the US agencies, EDGAR) to the S3 bucket; nothing here
# touches the served databases. Each run:
#
#   * copies equity/ and the SEC gate (observatory/app/sec_gate.py) out of the
#     repository checkout, so a deploy pulling the checkout mid-run changes
#     nothing under a running job;
#   * runs the collector in the image the refresher runs (it has Python 3.12
#     and boto3), with this server's settings file;
#   * shares the host's SEC gate state (data/sec-gate/), so the EDGAR capture
#     counts against the same SEC_RATE as every other SEC job here and goes
#     through SEC_VIA like them;
#   * on a non-zero exit, posts to the Telegram chat the site's own alarms use.
#     Railway emailed on a crash; nothing else here would notice a failed run.
#
# Usage: plover-capture <edinet|tdnet|boj|us|edgar|watchdog>
set -uo pipefail

JOB="${1:-}"
case "$JOB" in
  edinet)   ARGS=(capture.py --days 7) ;;
  tdnet)    ARGS=(tdnet_capture.py --days 7) ;;
  boj)      ARGS=(boj_capture.py) ;;
  us)       ARGS=(us_capture.py --sources fed,nyfed,treasury,fiscal,bls,bea,cftc,sec,fomc,fdic,sec-insiders,sec-13f,sec-ftd,sec-formd,sec-nport,sec-notes) ;;
  # beyond the core forms: insider filings (3/4/5) the day they are filed, rather
  # than a quarter later in the data sets; notices of planned insider sales (144);
  # private offerings (D). About 2,000 small filings a day in all.
  edgar)    ARGS=(edgar_capture.py --days 7 --workers 2 --add-forms "3,3/A,4,4/A,5,5/A,144,144/A,D,D/A") ;;
  watchdog) ARGS=(watchdog.py) ;;
  *) echo "usage: plover-capture <edinet|tdnet|boj|us|edgar|watchdog>"; exit 2 ;;
esac

REPO=/opt/plover/repo
STATE=/opt/plover/deploy.env
get() { grep -E "^$1=" "$STATE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
DATA_DIR="$(get DATA_DIR)"; DATA_DIR="${DATA_DIR:-$(readlink -f /srv/plover-data)}"
ENV_FILE="$(get ENV_FILE)"; ENV_FILE="${ENV_FILE:-/opt/plover/.env}"
TAG="$(get REFRESHER_IMAGE_TAG)"; TAG="${TAG:-$(get IMAGE_TAG)}"
IMAGE="plover:$TAG"

alarm() {
  # plain text, as app/refresh.py sends it: Markdown would reject an underscore
  docker run --rm --env-file "$ENV_FILE" "$IMAGE" python -c '
import json, os, sys, urllib.request
token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
for chat in [c.strip() for c in (os.environ.get("TELEGRAM_CHAT_ID") or "").split(",") if c.strip()]:
    if token:
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token,
                                     data=json.dumps({"chat_id": chat, "text": sys.argv[1]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "plover-capture"})
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as exc:
            print("telegram alert failed: %s" % type(exc).__name__)
' "$1" || echo "could not send the alarm: $1"
}

if [ -z "$TAG" ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "no refresher image on this server ($IMAGE); nothing run"
  exit 1
fi

RUN=$(mktemp -d "/tmp/plover-capture-$JOB.XXXXXX")
trap 'rm -rf "$RUN"' EXIT
mkdir -p "$RUN/observatory/app" "$DATA_DIR/sec-gate"
cp -r "$REPO/equity" "$RUN/equity"
cp "$REPO/observatory/app/__init__.py" "$REPO/observatory/app/sec_gate.py" "$RUN/observatory/app/"

echo "capture $JOB on $IMAGE ($(cd "$REPO" && git log -1 --format=%h 2>/dev/null))"
docker run --rm --name "plover-capture-$JOB" \
  --env-file "$ENV_FILE" -e PYTHONUNBUFFERED=1 -e SEC_GATE_DIR=/gate \
  -v "$RUN:/cap" -v "$DATA_DIR/sec-gate:/gate" -w /cap/equity \
  --memory 1500m --cpus 1 \
  "$IMAGE" python "${ARGS[@]}"
rc=$?

if [ "$rc" -ne 0 ]; then
  alarm "Plover capture '$JOB' failed (exit $rc) at $(date -u '+%Y-%m-%d %H:%M') UTC on $(hostname). Its log: journalctl -u plover-capture@$JOB --since today"
fi
exit "$rc"
