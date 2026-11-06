# -*- coding: utf-8 -*-
"""Latest Visits, organisations, and a page's reports finding their page view.

Run from observatory/:  ./.venv/bin/python -m unittest tests.test_visit_trips

The Traffic page answers "who was that?" from these: which organisation a
network names, every page one reader opened in order, and the reading time a
phone sends from a different address than the page it belongs to.
"""
import datetime
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from app import visit_orgs, visit_trips, visits

_REAL_DIR = [None, None]

UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36")


def setUpModule():
    """The test's own log in a temporary directory, never the data volume's."""
    _REAL_DIR[0] = visits.ANALYTICS_DIR
    _REAL_DIR[1] = tempfile.mkdtemp(prefix="visit-trips-test-")
    visits.ANALYTICS_DIR = pathlib.Path(_REAL_DIR[1])
    visits.SALT_PATH = visits.ANALYTICS_DIR / "salt"
    _forget()


def tearDownModule():
    visits.flush()
    _forget()
    visits.ANALYTICS_DIR = _REAL_DIR[0]
    visits.SALT_PATH = visits.ANALYTICS_DIR / "salt"
    shutil.rmtree(_REAL_DIR[1], ignore_errors=True)


def _forget():
    """Leave nothing in the counter's memory for the next test module: its day
    totals, live list and page views would otherwise carry these visitors."""
    visits._reset_folds()
    visits._VIEWS.clear()
    visits._ALIASES.clear()
    with visits._LOCK:
        visits._LIVE.clear()


def scope(path, ip, referer=None, cookie=None):
    headers = [(b"user-agent", UA.encode()), (b"host", b"ploveranalytics.com")]
    if referer:
        headers.append((b"referer", referer.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return {"type": "http", "method": "GET", "path": path, "client": (ip, 1),
            "headers": headers, "query_string": b""}


def _events():
    visits.flush()
    path = visits._month_path(datetime.datetime.utcnow())
    if not path.exists():
        return []
    with open(str(path), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class OrganisationTest(unittest.TestCase):
    """Only a name that says what it is is claimed as somebody's organisation."""

    def test_named_organisations(self):
        for name in ("Saba Capital Management, L.P.", "University of Kansas",
                     "SMBC Bank International plc", "FactSet Research Systems, Inc.",
                     "Morgan Stanley Management Service (Singapore) Pte. Ltd"):
            self.assertEqual(visit_orgs.kind(name), visit_orgs.ORGANISATION, name)
            self.assertEqual(visit_orgs.organisation(name), name)

    def test_providers_are_never_claimed(self):
        # SoftBank holds "bank", DBS Telecom holds a bank's name, Telefônica an accent.
        for name in ("SoftBank Corp.", "KDDI CORPORATION", "China Mobile Hong Kong Company Limited",
                     "DBS TELECOM EIRELI - ME", "TELEFÔNICA BRASIL S.A", "Comcast Cable Communications, LLC"):
            self.assertEqual(visit_orgs.kind(name), visit_orgs.PROVIDER, name)
            self.assertIsNone(visit_orgs.organisation(name))

    def test_data_centres_security_services_and_the_rest(self):
        self.assertEqual(visit_orgs.kind("Amazon.com, Inc."), visit_orgs.HOSTING)
        self.assertEqual(visit_orgs.kind("Google LLC"), visit_orgs.HOSTING)
        self.assertEqual(visit_orgs.kind("ZSCALER, INC."), visit_orgs.SECURITY)
        self.assertEqual(visit_orgs.kind("Web2Objects LLC"), visit_orgs.OTHER)
        self.assertEqual(visit_orgs.kind(None), visit_orgs.UNKNOWN)
        self.assertIsNone(visit_orgs.organisation("Web2Objects LLC"))

    def test_the_japanese_university_network_reads_as_one(self):
        name = "Research Organization of Information and Systems, National Institute of Informa"
        self.assertEqual(visit_orgs.organisation(name),
                         "SINET (Japanese universities and research institutes)")

    def test_app_referrers_are_their_site(self):
        self.assertEqual(visit_trips.source_name("com.linkedin.android"), "LinkedIn")
        self.assertEqual(visit_trips.source_name("www.linkedin.com"), "LinkedIn")
        self.assertEqual(visit_trips.source_name("t.co"), "X (Twitter)")
        self.assertEqual(visit_trips.source_name("www.google.co.jp"), "Google")
        self.assertEqual(visit_trips.source_name("gemini.google.com"), "Gemini")
        self.assertEqual(visit_trips.source_name("example.org"), "example.org")


class TripTest(unittest.TestCase):
    """One reader, one row, every page in order with its reading time."""

    def setUp(self):
        visits.flush()
        visits._VIEWS.clear()
        visits._ALIASES.clear()

    def place(self, table):
        return mock.patch.object(visits.geoip, "lookup", side_effect=lambda ip: table.get(ip, (None, None)))

    def test_a_reading_time_sent_from_another_address_joins_its_page_view(self):
        # The page opens over one address; its reports leave over another.
        places = {"203.0.113.10": ("HK", "China Mobile Hong Kong Company Limited"),
                  "203.0.113.11": ("HK", "China Mobile Hong Kong Company Limited")}
        with self.place(places):
            visits.observe(scope("/trip-rejoin.html", "203.0.113.10",
                                 referer="android-app://com.linkedin.android/"), 200)
            visits.heartbeat(scope("/api/v1/visit/ping", "203.0.113.11"), "/trip-rejoin.html")
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.11"), "/trip-rejoin.html", 21, "v1")
        page = [e for e in _events() if e.get("path") == "/trip-rejoin.html"]
        view = [e for e in page if e["kind"] == "page"][0]
        dwell = [e for e in page if e["kind"] == "dwell"][0]
        self.assertEqual(dwell["visitor"], view["visitor"], "the report is filed with its page view")
        self.assertTrue(dwell.get("rejoined"))
        self.assertEqual(dwell["network"], "China Mobile Hong Kong Company Limited")

        row = [r for r in visit_trips.recent()["readers"] if r["visitor"] == view["visitor"]][0]
        self.assertEqual(row["arrived"], "LinkedIn")
        self.assertEqual(row["steps"][0]["seconds"], 21)

    def test_a_report_is_never_given_to_a_view_from_another_country(self):
        places = {"203.0.113.20": ("JP", "KDDI CORPORATION"),
                  "203.0.113.21": ("US", "Comcast Cable Communications, LLC")}
        with self.place(places):
            visits.observe(scope("/trip-country.html", "203.0.113.20"), 200)
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.21"), "/trip-country.html", 9, "v2")
        page = [e for e in _events() if e.get("path") == "/trip-country.html"]
        self.assertNotEqual(page[0]["visitor"], page[1]["visitor"])

    def test_a_reader_with_their_own_view_keeps_it(self):
        places = {"203.0.113.30": ("JP", "KDDI CORPORATION"),
                  "203.0.113.31": ("JP", "KDDI CORPORATION")}
        with self.place(places):
            visits.observe(scope("/trip-own.html", "203.0.113.30"), 200)
            visits.observe(scope("/trip-own.html", "203.0.113.31"), 200)
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.30"), "/trip-own.html", 12, "v3")
        page = [e for e in _events() if e.get("path") == "/trip-own.html"]
        first, second, dwell = page
        self.assertEqual(dwell["visitor"], first["visitor"])
        self.assertNotIn("rejoined", dwell)

    def test_the_trip_lists_pages_in_order_with_their_times(self):
        places = {"203.0.113.40": ("JP", "Saba Capital Management, L.P.")}
        with self.place(places):
            visits.observe(scope("/trip-a.html", "203.0.113.40", referer="https://www.linkedin.com/"), 200)
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.40"), "/trip-a.html", 30, "a1")
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.40"), "/trip-a.html", 45, "a1")
            visits.observe(scope("/trip-b.html", "203.0.113.40",
                                 referer="https://ploveranalytics.com/trip-a.html"), 200)
        rows = [r for r in visit_trips.recent()["readers"] if r["steps"][0]["path"] == "/trip-a.html"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual([s["path"] for s in row["steps"]], ["/trip-a.html", "/trip-b.html"])
        self.assertEqual(row["steps"][0]["seconds"], 45, "the longest report of one view wins")
        self.assertIsNone(row["steps"][1]["seconds"])
        self.assertEqual(row["organisation"], "Saba Capital Management, L.P.")
        self.assertEqual(row["pages"], 2)

    def test_an_unconfirmed_visit_from_a_bank_is_listed_but_not_counted(self):
        # The bank's network stopped the page's report: listed, marked, not counted.
        places = {"203.0.113.80": ("GB", "SMBC Bank International plc"),
                  "203.0.113.81": ("GB", "Example Data Management sal")}
        with self.place(places):
            visits.observe(scope("/trip-bank.html", "203.0.113.80"), 200)
            visits.observe(scope("/trip-scrape.md", "203.0.113.81"), 200)
        recent = visit_trips.recent()
        bank = [r for r in recent["readers"] if r["steps"][0]["path"] == "/trip-bank.html"][0]
        self.assertFalse(bank["confirmed"])
        self.assertEqual(bank["organisation"], "SMBC Bank International plc")
        self.assertFalse([r for r in recent["readers"] if r["steps"][0]["path"] == "/trip-scrape.md"],
                         "a script reading the .md copy is not listed as a reader")
        self.assertEqual(recent["unconfirmed_total"],
                         sum(1 for r in recent["readers"] if not r["confirmed"]))
        visits._reset_folds()
        orgs = dict((o["key"], o) for o in visits.summary(7)["organisations"])
        self.assertEqual(orgs["SMBC Bank International plc"]["confirmed"], 0)
        self.assertNotIn("Example Data Management sal", orgs)

    def test_machines_are_listed_apart_and_named(self):
        bot = scope("/trip-bot.html", "203.0.113.50")
        bot["headers"] = [(b"user-agent", b"LinkedInBot/1.0 (compatible; Mozilla/5.0)")]
        with self.place({"203.0.113.50": ("US", "LinkedIn Corporation")}):
            visits.observe(bot, 200)
        recent = visit_trips.recent()
        self.assertFalse([r for r in recent["readers"] if r["steps"][0]["path"] == "/trip-bot.html"])
        names = [m["name"] for m in recent["machines"] if "/trip-bot.html" in m["pages"]]
        self.assertEqual(names, ["LinkedIn link preview"])

    def test_a_signed_in_reader_carries_the_email_domain_only(self):
        cookie = "%s=open-session" % visits.SESSION_COOKIE
        with self.place({"203.0.113.60": ("SG", "Singtel Fibre Broadband")}), \
                mock.patch("app.accounts.session_is_live", side_effect=lambda raw: raw == "open-session"), \
                mock.patch("app.accounts.session_domain", side_effect=lambda raw: "fund.example"):
            visits.observe(scope("/trip-member.html", "203.0.113.60", cookie=cookie), 200)
        event = [e for e in _events() if e.get("path") == "/trip-member.html"][0]
        self.assertEqual(event["member_domain"], "fund.example")
        self.assertNotIn("@", json.dumps(event))
        row = [r for r in visit_trips.recent()["readers"] if r["steps"][0]["path"] == "/trip-member.html"][0]
        self.assertEqual(row["member_domain"], "fund.example")

    def test_the_summary_lists_organisations_and_domains(self):
        cookie = "%s=open-session" % visits.SESSION_COOKIE
        with self.place({"203.0.113.70": ("US", "University of Kansas")}), \
                mock.patch("app.accounts.session_is_live", side_effect=lambda raw: raw == "open-session"), \
                mock.patch("app.accounts.session_domain", side_effect=lambda raw: "ku.example"):
            visits.observe(scope("/trip-org.html", "203.0.113.70", cookie=cookie), 200)
        visits._reset_folds()
        out = visits.summary(7)
        org = [o for o in out["organisations"] if o["key"] == "University of Kansas"][0]
        self.assertEqual(org["kind"], visit_orgs.ORGANISATION)
        self.assertIn("/trip-org.html", [p["path"] for p in org["pages"]])
        self.assertIn("ku.example", [d["key"] for d in out["member_domains"]])


class AccountDomainTest(unittest.TestCase):
    def test_domain_of_an_open_session(self):
        from app import accounts
        with mock.patch.object(accounts, "enabled", return_value=True), \
                mock.patch.object(accounts, "_allowed", return_value=True), \
                mock.patch.object(accounts, "conn") as conn:
            accounts._LIVE_SESSIONS.clear()
            conn.return_value.execute.return_value.fetchone.return_value = {"email": "Reader@Fund.Example"}
            self.assertEqual(accounts.session_domain("raw-token"), "fund.example")
            self.assertTrue(accounts.session_is_live("raw-token"))
            accounts._LIVE_SESSIONS.clear()
            conn.return_value.execute.return_value.fetchone.return_value = None
            self.assertIsNone(accounts.session_domain("other-token"))
            self.assertFalse(accounts.session_is_live("other-token"))
        accounts._LIVE_SESSIONS.clear()


class ReaderEvidenceTest(unittest.TestCase):
    """The three ways a real reader used to be lost (November 2026): a closing
    report that never arrived, a network that blocks the page's scripts, and
    Apple's iCloud Private Relay read as a data centre."""

    def setUp(self):
        visits.flush()
        visits._VIEWS.clear()
        visits._ALIASES.clear()

    def place(self, table):
        return mock.patch.object(visits.geoip, "lookup", side_effect=lambda ip: table.get(ip, (None, None)))

    def test_an_early_report_confirms_a_reader_without_a_reading_time(self):
        places = {"203.0.113.90": ("CA", "Bell Canada")}
        with self.place(places):
            visits.observe(scope("/evidence-early.html", "203.0.113.90"), 200)
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.90"),
                                "/evidence-early.html", 5, "e1", early=True)
        early = [e for e in _events() if e.get("view") == "e1"]
        self.assertTrue(early and early[0]["early"])
        row = [r for r in visit_trips.recent()["readers"]
               if r["steps"][0]["path"] == "/evidence-early.html"][0]
        self.assertTrue(row["confirmed"], "the closing report was lost; the early one is proof enough")
        self.assertIsNone(row["seconds"], "five seconds in is not how long they read")

    def test_the_closing_report_still_sets_the_reading_time(self):
        places = {"203.0.113.91": ("CA", "Bell Canada")}
        with self.place(places):
            visits.observe(scope("/evidence-closed.html", "203.0.113.91"), 200)
            ping = scope("/api/v1/visit/ping", "203.0.113.91")
            visits.record_dwell(ping, "/evidence-closed.html", 5, "e2", early=True)
            visits.record_dwell(ping, "/evidence-closed.html", 40, "e2")
        row = [r for r in visit_trips.recent()["readers"]
               if r["steps"][0]["path"] == "/evidence-closed.html"][0]
        self.assertEqual(row["seconds"], 40)

    def test_the_summary_reading_times_leave_early_reports_out(self):
        _forget()
        places = {"203.0.113.92": ("CA", "Bell Canada")}
        with self.place(places):
            visits.observe(scope("/evidence-median.html", "203.0.113.92"), 200)
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.92"),
                                "/evidence-median.html", 5, "e3", early=True)
        visits._reset_folds()
        before = visits.summary(7)["dwell"]["samples"]
        with self.place(places):
            visits.record_dwell(scope("/api/v1/visit/ping", "203.0.113.92"),
                                "/evidence-median.html", 30, "e3")
        visits._reset_folds()
        self.assertEqual(visits.summary(7)["dwell"]["samples"], before + 1)

    def test_a_drawn_page_tells_blocked_scripts_from_a_script(self):
        places = {"203.0.113.93": ("MY", "Universiti Teknikal Malaysia Melaka"),
                  "203.0.113.94": ("MY", "Universiti Teknikal Malaysia Melaka")}
        with self.place(places):
            visits.observe(scope("/evidence-blocked.html", "203.0.113.93"), 200)
            visits.observe(scope(visits.DRAWN_PATH, "203.0.113.93"), 200)
            visits.observe(scope("/evidence-script.html", "203.0.113.94"), 200)
        readers = visit_trips.recent()["readers"]
        blocked = [r for r in readers if r["steps"][0]["path"] == "/evidence-blocked.html"][0]
        script = [r for r in readers if r["steps"][0]["path"] == "/evidence-script.html"][0]
        self.assertFalse(blocked["confirmed"])
        self.assertTrue(blocked["drawn"])
        self.assertFalse(script["drawn"])
        self.assertEqual([s["path"] for s in blocked["steps"]], ["/evidence-blocked.html"],
                         "the pixel is not a page they read")

    def test_the_pixel_is_an_uncached_gif(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.visits_api import router
        api = FastAPI()
        api.include_router(router)
        response = TestClient(api).get(visits.DRAWN_PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/gif")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.content.startswith(b"GIF89a"))
        css = (pathlib.Path(__file__).resolve().parents[1] / "web" / "assets" / "tokens.css").read_text()
        self.assertIn(visits.DRAWN_PATH, css, "every page asks for it through tokens.css")


class PrivateRelayTest(unittest.TestCase):
    """Apple's list turns a Cloudflare address into a Safari reader."""

    LIST = (b"172.224.226.0/27,GB,GB-EN,London,\n"
            b"2a02:26f7:b3c0:4000::/64,JP,JP-13,Tokyo,\n"
            b"104.28.0.0/31,,,,\n")

    def test_listed_addresses_are_the_relay_in_their_own_country(self):
        relay = visits.geoip._parse_relay(self.LIST)
        dbip = (visits.geoip._Ranges(), visits.geoip._Ranges())
        with mock.patch.dict(visits.geoip._STATE, {"tables": dbip, "relay": relay}):
            self.assertEqual(visits.geoip.lookup("172.224.226.5"), ("GB", visits.geoip.RELAY_NETWORK))
            self.assertEqual(visits.geoip.lookup("2a02:26f7:b3c0:4000::1"),
                             ("JP", visits.geoip.RELAY_NETWORK))
            self.assertEqual(visits.geoip.lookup("104.28.0.1")[1], visits.geoip.RELAY_NETWORK,
                             "a range with no country is still the relay")
            self.assertEqual(visits.geoip.lookup("172.224.227.5"), (None, None))

    def test_the_relay_is_a_provider_and_its_readers_count(self):
        self.assertFalse(visits.is_hosting(visits.geoip.RELAY_NETWORK))
        self.assertEqual(visit_orgs.kind(visits.geoip.RELAY_NETWORK), visit_orgs.PROVIDER)
        self.assertTrue(visits._is_reader(True, visits.geoip.RELAY_NETWORK))
        self.assertFalse(visits._is_reader(True, "Cloudflare, Inc."))


if __name__ == "__main__":
    unittest.main()
