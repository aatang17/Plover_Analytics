#!/bin/bash
# Installs the archive captures on this server as systemd timers.
# Run as root from the checkout: /opt/plover/repo/observatory/deploy/capture/install.sh
#
# The times are the Railway cron times they replace (UTC): daily, except the
# BOJ walk, which reads every database and runs on the 1st of the month. Persistent=true runs
# a capture the server missed while it was down, once it is back. A timer never
# starts a second copy of a job that is still running.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# the runner is copied out of the checkout: bash reads a script as it runs, so
# a deploy rewriting it mid-run would change the job under its feet
install -d /opt/plover/bin
install -m 755 "$HERE/run-capture.sh" /opt/plover/bin/plover-capture
install -m 644 "$HERE/plover-capture@.service" /etc/systemd/system/plover-capture@.service

while read -r job when; do
  cat > "/etc/systemd/system/plover-capture-$job.timer" <<EOF
[Unit]
Description=Plover archive capture: $job ($when UTC)

[Timer]
OnCalendar=$when UTC
Persistent=true
Unit=plover-capture@$job.service

[Install]
WantedBy=timers.target
EOF
done <<'JOBS'
edgar    *-*-* 06:00:00
watchdog *-*-* 07:00:00
edinet   *-*-* 12:00:00
tdnet    *-*-* 12:30:00
boj      *-*-01 14:00:00
us       *-*-* 22:00:00
JOBS

systemctl daemon-reload
for job in edgar watchdog edinet tdnet boj us; do
  systemctl enable --now "plover-capture-$job.timer" >/dev/null
done
systemctl list-timers 'plover-capture-*' --no-pager
