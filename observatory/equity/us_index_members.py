# -*- coding: utf-8 -*-
"""Index member lists from the funds that track them.

An index provider sells its constituent list; the exchange-traded funds that
track an index publish their holdings every day for free. State Street's SPDR
funds publish one workbook per fund in the same layout (name, ticker, weight,
'As of' date), which equity/sec_proxy_pay.py already reads for SPY. This
loader takes the same file for any SPDR fund named below and stores one
snapshot per holdings date in data/sec.duckdb (us_index_snapshots /
us_index_members, the tables SPY already uses), with the raw workbook
archived with its SHA-256 under data/raw/us-index/{date}/.

  FUNDS    index key -> (fund ticker, index name as stored, fewest companies a
           good file can hold)

Tickers are matched to SEC CIKs through sec_companies (equity/sec_universe.py),
so the universe loader must have run. A file that matches too few companies is
refused and the previous snapshot stays in force.

Run from observatory/ against a copy of the served database:
    ./.venv/bin/python equity/us_index_members.py --db data/sec.work.duckdb [--funds dia,spy]
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import os
import sys
import urllib.request

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEC_DB_PATH", os.path.join(HERE, "..", "data", "sec.duckdb"))
RAW_ROOT = os.path.join(HERE, "..", "data", "raw", "us-index")
URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-%s.xlsx"
FUNDS = {
    "sp500": ("spy", "S&P 500 (SPY holdings)", 450),
    "dow": ("dia", "Dow Jones Industrial Average (DIA holdings)", 28),
}

_spec = importlib.util.spec_from_file_location("sec_proxy_pay", os.path.join(HERE, "sec_proxy_pay.py"))
_spp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_spp)
parse_holdings = _spp.parse_holdings
SCHEMA_SQL = _spp.SCHEMA_SQL if hasattr(_spp, "SCHEMA_SQL") else None


class IndexError_(Exception):
    pass


def fetch(fund):
    req = urllib.request.Request(URL % fund, headers={"User-Agent": "Mozilla/5.0 (compatible; data archive)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def store(con, key, body):
    fund, index_name, minimum = FUNDS[key]
    sha = hashlib.sha256(body).hexdigest()
    as_of, holdings = parse_holdings(body)
    last = con.execute("SELECT snapshot_id, as_of, sha256 FROM us_index_snapshots WHERE index_name = ? "
                       "ORDER BY snapshot_id DESC LIMIT 1", [index_name]).fetchone()
    if last and (last[1] == as_of or last[2] == sha):
        print("  %s: holdings as of %s already stored (snapshot %d)" % (fund.upper(), last[1], last[0]))
        return last[0]
    folder = os.path.join(RAW_ROOT, dt.date.today().isoformat())
    os.makedirs(folder, exist_ok=True)
    raw = os.path.join(folder, "%s-holdings-%s.xlsx" % (fund, as_of.isoformat()))
    with open(raw, "wb") as f:
        f.write(body)
    cik_of = {}
    for cik, ticker, tickers in con.execute("SELECT cik, ticker, tickers FROM sec_companies").fetchall():
        for t in (tickers or ticker or "").split(","):
            if t:
                cik_of.setdefault(t.upper(), cik)
    members, unmatched = {}, []
    for name, ticker, weight in holdings:
        cik = cik_of.get(ticker.upper().replace(".", "-"))
        if cik is None:
            unmatched.append("%s (%s)" % (name, ticker))
            continue
        m = members.setdefault(cik, {"tickers": [], "name": name, "weight": 0.0})
        m["tickers"].append(ticker.replace(".", "-"))
        m["weight"] += weight or 0.0
    if len(members) < minimum:
        raise IndexError_("%s holdings matched only %d companies; expected at least %d" % (fund.upper(), len(members), minimum))
    # the table's own highest id, not the sequence: the sequence was not always
    # advanced by earlier writers, and a reused id is a primary-key clash
    sid = con.execute("SELECT coalesce(max(snapshot_id), 0) + 1 FROM us_index_snapshots").fetchone()[0]
    con.execute("BEGIN")
    try:
        con.execute("INSERT INTO us_index_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [sid, index_name, URL % fund, as_of, dt.datetime.utcnow().replace(microsecond=0), sha,
                     os.path.relpath(raw, os.path.join(HERE, "..")), len(holdings), len(members),
                     "; ".join(unmatched) or None])
        con.executemany("INSERT INTO us_index_members VALUES (?,?,?,?,?,?)",
                        [(sid, cik, m["tickers"][0], ",".join(m["tickers"]), m["name"], round(m["weight"], 6))
                         for cik, m in members.items()])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("  %s: snapshot %d, holdings as of %s — %d companies; unmatched: %s"
          % (fund.upper(), sid, as_of, len(members), "; ".join(unmatched) or "none"))
    return sid


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--funds", default="dow,sp500", help="comma-separated index keys: " + ", ".join(FUNDS))
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db)
    failed = []
    try:
        for key in [k.strip() for k in args.funds.split(",") if k.strip()]:
            if key not in FUNDS:
                print("unknown index %r" % key)
                failed.append(key)
                continue
            try:
                store(con, key, fetch(FUNDS[key][0]))
            except Exception as e:                                # noqa: BLE001 — one fund failing keeps the others
                print("  ATTENTION %s: %s — previous snapshot stays in force" % (key, e))
                failed.append(key)
    finally:
        con.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
