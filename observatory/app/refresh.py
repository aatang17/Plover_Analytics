"""The daily data refresh, and the watch that notices when it stops.

Why the process ends itself
---------------------------
DuckDB takes one writer, and the API holds a long-lived read-only handle on
the same file, so the ingest cannot run while the server is serving. The
refresh is therefore a restart: start.sh runs the ingests, then serves, and
when the server exits it runs them again. This module is what makes the
server exit, once a day, on the clock.

The alternative — a second Railway service that restarts this one on a cron —
is what observatory/README.md recommended for a month. It was never created,
and in early September 2026 the site quietly served three-day-old yields
because nothing had restarted it since the 1st. An in-repo scheduler cannot
be forgotten, needs no API token, and works identically under Docker and on a
laptop. That is the whole argument for it.

Only when something will restart us
-----------------------------------
Exiting is safe only under a supervisor. start.sh exports
REFRESH_SUPERVISED=1; nothing else does, so a plain `uvicorn app.main:app`
during development is never killed by this code, whatever the clock says.
The health watch below runs everywhere, because it only reports.

Why UTC
-------
The schedule is UTC and the default is 13:00, which is 22:00 in Tokyo — after
the Ministry of Finance posts the day's yield curve and after the 12:00 UTC
EDINET capture job, so an equity refresh reads the same day's archive rather
than yesterday's. Japan has no daylight
saving, so a fixed UTC offset is exactly right all year, and the slim
container image needs no timezone database for it to be.
"""
import asyncio
import datetime
import json
import os
import signal
import time
import urllib.error
import urllib.request

from starlette.concurrency import run_in_threadpool

from . import heartbeat
from . import seo

# The loop wakes every minute rather than sleeping until the target: a long
# sleep survives neither a suspended laptop nor a clock correction, and a
# minute of drift on a daily job is immaterial.
TICK_SECONDS = 60

# Never exit before this much uptime, whatever the clock says. A belt to the
# braces of _Clock's boot rule, so no combination of restart timing can turn
# into a restart loop.
MIN_UPTIME_SECONDS = 600

HEALTH_EVERY_SECONDS = 900
ALERT_REPEAT_SECONDS = 6 * 3600

# 22:00 Asia/Tokyo. Late enough to follow both upstream clocks: the Ministry
# of Finance posts the day's yield curve in the Tokyo afternoon, and the
# EDINET capture job runs at 12:00 UTC. Refreshing at 09:00 as it used to
# meant the equity extractors always read an archive that stopped the day
# before.
DEFAULT_AT = "13:00"
_UTC = datetime.timezone.utc

_FALSE = ("0", "false", "no", "off", "")


def _utcnow():
    return datetime.datetime.now(_UTC)


def enabled():
    return os.environ.get("REFRESH_ENABLED", "1").strip().lower() not in _FALSE


def supervised():
    return os.environ.get("REFRESH_SUPERVISED", "").strip().lower() not in _FALSE


def scheduled_at():
    """(hour, minute) of the daily refresh, in UTC."""
    raw = (os.environ.get("REFRESH_AT") or DEFAULT_AT).strip()
    head, _, tail = raw.partition(":")
    try:
        hour, minute = int(head), int(tail or 0)
    except ValueError:
        hour, minute = -1, -1
    if not (0 <= hour < 24 and 0 <= minute < 60):
        print("refresh: REFRESH_AT=%r is not HH:MM; using %s UTC" % (raw, DEFAULT_AT))
        return int(DEFAULT_AT[:2]), int(DEFAULT_AT[3:])
    return hour, minute


class _Clock(object):
    """Fires once per day, the first time the tick lands past the target.

    The boot rule matters more than it looks: a container that starts *after*
    today's target has, by definition, just ingested, so today's slot is
    already served. Without that, every restart after the target time would
    schedule another one a minute later, forever.
    """

    def __init__(self, now):
        self.hour, self.minute = scheduled_at()
        self.last_fired = now.date() if self.target(now) <= now else None

    def target(self, now):
        return now.replace(hour=self.hour, minute=self.minute,
                           second=0, microsecond=0)

    def due(self, now):
        return self.last_fired != now.date() and now >= self.target(now)

    def fired(self, now):
        self.last_fired = now.date()


# --- what counts as a problem ------------------------------------------------

def _age_hours(stamp, now=None):
    """Hours since an ISO stamp from the health report, or None if unreadable.

    The report mixes precisions — the journal writes whole seconds, the equity
    tables carry microseconds — and both end in 'Z', which Python 3.9's
    fromisoformat does not accept. Unreadable is None rather than 0: an alarm
    must never be raised by a parsing failure, nor silenced by one.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(stamp).rstrip("Z"))
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=_UTC)
    return ((now or _utcnow()) - parsed).total_seconds() / 3600.0


def problems(report):
    """(key, message) for everything wrong in a health report.

    Keyed so the alert throttle can hold one problem quiet while letting a
    new one through immediately.
    """
    found = []
    if report.get("refresh_overdue"):
        found.append(("refresh", "the ingest has not run for %s hours (limit %s) — "
                                 "the daily refresh has stopped"
                      % (report.get("hours_since_ingest"),
                         report.get("refresh_max_age_hours"))))
    for bad in (report.get("manifests") or {}).get("errors", []):
        name = bad.get("id") or bad.get("module")
        found.append(("manifest:" + str(name),
                      "%s manifest quarantined: %s"
                      % (name, "; ".join(bad.get("errors", [])))))
    for row in report.get("datasets", []):
        slug = row.get("dataset")
        # Asked of every dataset, published or not, and asked FIRST: a dataset
        # the refresh has stopped reaching is the fault that hides all the
        # others. Its data can sit comfortably inside a 90-day staleness
        # allowance for weeks while nothing whatsoever is happening to it.
        if row.get("checked_overdue"):
            found.append((slug + ":unchecked",
                          "%s has not been refreshed for %s hours (limit %s) — the "
                          "pipeline has stopped reaching it, whatever its data looks like"
                          % (slug, row.get("hours_since_checked"),
                             row.get("check_max_age_hours"))))
        if row.get("last_check_outcome") == "failed":
            found.append((slug + ":failing",
                          "%s failed its last refresh at %s: %s"
                          % (slug, row.get("last_checked_at"),
                             row.get("last_check_detail") or "no detail recorded")))
        if not row.get("published"):
            found.append((slug + ":unpublished",
                          "%s has no published release" % slug))
            continue
        if row.get("stale"):
            found.append((slug + ":stale",
                          "%s is stale: latest period %s, %s days old (limit %s)"
                          % (slug, row.get("latest_period"),
                             row.get("days_since_latest_period"),
                             row.get("stale_after_days"))))
        if row.get("unpublished_artifact"):
            found.append((slug + ":orphan",
                          "%s fetched a file that produced no release — a validation "
                          "failure nobody was told about" % slug))
    # The EDINET extractors, which this function ignored entirely until
    # 2026-09-20. All eleven were frozen for a week; /catalog/health said so
    # the whole time and the watch that turns health into an alert never read
    # that half of the report.
    #
    # They need no journal: eq_extract_runs already records when each
    # extractor last ran, which is the same "did the pipeline reach it?"
    # question the macro journal answers.
    for row in report.get("equity_extractors", []):
        name = "equity/" + str(row.get("dataset"))
        ran = _age_hours(row.get("last_extracted_at"))
        if ran is not None and ran > heartbeat.check_max_age_hours():
            found.append((name + ":unchecked",
                          "%s has not run for %.0f hours — the extractor has stopped, "
                          "whatever the archive holds" % (name, ran)))
        if row.get("stale"):
            found.append((name + ":stale",
                          "%s has read the archive only through %s (%s days behind, "
                          "limit %s)" % (name, row.get("archive_read_through"),
                                         row.get("days_behind"),
                                         row.get("stale_after_days"))))
        for flag in row.get("shape_flags") or []:
            found.append(("%s:shape:%s.%s" % (name, flag.get("table"), flag.get("column")),
                          "%s changed shape: %s.%s %s"
                          % (name, flag.get("table"), flag.get("column"),
                             flag.get("detail") or "")))
    return found


# --- alerting ----------------------------------------------------------------

_last_alert = {}


def _webhook():
    return (os.environ.get("ALERT_WEBHOOK_URL") or "").strip()


def _alert_emails():
    """Addresses to alarm, from ALERT_EMAIL_TO (comma-separated)."""
    raw = (os.environ.get("ALERT_EMAIL_TO") or "").strip()
    return [a.strip() for a in raw.split(",") if a.strip()]


def _telegram():
    """(bot token, [chat ids]) from the environment, or (None, []).

    TELEGRAM_BOT_TOKEN comes from @BotFather; TELEGRAM_CHAT_ID is the chat the
    bot posts into (comma-separated for several). Both are needed: a token
    with nowhere to post, or a chat with no bot, delivers nothing.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    raw = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    chats = [c.strip() for c in raw.split(",") if c.strip()]
    if not token or not chats:
        return None, []
    return token, chats


def delivery():
    """How an alarm would actually leave the building, for the health report.

    Worth reporting, because the answer was "it would not" for the whole of
    the 14-19 September 2026 outage: the watch below ran every fifteen
    minutes and printed ATTENTION into a log with nobody reading it, because
    no webhook and no mailbox were ever configured. An alarm nobody can
    receive is not a quieter alarm, it is no alarm, and the health endpoint
    should say so rather than implying one exists.
    """
    from . import mailer
    channels = []
    if _webhook():
        channels.append("webhook")
    if _alert_emails() and mailer.configured():
        channels.append("email")
    if _telegram()[0]:
        channels.append("telegram")
    return {"alert_channels": channels,
            "alerts_deliverable": bool(channels)}


# Telegram refuses a message over 4,096 characters. The first cycle after a
# long outage can carry dozens of problems, and one refused message would be
# the whole alarm lost, so long text is sent in pieces cut at line breaks.
TELEGRAM_LIMIT = 4000


def _chunks(text, limit=TELEGRAM_LIMIT):
    """Split text into pieces no longer than `limit`, at line breaks where possible."""
    pieces, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:             # one absurdly long line: hard cut
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            pieces.append(current)
            current = line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _send_telegram(text):
    """Post an alarm to every configured Telegram chat. Never raises.

    Plain text on purpose: Telegram's Markdown modes reject a message with an
    unbalanced underscore or bracket, and dataset names and error details are
    full of both. A formatting refusal must not be what silences an alarm.

    The token is part of the request URL, so no error printed here ever
    includes the URL — only the status and Telegram's own description.
    """
    token, chats = _telegram()
    if not token:
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    for chat in chats:
        for piece in _chunks(text):
            try:
                _post(url, {"chat_id": chat, "text": piece,
                            "disable_web_page_preview": True})
            except urllib.error.HTTPError as exc:
                try:
                    reason = json.loads(exc.read().decode("utf-8", "replace")).get(
                        "description")
                except Exception:                            # noqa: BLE001
                    reason = None
                print("refresh: telegram alert to chat %s failed: HTTP %s %s"
                      % (chat, exc.code, reason or ""))
                break                        # the rest would fail the same way
            except Exception as exc:                         # noqa: BLE001
                print("refresh: telegram alert to chat %s failed: %s"
                      % (chat, type(exc).__name__))
                break


def _email(subject, text):
    """Send one alarm to every configured address. Never raises."""
    from . import mailer
    for address in _alert_emails():
        try:
            mailer.send(address, subject, text)
        except Exception as exc:                             # noqa: BLE001
            print("refresh: alert email to %s failed: %s" % (address, exc))


def _post(url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return getattr(response, "status", response.getcode())


def alert(found, now=None):
    """Push the problems that are not already in their quiet window.

    Returns the keys actually sent, so a caller (and the tests) can see what
    the throttle let through.
    """
    url = _webhook()
    stamp = time.time() if now is None else now
    # A fault that has cleared forgets its quiet window, so the same fault
    # recurring an hour later is reported again rather than swallowed.
    for key in list(_last_alert):
        if key not in [k for k, _ in found]:
            del _last_alert[key]
    # Membership, not a zero sentinel: "never alerted" has to pass the throttle
    # whatever the clock reads, and `stamp - 0` does not on a small clock.
    fresh = [(key, message) for key, message in found
             if key not in _last_alert
             or stamp - _last_alert[key] >= ALERT_REPEAT_SECONDS]
    if not fresh:
        return []
    site = seo.SITE_BASE_URL
    text = "Plover Analytics — %d problem(s):\n%s" % (
        len(fresh), "\n".join("• " + message for _, message in fresh))
    if site:
        text += "\n%s/api/v1/catalog/health" % site
    if url:
        try:
            _post(url, {"text": text})
        except Exception as exc:  # noqa: BLE001 — an alert must never crash the app
            print("refresh: alert webhook failed: %s" % exc)
            return []
    # Email and Telegram are sent independently of the webhook and swallow
    # their own failures: one dead channel must not silence another, and must
    # not hold the quiet window open so the next pass repeats everything.
    _email("Plover Analytics: %d data problem(s)" % len(fresh), text)
    _send_telegram(text)
    for key, _ in fresh:
        _last_alert[key] = stamp
    return [key for key, _ in fresh]


def watch():
    """One pass of the health watch. Synchronous; run it off the event loop."""
    from . import api  # local: api imports heartbeat, and this imports api

    found = problems(api.health())
    for _, message in found:
        # Same shape as the boot check in start.sh, so one log rule catches both.
        print("ATTENTION %s" % message)
    # Called even when nothing is wrong, so cleared faults release their
    # throttle entries.
    alert(found)
    return found


# --- the loop ----------------------------------------------------------------

def _refresh_studies():
    """Studies set to refresh themselves (app/assistant/api.py): re-run the
    due ones on the newest data. No AI; a failure is kept as a run that says
    so. Nothing to do where the Assistant is off."""
    try:
        from .assistant import api as assistant_api
        made = assistant_api.refresh_due_studies()
    except Exception as exc:  # noqa: BLE001 — never let a study stop the watch
        print("refresh: study refresh failed: %s" % exc)
        return
    if made:
        print("refresh: %d stud%s refreshed on schedule" % (made, "y" if made == 1 else "ies"))


def _poll_press():
    """S&P 500 press releases (app/us_press.py): read every company's news
    feed when US_PRESS_POLL_HOURS have passed since the last poll. Writes only
    its own SQLite file; a failure is printed and the watch goes on."""
    try:
        from . import us_press
        us_press.poll_if_due()
    except Exception as exc:  # noqa: BLE001 — never let a newsroom stop the watch
        print("refresh: press-release poll failed: %s" % exc)


async def run():
    """Tick until cancelled: refresh on the clock, watch health in between."""
    if not enabled():
        print("refresh: disabled by REFRESH_ENABLED")
        return
    started = time.time()
    clock = _Clock(_utcnow())
    armed = supervised()
    print("refresh: watching health every %dmin; daily refresh at %02d:%02d UTC %s"
          % (HEALTH_EVERY_SECONDS // 60, clock.hour, clock.minute,
             "armed" if armed else "not armed (no supervisor)"))
    next_watch = time.time() + 60

    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            now = _utcnow()
            if (armed and clock.due(now)
                    and time.time() - started >= MIN_UPTIME_SECONDS):
                clock.fired(now)
                print("REFRESH scheduled daily refresh (%02d:%02d UTC) — shutting down "
                      "so the supervisor can re-run the ingests" % (clock.hour, clock.minute))
                os.kill(os.getpid(), signal.SIGTERM)
                return
            if time.time() >= next_watch:
                next_watch = time.time() + HEALTH_EVERY_SECONDS
                await run_in_threadpool(watch)
                await run_in_threadpool(_refresh_studies)
                await run_in_threadpool(_poll_press)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop outlives any one failure
            print("refresh: tick failed: %s" % exc)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "heartbeat":
        print("ingest heartbeat: %s" % heartbeat.write()["at"])
    elif len(sys.argv) > 1 and sys.argv[1] == "test-alert":
        # Proves a channel end to end before anyone relies on it: an alarm
        # channel first tested by a real outage is an alarm channel untested.
        print("channels: %s" % (delivery()["alert_channels"] or "none configured"))
        _send_telegram("Plover Analytics — test alert. If you can read this, "
                       "data-refresh alarms will reach this chat.")
        _email("Plover Analytics: test alert",
               "If you can read this, data-refresh alarms will reach this inbox.")
        print("sent (any failure is printed above)")
    else:
        print(json.dumps(heartbeat.status(), indent=2))
