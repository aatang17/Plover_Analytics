# -*- coding: utf-8 -*-
"""The refresher: the one process that writes the data files, beside the site.

On Railway, start.sh is the website and the refresh in one container: it binds
the port, runs app/backfill.py behind it, and restarts the whole server once a
day. On the DigitalOcean server (docs/plans/PLAN-DROPLET-ZERO-DOWNTIME.md) the
website runs in a container that only serves, so a deploy can start the new
copy before it stops the old one, and this runs in a second container on the
same volume:

  * on a volume with nothing published, a cold build first, starting with
    the point-in-time seed (app.vintages seed). start.sh seeds at every boot,
    before the server opens the file; here the website always holds it open,
    so a write to it is refused, and only a fresh volume needs the seed;
  * once a day at REFRESH_AT (13:00 UTC by default): app/backfill.py with
    every dataset — the copy → ingest → swap the served files have always
    had — then the research desk backup and the data backup;
  * in between it sleeps, and says so in data/refresher-state.json. A deploy
    replaces it only while it is idle: a slice cut short loses only that
    slice, but an equity slice has run 97 minutes.

A start does not run a cycle at once when the last one finished within the
day (the ingest heartbeat says when). Otherwise every deploy that replaced
this container would repeat the hour-long copy pass that made the research
desk stall after each Railway deploy.

Nothing here changes Railway: start.sh does not use it.

Usage:  python -m app.refresher            # run forever
        python -m app.refresher once       # one cycle now, then exit
        python -m app.refresher status     # print the state file
"""
import datetime
import json
import os
import signal
import subprocess
import sys
import time

from . import db, heartbeat, refresh

ROOT = db.ROOT
STATE_PATH = db.DATA_DIR / "refresher-state.json"
# A cycle that finished this recently is today's: a restart waits for the
# next REFRESH_AT instead of running another.
FRESH_HOURS = 20
# The same lead as start.sh's CURATED list: headline CPI before the long tail,
# so the most-read pages settle first. Everything registered in app/ingest.py
# follows, whether or not it is named here.
CURATED = """cpi-jp cpi-jp-items cpi-jp-goods-services cpi-jp-sa cpi-jp-long
    cpi-tokyo cpi-tokyo-items boj-assets jgb-yields jnto-visitors
    accommodation-jp population-jp population-jp-history population-jp-municipal
    trade-semis trade-inputs trade-autos trade-energy
    trade-machinery trade-pharma trade-food
    rice-prices-jp rice-inventory-jp agri-prices rice-production-cost
    ja-statistics gdp-jp corporate-finance-jp
    fsa-npl fsa-bank-results jba-banks""".split()

_child = {"proc": None}
_stopping = []


def log(msg):
    print("REFRESHER %s" % msg, flush=True)


def _utcnow():
    # naive UTC, as heartbeat stamps are; utcnow() warns on Python 3.12
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def datasets():
    """Every registered macro dataset, curated ones first."""
    from .ingest import ADAPTERS
    registered = set(ADAPTERS)
    return [d for d in CURATED if d in registered] + sorted(registered - set(CURATED))


def write_state(state, **extra):
    payload = {"state": state, "since": _utcnow().isoformat() + "Z",
               "image": os.environ.get("IMAGE_TAG") or None}
    payload.update(extra)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=1))
    os.replace(str(tmp), str(STATE_PATH))
    return payload


def read_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return None


def next_run(now):
    """The next REFRESH_AT strictly after now (UTC)."""
    hour, minute = refresh.scheduled_at()
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at if at > now else at + datetime.timedelta(days=1)


def last_cycle_hours(now):
    """Hours since the last cycle stamped the heartbeat, or None if never."""
    stamp = (heartbeat.read() or {}).get("at")
    if not stamp:
        return None
    try:
        at = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return (now - at).total_seconds() / 3600.0


def due_at_start(now):
    hours = last_cycle_hours(now)
    return hours is None or hours >= FRESH_HOURS


def has_published_data():
    """Is anything published on the volume? Fails closed, as start.sh does."""
    import duckdb
    path = db.DATA_DIR / "observatory.duckdb"
    if not path.exists():
        return False
    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception:  # noqa: BLE001
        return False
    try:
        return bool(con.execute(
            "SELECT count(*) FROM releases WHERE status = 'published'").fetchone()[0])
    except Exception:  # noqa: BLE001
        return False
    finally:
        con.close()


def _run(argv, env=None, deadline=None):
    """Run a step as a child process; stopped at the deadline or on SIGTERM.
    Returns its exit code (-1 when it was stopped)."""
    if _stopping:
        return -1
    proc = subprocess.Popen(argv, cwd=str(ROOT), env=env or os.environ.copy())
    _child["proc"] = proc
    try:
        while True:
            try:
                return proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if _stopping or (deadline is not None and time.time() >= deadline):
                log("stopping %s (%s)" % (argv[2] if len(argv) > 2 else argv[-1],
                                          "shutdown" if _stopping else "next cycle is due"))
                proc.terminate()
                try:
                    proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return -1
    finally:
        _child["proc"] = None


def cold_build():
    """A volume with nothing to serve: build everything, as start.sh does."""
    py = sys.executable
    _run([py, "-m", "app.vintages", "seed"])
    for dataset in datasets():
        if _run([py, "-m", "app.ingest", dataset]) != 0:
            log("ingest %s did not publish" % dataset)
    _run([py, "equity/refresh_equity.py", "--seed", "seed/equity.duckdb", "--skip", "bank-irrbb"])
    if os.environ.get("EDINET_S3_BUCKET"):
        _run([py, "equity/sec_extract.py", "--source", "s3",
              "--last", os.environ.get("SEC_QUARTERS", "4"), "--db", "data/sec.duckdb"])
    _run([py, "-m", "app.refresh", "heartbeat"])


def backups():
    """The research desk, then the data files. Neither may stop the other."""
    from . import data_backup, research_backup
    if not data_backup.enabled():
        log("off-site backups off (OFFSITE_BACKUPS=0); local desk copies only")
    try:
        status = research_backup.run()
        log("research backup: %s" % ("ok" if status.get("ok") else status.get("error")))
    except Exception as exc:  # noqa: BLE001
        log("ATTENTION research backup failed: %s" % exc)
    try:
        status = data_backup.run()
        log("data backup: %s" % ("ok" if status.get("ok") else status.get("error")))
    except Exception as exc:  # noqa: BLE001
        log("ATTENTION data backup failed: %s" % exc)


def cycle(deadline=None):
    """One refresh: backfill with every dataset, then the backups."""
    write_state("busy", step="refresh")
    if not has_published_data():
        log("nothing published on the volume; cold build first")
        cold_build()
    env = dict(os.environ, BACKFILL_DATASETS=" ".join(datasets()))
    rc = _run([sys.executable, "-m", "app.backfill"], env=env, deadline=deadline)
    log("backfill exited %s" % rc)
    if not _stopping:
        # Stored holder types brought up to the current rules (app/reclassify.py).
        log("reclassify exited %s" % _run([sys.executable, "-m", "app.reclassify"],
                                          env=dict(os.environ), deadline=deadline))
    if not _stopping:
        write_state("busy", step="backups")
        backups()


def _on_term(signum, frame):
    _stopping.append(signum)
    proc = _child["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()


def main(argv):
    if argv[:1] == ["status"]:
        print(json.dumps(read_state(), indent=1))
        return 0
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    if argv[:1] == ["once"]:
        cycle()
        write_state("idle", next_run=None)
        return 0
    run_now = due_at_start(_utcnow())
    while not _stopping:
        now = _utcnow()
        if run_now:
            # the long history phases stop when the next cycle is due, as
            # start.sh's daily restart stops them; that cycle then runs at once
            due = next_run(now)
            cycle(deadline=time.time() + (due - now).total_seconds())
            run_now = _utcnow() >= due
            continue
        at = next_run(now)
        write_state("idle", next_run=at.isoformat() + "Z")
        log("idle until %sZ" % at.isoformat())
        while not _stopping and _utcnow() < at:
            time.sleep(min(30, max(1, (at - _utcnow()).total_seconds())))
        run_now = True
    write_state("idle", next_run=None, stopped=True)
    log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
