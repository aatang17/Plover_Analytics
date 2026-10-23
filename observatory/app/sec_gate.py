# -*- coding: utf-8 -*-
"""One gate for every request this host sends the SEC.

2026-10-19: the 8-K press poller burst past the SEC's 10 requests a second
and the SEC (www.sec.gov and data.sec.gov, behind Akamai) refused the
server's address from then on, for every SEC job. Each job paced itself
alone, so several running at once could still add up to too much, and each
only stopped after its own refusals. Now every SEC request from any process
or thread on this host goes through get() here:

  rate     at most SEC_RATE requests a second in all (default 3), retries
           included: a lock file under data/sec-gate/ holds the time of the
           last request, taken with fcntl.flock so processes queue on it.
  refusal  a 429, or a 403 that is an HTML page (the "Access Denied" page;
           a missing EDGAR daily index is also a 403, but a short non-HTML
           one, and is not a refusal), writes data/sec-gate/refused.json.
           For SEC_REFUSED_HOLD_MINUTES after it (default 60) every get()
           from any process answers HELD at once, without asking the SEC,
           except one probe a window, sent once half the window has passed,
           so a lifted block is noticed. A refused probe starts a new window;
           an answer clears the marker.
  proxy    SEC_VIA (e.g. http://10.0.0.5:3128) carries SEC requests only;
           any other host is fetched directly.
  name     EDGAR_USER_AGENT, from the environment or observatory/.env: the
           SEC refuses a client that does not name a contact.

status() is what /catalog/health reads: the last answer and the refusal on
record. Python 3.9, standard library only: equity/ scripts import it too.
"""
import fcntl
import gzip
import html
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_DIR = pathlib.Path(os.environ.get("SEC_GATE_DIR") or str(ROOT / "data" / "sec-gate"))
HELD = -1                       # the status get() answers while a refusal hold is on
_LOCAL = threading.Lock()       # flock alone is per open file; this keeps one process's threads in line too
_OK_EVERY = 60.0                # seconds between writes of the last-success time
_last_ok_written = [0.0]


def rate():
    try:
        return max(0.1, float(os.environ.get("SEC_RATE") or 3))
    except ValueError:
        return 3.0


def hold_seconds():
    try:
        return max(0.0, float(os.environ.get("SEC_REFUSED_HOLD_MINUTES") or 60)) * 60
    except ValueError:
        return 3600.0


def user_agent():
    """EDGAR_USER_AGENT ("Company contact@domain"), or "" when it is not set."""
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not ua:
        try:
            for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("EDGAR_USER_AGENT="):
                    ua = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ua


def is_sec(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def is_refusal(status, body=b""):
    """True for HELD, a 429, or a 403 that is the HTML refusal page."""
    if status in (HELD, 429):
        return True
    if status != 403:
        return False
    head = (body or b"")[:4096].lower()
    return b"<html" in head or b"<!doctype html" in head


def _opener(url):
    """The proxy carries SEC hosts only; everything else goes as before."""
    proxy = os.environ.get("SEC_VIA", "").strip()
    if proxy and is_sec(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def _urlopen(req, timeout):
    return _opener(req.full_url).open(req, timeout=timeout)


# ---------------------------------------------------------------- state files


def _path(name):
    return pathlib.Path(STATE_DIR) / name


class _Locked(object):
    """An exclusive flock on one file under the state directory, held by one thread at a time."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        _LOCAL.acquire()
        try:
            pathlib.Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
            self.fd = os.open(str(_path(self.name)), os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException:
            _LOCAL.release()
            raise
        return self.fd

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
        finally:
            _LOCAL.release()
        return False


def _read_json(name):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(name, rec):
    tmp = _path(name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(str(tmp), str(_path(name)))


def _stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _slot():
    """Wait for this request's turn: requests are spaced 1/SEC_RATE apart across the host."""
    gap = 1.0 / rate()
    with _Locked("rate.lock") as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            last = float(os.read(fd, 64).decode("ascii").strip() or 0)
        except ValueError:
            last = 0.0
        now = time.time()
        at = max(now, last + gap)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, ("%.6f" % at).encode("ascii"))
    if at > now:
        time.sleep(at - now)


def _admit():
    """True when a request may go to the SEC: no hold on, or this is the window's one probe."""
    rec = _read_json("refused.json")
    if not rec:
        return True
    hold = hold_seconds()
    now = time.time()
    if now - rec.get("at", 0) >= hold:
        return True
    with _Locked("state.lock"):
        rec = _read_json("refused.json")
        if not rec or now - rec.get("at", 0) >= hold:
            return True
        # one probe a window, once half of it has passed
        if (rec.get("probe_at") or 0) < rec["at"] and now - rec["at"] >= hold / 2:
            rec["probe_at"] = now
            _write_json("refused.json", rec)
            return True
    return False


def _refused(url, status, body):
    text = html.unescape((body or b"")[:8192].decode("utf-8", "replace"))
    ref = re.search(r"Reference\s*#\s*([0-9A-Za-z.]+)", text)
    now = time.time()
    with _Locked("state.lock"):
        prev = _read_json("refused.json") or {}
        _write_json("refused.json", {
            "since": prev.get("since") or now, "at": now, "url": url, "status": status,
            "reference": ref.group(1).rstrip(".") if ref else None, "probe_at": None})


def _answered(status):
    now = time.time()
    if os.path.exists(str(_path("refused.json"))):
        with _Locked("state.lock"):
            try:
                os.remove(str(_path("refused.json")))
            except OSError:
                pass
    elif now - _last_ok_written[0] < _OK_EVERY:
        return
    _last_ok_written[0] = now
    with _Locked("state.lock"):
        _write_json("last-ok.json", {"at": now, "status": status})


# ---------------------------------------------------------------- the gate


def get(url, timeout=20, max_bytes=None, headers=None):
    """(status, body, headers) for one GET. Never raises for an HTTP answer
    or a network fault: an error status comes back as is, a fault as 0, and
    a refusal hold as HELD without asking the SEC. Bodies are un-gzipped.
    SEC hosts are paced, held and proxied; any other host is fetched directly."""
    sec = is_sec(url)
    h = {"Accept-Encoding": "gzip"}
    h.update(headers or {})
    if sec:
        if not h.get("User-Agent"):
            h["User-Agent"] = user_agent()
            if not h["User-Agent"]:
                raise RuntimeError('EDGAR_USER_AGENT is not set ("Company Name contact@domain"); '
                                   "the SEC refuses an unnamed client")
        if not _admit():
            return HELD, b"", {}
        _slot()
    req = urllib.request.Request(url, headers=h)
    try:
        with _urlopen(req, timeout) as r:
            body = r.read(max_bytes) if max_bytes else r.read()
            got = dict(r.headers.items()) if r.headers else {}
            status = r.status
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(65536)
        except Exception:  # noqa: BLE001
            body = b""
        got = dict(exc.headers.items()) if exc.headers else {}
        status = exc.code
    except Exception:  # noqa: BLE001 — DNS, TLS, timeout, proxy down
        return 0, b"", {}
    if {k.lower(): v for k, v in got.items()}.get("content-encoding") == "gzip" and body:
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    if sec:
        if is_refusal(status, body):
            _refused(url, status, body)
        elif status < 500:
            _answered(status)
    return status, body, got


def held():
    """True while a refusal hold is on (the probe allowance aside)."""
    rec = _read_json("refused.json")
    return bool(rec) and time.time() - rec.get("at", 0) < hold_seconds()


def status():
    """{last_ok_at, refused: None or {since, at, url, status, reference}, held}."""
    ok = _read_json("last-ok.json") or {}
    rec = _read_json("refused.json")
    out = {"last_ok_at": _stamp(ok["at"]) if ok.get("at") else None, "refused": None, "held": held(),
           "rate": rate(), "proxy": bool(os.environ.get("SEC_VIA", "").strip())}
    if rec:
        out["refused"] = {"since": _stamp(rec.get("since") or rec["at"]), "at": _stamp(rec["at"]),
                          "url": rec.get("url"), "status": rec.get("status"), "reference": rec.get("reference")}
    return out
