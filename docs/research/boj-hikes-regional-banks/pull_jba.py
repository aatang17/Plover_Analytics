# -*- coding: utf-8 -*-
"""Pull every regional bank's JBA lines from the live API, then one long table.

Bank list and tier: the FSA institution list as held in the equity database
(fi_type regional-1 / regional-2). The observations API keeps at most eight
series per request and drops the rest without saying so, so lines go in two
groups; a series a bank never reports makes the whole request 404, so a failed
group is re-fetched line by line and the absent lines recorded."""
import csv, io, json, os, time, urllib.error, urllib.request
import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "jba")
os.makedirs(OUT, exist_ok=True)
API = "https://ploveranalytics.com/api/v1/jba-banks/observations?series=%s&start=2022-03&format=csv"
GROUPS = [["e020.fy", "e350.fy", "e230.fy", "e240.fy", "e610.fy", "e620.fy", "e630.fy"],
          ["d395", "a015", "b950"],
          ["e030.fy", "e040.fy", "e090.fy", "e360.fy", "d035", "d990"]]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "plover-research"})
    return urllib.request.urlopen(req, timeout=90).read().decode("utf-8")


def parse(body):
    data = [l for l in body.splitlines() if not l.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(data))))


con = duckdb.connect("/Users/aatang17/Japan_data/Japan_data/observatory/data/equity.duckdb", read_only=True)
banks = con.execute("select fi_code, name_ja, fi_type from eq_bank_irrbb_sources "
                    "where fi_type in ('regional-1','regional-2') order by fi_code").fetchall()
banks += [("regional", "地方銀行（合計）", "aggregate"), ("regional-2", "第二地方銀行（合計）", "aggregate")]
json.dump([list(b) for b in banks], open(os.path.join(HERE, "data", "bank_list.json"), "w"),
          ensure_ascii=False, indent=1)

rows, absent_log = [], []
for code, name, typ in banks:
    for gi, lines in enumerate(GROUPS):
        path = os.path.join(OUT, "%s.g%d.csv" % (code, gi))
        if not os.path.exists(path):
            try:
                bodies = [get(API % ",".join("s.%s.%s" % (code, l) for l in lines))]
                absent = []
            except urllib.error.HTTPError:
                bodies, absent = [], []
                for l in lines:
                    try:
                        bodies.append(get(API % ("s.%s.%s" % (code, l))))
                    except urllib.error.HTTPError:
                        absent.append(l)
                    time.sleep(0.2)
            with open(path, "w", encoding="utf-8") as f:
                f.write(("# absent lines (no such series for this bank): %s\n" % ",".join(absent)) if absent else "")
                f.write("\n#=====\n".join(bodies))
            time.sleep(0.3)
        text = open(path, encoding="utf-8").read()
        if text.startswith("# absent lines"):
            absent_log.append((code, name, text.splitlines()[0][len("# absent lines (no such series for this bank): "):]))
        for body in text.split("\n#=====\n"):
            got = parse(body)
            cols = [c for c in (got[0].keys() if got else []) if c != "period"]
            want = {"s.%s.%s" % (code, l) for l in lines}
            for r in got:
                for k in cols:
                    if r[k] not in ("", None):
                        rows.append([code, name, typ, r["period"], k.split(".", 2)[2], float(r[k])])

with open(os.path.join(HERE, "data", "jba_banks_long.csv"), "w", newline="", encoding="utf-8") as f:
    f.write("# Source: Japanese Bankers Association — Analysis of Financial Statements of All Banks (全国銀行財務諸表分析), via Plover jba-banks\n")
    f.write("# Release: Data through March 2026 (sha256 b4e39587...), retrieved 2026-09-24 from the live API; per-bank CSVs with full headers in data/jba/\n")
    f.write("# Non-consolidated (s.) basis; ¥mn as published; blank = not published (no row)\n")
    w = csv.writer(f); w.writerow(["fi_code", "name_ja", "tier", "period", "line", "yen_mn"]); w.writerows(rows)
json.dump(absent_log, open(os.path.join(HERE, "data", "jba_absent_lines.json"), "w"), ensure_ascii=False, indent=1)
per_line = {}
for r in rows:
    per_line.setdefault(r[4], set()).add(r[0])
print("banks:", len(banks), "rows:", len(rows), "banks per line:", {k: len(v) for k, v in sorted(per_line.items())})
