# -*- coding: utf-8 -*-
"""A publication's own email list: readers sign up, writers send each article.

Every publication other than PloverResearch (which posts through its owner's
Substack) can collect subscribers on its own pages and email each new article
to them, under its own name, with replies going to the writer who sent it.
The list belongs to the publication: its owner sees it, imports it, downloads
it and removes from it; nobody else on the platform does.

The rules a list must keep, because one careless send can put every email the
site sends (sign-in links included) into spam folders:

  * A reader who signs up on the page CONFIRMS by email before anything is
    sent to them. A typo or someone else's address never gets a newsletter.
  * An imported list (a Substack export, say) is taken as already confirmed,
    on the owner's word that these people asked for this newsletter. The
    import screen says so, and the owner ticks it.
  * Every email carries an unsubscribe link and the List-Unsubscribe headers
    mail providers use for their own one-click button. Unsubscribing takes
    effect at once and for good, until the reader signs up again.
  * One published version is emailed at most once (UNIQUE on article and
    version). An interrupted send is resumed, never restarted: each address
    already sent is recorded and skipped.

Sending runs in a background thread of the web process, in batches of 100
through Resend's batch endpoint (app/mailer.py), so the writer's browser is
not held open. A deploy that restarts the process mid-send leaves the send
"interrupted"; the writer resumes it from the editor and only the addresses
not yet sent are sent.

Storage: three tables in data/research/research.db, beside the publications
they belong to, so research_backup.py copies them every night with the rest.
"""
import csv
import datetime
import html
import io
import re
import secrets
import threading
import time
from email.utils import formataddr, parseaddr

from . import mailer, research, seo

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
  publication_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  status TEXT NOT NULL,             -- pending | active | unsubscribed
  token TEXT NOT NULL,              -- confirms and unsubscribes this one address
  source TEXT NOT NULL,             -- signup | import
  created_at INTEGER NOT NULL,
  confirm_sent_at INTEGER,
  confirmed_at INTEGER,
  unsubscribed_at INTEGER,
  PRIMARY KEY (publication_id, email)
);
CREATE UNIQUE INDEX IF NOT EXISTS subscribers_token ON subscribers (token);
CREATE TABLE IF NOT EXISTS newsletter_sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id INTEGER NOT NULL,
  article_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  subject TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  reply_to TEXT NOT NULL,
  status TEXT NOT NULL,             -- sending | stopped | done
  error TEXT,                       -- why a send stopped, for the writer
  total INTEGER NOT NULL DEFAULT 0,
  sent INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  progress_at INTEGER NOT NULL,
  finished_at INTEGER,
  UNIQUE (article_id, version)
);
CREATE TABLE IF NOT EXISTS newsletter_items (
  send_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  status TEXT NOT NULL,             -- sent | failed
  at INTEGER NOT NULL,
  error TEXT,
  PRIMARY KEY (send_id, email)
);
"""

BATCH = 100                         # Resend's batch endpoint takes up to 100 emails
PAUSE_SECONDS = 0.6                 # stays under Resend's default two requests a second
STALE_SECONDS = 300                 # a send with no progress for this long was interrupted
CONFIRM_COOLDOWN = 600          # one confirmation email per address per ten minutes
SIGNUPS_PER_IP_HOUR = 20
IMPORT_MAX_ROWS = 50000
IMPORT_MAX_BYTES = 8 * 1024 * 1024
EMAIL_MAX = 254
_EMAIL = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")

_schema_for = [None]
_lock = threading.Lock()
_ip_hits = {}
_running = set()                    # send ids with a live thread in this process


class NewsletterError(ValueError):
    """A request that cannot be honoured, worded for the reader or writer."""


class MailFailed(NewsletterError):
    """The mail provider refused a message; worth trying again later."""


class SendStopped(Exception):
    """The provider refused us, not an address (a bad key, a suspended
    account): sending more would only fail the same way."""


def _now():
    return int(time.time())


def conn():
    c = research.conn()
    path = str(research.db_path())
    if _schema_for[0] != path:
        with _lock:
            if _schema_for[0] != path:
                c.executescript(SCHEMA)
                c.commit()
                _schema_for[0] = path
    return c


def normalise(email):
    email = (email or "").strip().lower()
    if not email or len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise NewsletterError("Enter an email address, like name@example.com.")
    return email


def can_collect(pub):
    """PloverResearch's readers subscribe through its Substack; every other
    publication collects its own."""
    return bool(pub) and not pub["home"]


# --------------------------------------------------------------- links

def _link(pub, what, token):
    return "%s/p/%s/email/%s?t=%s" % (seo.SITE_BASE_URL, pub["slug"], what, token)


def confirm_link(pub, token):
    return _link(pub, "confirm", token)


def unsubscribe_link(pub, token):
    return _link(pub, "unsubscribe", token)


def sender_for(pub):
    """'Japan Special Situations <hello@ploveranalytics.com>': the platform's
    verified address under the publication's name."""
    import os
    raw = (os.environ.get("RESEND_FROM") or "").strip()
    addr = parseaddr(raw)[1] or raw
    return formataddr((pub["name"], addr)) if addr else ""


def postal_line():
    """Bulk mail rules ask for the sender's postal address in every message.
    Set NEWSLETTER_POSTAL_ADDRESS on the server; the footer prints it."""
    import os
    return (os.environ.get("NEWSLETTER_POSTAL_ADDRESS") or "").strip()


# ----------------------------------------------------- reader: sign up

def _ip_allowed(ip):
    now = time.monotonic()
    hits = [t for t in _ip_hits.get(ip, []) if t > now - 3600]
    if len(hits) >= SIGNUPS_PER_IP_HOUR:
        _ip_hits[ip] = hits
        return False
    hits.append(now)
    _ip_hits[ip] = hits
    if len(_ip_hits) > 10000:                       # forget the oldest networks
        for k in list(_ip_hits)[:5000]:
            _ip_hits.pop(k, None)
    return True


def subscribe(pub, email, ip=""):
    """Start a sign-up. Returns 'confirm_sent', 'already' (active: nothing is
    sent, and the page says the same thing either way), or 'resend_later'
    when a confirmation went to the address minutes ago. Raises
    NewsletterError for a bad address or a busy network."""
    if not can_collect(pub):
        raise NewsletterError("This publication does not take email sign-ups.")
    email = normalise(email)
    if ip and not _ip_allowed(ip):
        raise NewsletterError("Too many sign-ups from this network. Try again in an hour.")
    c = conn()
    now = _now()
    row = c.execute("SELECT * FROM subscribers WHERE publication_id = ? AND email = ?",
                    (pub["id"], email)).fetchone()
    if row is not None and row["status"] == "active":
        return "already"
    if row is not None and row["status"] == "pending" and row["confirm_sent_at"] \
            and row["confirm_sent_at"] > now - CONFIRM_COOLDOWN:
        return "resend_later"
    token = row["token"] if row is not None and row["status"] == "pending" \
        else secrets.token_urlsafe(24)
    with _lock:
        if row is None:
            c.execute("INSERT INTO subscribers (publication_id, email, status, token, source, "
                      "created_at, confirm_sent_at) VALUES (?, ?, 'pending', ?, 'signup', ?, ?)",
                      (pub["id"], email, token, now, now))
        else:
            c.execute("UPDATE subscribers SET status = 'pending', token = ?, source = 'signup', "
                      "confirm_sent_at = ?, unsubscribed_at = NULL "
                      "WHERE publication_id = ? AND email = ?", (token, now, pub["id"], email))
        c.commit()
    delivered = mailer.send_link(
        email, "Confirm your subscription to %s" % pub["name"],
        ["Someone, probably you, asked to receive %s by email on Plover Analytics." % pub["name"],
         "Press the link to start receiving new articles. If you did not ask, ignore this "
         "email and nothing more will be sent."],
        confirm_link(pub, token), "Confirm Subscription")
    if not delivered and mailer.configured():
        # The provider refused it. Telling the reader to check an inbox that
        # will stay empty is worse than saying so; the cooldown is lifted so
        # they can try again at once.
        with _lock:
            if row is None:                 # nothing half-made left on the owner's list
                c.execute("DELETE FROM subscribers WHERE publication_id = ? AND email = ?",
                          (pub["id"], email))
            else:
                c.execute("UPDATE subscribers SET confirm_sent_at = NULL "
                          "WHERE publication_id = ? AND email = ?", (pub["id"], email))
            c.commit()
        raise MailFailed("The confirmation email could not be sent just now. "
                         "Try again in a few minutes.")
    return "confirm_sent"


def _by_token(pub, token):
    if not token or len(token) > 100:
        return None
    return conn().execute("SELECT * FROM subscribers WHERE publication_id = ? AND token = ?",
                          (pub["id"], token)).fetchone()


def confirm(pub, token):
    """The address behind a confirmation link, now active; None for a link
    that is not (or no longer) valid."""
    row = _by_token(pub, token)
    if row is None or row["status"] == "unsubscribed":
        return None
    if row["status"] == "pending":
        c = conn()
        with _lock:
            c.execute("UPDATE subscribers SET status = 'active', confirmed_at = ? "
                      "WHERE publication_id = ? AND email = ?", (_now(), pub["id"], row["email"]))
            c.commit()
    return row["email"]


def subscriber_by_token(pub, token):
    row = _by_token(pub, token)
    return dict(row) if row is not None else None


def unsubscribe(pub, token):
    """Unsubscribe the address behind a link; its email, or None."""
    row = _by_token(pub, token)
    if row is None:
        return None
    if row["status"] != "unsubscribed":
        c = conn()
        with _lock:
            c.execute("UPDATE subscribers SET status = 'unsubscribed', unsubscribed_at = ? "
                      "WHERE publication_id = ? AND email = ?", (_now(), pub["id"], row["email"]))
            c.commit()
    return row["email"]


# ----------------------------------------------------- owner: the list

def counts(publication_id):
    out = {"active": 0, "pending": 0, "unsubscribed": 0}
    for r in conn().execute("SELECT status, count(*) AS n FROM subscribers "
                            "WHERE publication_id = ? GROUP BY 1", (publication_id,)):
        out[r["status"]] = r["n"]
    return out


def listing(publication_id):
    return [dict(r) for r in conn().execute(
        "SELECT email, status, source, created_at, confirmed_at, unsubscribed_at "
        "FROM subscribers WHERE publication_id = ? ORDER BY created_at DESC, email",
        (publication_id,))]


def remove(publication_id, email):
    email = (email or "").strip().lower()
    c = conn()
    with _lock:
        n = c.execute("DELETE FROM subscribers WHERE publication_id = ? AND email = ?",
                      (publication_id, email)).rowcount
        c.commit()
    if not n:
        raise NewsletterError("That address is not on the list.")


def _truthy(v):
    return (v or "").strip().lower() in ("true", "yes", "1", "y", "t")


def parse_import(text):
    """(addresses, skipped) from a CSV or a plain list, one address a line.

    A Substack export has a header row with an `email` column; any CSV with
    a column called email (or e-mail, email address) works the same way. A
    row flagged unsubscribed or email_disabled is left out. Without such a
    header, every cell that is an address is taken."""
    if len(text.encode("utf-8")) > IMPORT_MAX_BYTES:
        raise NewsletterError("That file is larger than 8 MB. Split it and import each part.")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise NewsletterError("The file is empty.")
    head = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(head)
                if h in ("email", "e-mail", "email address", "email_address", "emailaddress")), None)
    off = [i for i, h in enumerate(head) if h in ("email_disabled", "unsubscribed",
                                                  "is_unsubscribed", "email disabled")]
    found, skipped, seen = [], 0, set()
    body = rows[1:] if col is not None else rows
    for r in body:
        cells = [r[col]] if col is not None and col < len(r) else ([] if col is not None else r)
        if col is not None and any(i < len(r) and _truthy(r[i]) for i in off):
            skipped += 1
            continue
        got = False
        for cell in cells:
            try:
                e = normalise(cell)
            except NewsletterError:
                continue
            got = True
            if e not in seen:
                seen.add(e)
                found.append(e)
        if not got and any((x or "").strip() for x in r):
            skipped += 1
    if len(found) > IMPORT_MAX_ROWS:
        raise NewsletterError("More than %s addresses. Split the file and import each part."
                              % format(IMPORT_MAX_ROWS, ","))
    if not found:
        raise NewsletterError("No email addresses found. Use a CSV with an 'email' column, "
                              "such as Substack's subscriber export, or one address a line.")
    return found, skipped


def import_addresses(publication_id, addresses):
    """Add confirmed subscribers. An address that unsubscribed stays
    unsubscribed: an import must never undo a reader's own choice.
    Returns {added, already, kept_unsubscribed}."""
    c = conn()
    now = _now()
    have = {r["email"]: r["status"] for r in c.execute(
        "SELECT email, status FROM subscribers WHERE publication_id = ?", (publication_id,))}
    out = {"added": 0, "already": 0, "kept_unsubscribed": 0}
    with _lock:
        for e in addresses:
            st = have.get(e)
            if st == "unsubscribed":
                out["kept_unsubscribed"] += 1
            elif st == "active":
                out["already"] += 1
            elif st == "pending":
                c.execute("UPDATE subscribers SET status = 'active', source = 'import', "
                          "confirmed_at = ? WHERE publication_id = ? AND email = ?",
                          (now, publication_id, e))
                out["added"] += 1
            else:
                c.execute("INSERT INTO subscribers (publication_id, email, status, token, source, "
                          "created_at, confirmed_at) VALUES (?, ?, 'active', ?, 'import', ?, ?)",
                          (publication_id, e, secrets.token_urlsafe(24), now, now))
                out["added"] += 1
        c.commit()
    return out


def export_csv(publication_id):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["email", "status", "source", "subscribed", "confirmed", "unsubscribed"])
    day = lambda t: datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d") if t else ""
    for r in listing(publication_id):
        w.writerow([r["email"], r["status"], r["source"], day(r["created_at"]),
                    day(r["confirmed_at"]), day(r["unsubscribed_at"])])
    return out.getvalue()


# ----------------------------------------------------- writer: sending

def _send_view(row):
    if row is None:
        return None
    d = dict(row)
    # No thread here and no progress for five minutes: the process that was
    # sending it has gone (a deploy), and the writer can resume it.
    if d["status"] == "sending" and d["id"] not in _running \
            and d["progress_at"] < _now() - STALE_SECONDS:
        d["status"] = "interrupted"
    return d


def send_for(article_id, version):
    return _send_view(conn().execute(
        "SELECT * FROM newsletter_sends WHERE article_id = ? AND version = ?",
        (article_id, version)).fetchone())


def sends_of(publication_id, limit=20):
    return [_send_view(r) for r in conn().execute(
        "SELECT * FROM newsletter_sends WHERE publication_id = ? ORDER BY id DESC LIMIT ?",
        (publication_id, limit))]


def message(pub, version, url, token):
    """(subject, text, html, headers) for one subscriber."""
    unsub = unsubscribe_link(pub, token)
    title, dek, summary = version["title"], version["dek"], version["summary"]
    authors = [a for a in version["authors"] if a]
    byline = ("By " + (", ".join(authors[:-1]) + " and " + authors[-1] if len(authors) > 1
                       else authors[0])) if authors else ""
    postal = postal_line()
    why = ("You are receiving this because you subscribed to %s on Plover Analytics."
           % pub["name"])
    text = "\n\n".join(p for p in [
        pub["name"].upper(), title, dek, byline, summary,
        "Read the full article, with the charts and data: " + url,
        "---", why, "Unsubscribe: " + unsub, postal] if p) + "\n"
    e = lambda s: html.escape(s or "", quote=True)
    para = 'style="margin:0 0 14px;font-size:16px;line-height:1.55;color:#0f172a"'
    body = "".join([
        '<div style="max-width:600px;margin:0 auto;padding:24px 20px;'
        'font-family:Inter,-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
        '<p style="margin:0 0 18px;font-size:12px;font-weight:700;letter-spacing:.06em;'
        'text-transform:uppercase;color:#1a4d8f">%s</p>' % e(pub["name"]),
        '<h1 style="margin:0 0 10px;font-size:26px;line-height:1.2;font-weight:800;'
        'color:#0f172a">%s</h1>' % e(title),
        ('<p style="margin:0 0 12px;font-size:17px;line-height:1.45;color:#334155">%s</p>'
         % e(dek)) if dek else "",
        ('<p style="margin:0 0 18px;font-size:13px;color:#64748b">%s</p>' % e(byline))
        if byline else "",
        ('<p %s>%s</p>' % (para, e(summary))) if summary else "",
        '<p style="margin:22px 0 28px"><a href="%s" style="display:inline-block;'
        'background:#1a4d8f;color:#ffffff;text-decoration:none;font-weight:600;'
        'font-size:15px;padding:11px 20px;border-radius:3px">Read the Full Article</a></p>'
        % e(url),
        '<p style="margin:0;padding-top:16px;border-top:1px solid #e2e8f0;font-size:12px;'
        'line-height:1.5;color:#64748b">%s <a href="%s" style="color:#64748b">Unsubscribe</a>.'
        '%s</p>' % (e(why), e(unsub), ("<br>" + e(postal)) if postal else ""),
        "</div>"])
    headers = {"List-Unsubscribe": "<%s>" % unsub,
               "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return title, text, body, headers


def start_send(pub, article_id, version, actor, reply_to):
    """Begin (or resume) emailing a published version to the list. Returns
    the send's view. Raises NewsletterError when there is nobody to send to
    or the version has been sent already."""
    if not mailer.configured():
        raise NewsletterError("Email is not set up on this server, so nothing can be sent.")
    v = research.version(article_id, version)
    if v is None:
        raise NewsletterError("Publish the article before emailing it.")
    c = conn()
    now = _now()
    existing = send_for(article_id, version)
    if existing is not None and existing["status"] == "done":
        raise NewsletterError("Version %d was emailed already, on %s. A version is sent once; "
                              "publish a new version to send again."
                              % (version, datetime.datetime.utcfromtimestamp(
                                  existing["finished_at"] or existing["created_at"])
                                 .strftime("%d %B %Y")))
    if existing is not None and existing["status"] == "sending":
        return existing                                  # running: report it, never twice
    # "stopped" (the provider refused us) and "interrupted" (the process
    # went away) resume below: only addresses not yet sent are sent.
    active = counts(pub["id"])["active"]
    if existing is None and not active:
        raise NewsletterError("This publication has no subscribers yet.")
    with _lock:
        if existing is None:
            c.execute("INSERT INTO newsletter_sends (publication_id, article_id, version, subject, "
                      "created_at, created_by, reply_to, status, total, progress_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'sending', ?, ?)",
                      (pub["id"], article_id, version, v["title"], now, actor,
                       reply_to or "", active, now))
        else:
            c.execute("UPDATE newsletter_sends SET status = 'sending', error = NULL, "
                      "progress_at = ? WHERE id = ?", (now, existing["id"]))
        c.commit()
    row = c.execute("SELECT * FROM newsletter_sends WHERE article_id = ? AND version = ?",
                    (article_id, version)).fetchone()
    _running.add(row["id"])
    threading.Thread(target=_run_send, args=(row["id"],), daemon=True,
                     name="newsletter-%d" % row["id"]).start()
    return _send_view(row)


def _run_send(send_id):
    try:
        _deliver(send_id)
    except SendStopped as exc:
        c = research.conn()
        with _lock:
            c.execute("UPDATE newsletter_sends SET status = 'stopped', error = ?, progress_at = ? "
                      "WHERE id = ?", (str(exc)[:300], _now(), send_id))
            c.commit()
    except Exception as exc:                             # noqa: BLE001 — logged, resumable
        print("[newsletter] send %d stopped: %r" % (send_id, exc), flush=True)
    finally:
        _running.discard(send_id)


def _deliver(send_id):
    c = research.conn()
    s = c.execute("SELECT * FROM newsletter_sends WHERE id = ?", (send_id,)).fetchone()
    pub = research.publication(s["publication_id"])
    v = research.version(s["article_id"], s["version"])
    url = seo.SITE_BASE_URL + research.address(s["publication_id"], v["slug"])
    sender = sender_for(pub)
    while True:
        todo = c.execute(
            "SELECT email, token FROM subscribers WHERE publication_id = ? AND status = 'active' "
            "AND email NOT IN (SELECT email FROM newsletter_items WHERE send_id = ?) "
            "ORDER BY email LIMIT ?", (s["publication_id"], send_id, BATCH)).fetchall()
        if not todo:
            break
        batch = []
        for r in todo:
            subject, text, body, headers = message(pub, v, url, r["token"])
            m = {"from": sender, "to": [r["email"]], "subject": subject, "text": text,
                 "html": body, "headers": headers}
            if s["reply_to"]:
                m["reply_to"] = s["reply_to"]
            batch.append(m)
        results = _send_batch(batch)
        now = _now()
        with _lock:
            for r, (ok, err) in zip(todo, results):
                c.execute("INSERT OR REPLACE INTO newsletter_items (send_id, email, status, at, error) "
                          "VALUES (?, ?, ?, ?, ?)",
                          (send_id, r["email"], "sent" if ok else "failed", now, err))
            c.execute("UPDATE newsletter_sends SET "
                      "sent = (SELECT count(*) FROM newsletter_items WHERE send_id = ? AND status = 'sent'), "
                      "failed = (SELECT count(*) FROM newsletter_items WHERE send_id = ? AND status = 'failed'), "
                      "progress_at = ? WHERE id = ?", (send_id, send_id, now, send_id))
            c.commit()
        time.sleep(PAUSE_SECONDS)
    with _lock:
        c.execute("UPDATE newsletter_sends SET status = 'done', finished_at = ?, "
                  "total = sent + failed WHERE id = ?", (_now(), send_id))
        c.commit()


def _send_batch(batch):
    """[(ok, error)] per message. A refused batch is retried after a pause
    when the provider is busy (429 or 5xx), then tried one message at a time
    so one bad address cannot fail the other ninety-nine."""
    for wait in (0, 2, 10):
        if wait:
            time.sleep(wait)
        try:
            mailer.send_batch(batch)
            return [(True, None)] * len(batch)
        except mailer.ProviderBusy:
            continue
        except Exception:                                # noqa: BLE001 — try one by one
            break
    out = []
    for m in batch:
        try:
            mailer.send_batch([m])
            out.append((True, None))
        except Exception as exc:                         # noqa: BLE001
            if getattr(exc, "code", None) in (401, 403) or isinstance(exc, mailer.ProviderBusy):
                # not this address: the key, the account or the provider.
                # Nothing is recorded, so a resume sends to everyone left.
                print("[newsletter] provider refused the account: %r" % (exc,), flush=True)
                raise SendStopped(
                    "The mail service refused the platform's email account, so nothing more "
                    "was sent. Ask the Plover team, then press Resume Sending.")
            out.append((False, str(exc)[:300]))
        time.sleep(PAUSE_SECONDS)
    return out
