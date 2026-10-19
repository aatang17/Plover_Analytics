# -*- coding: utf-8 -*-
"""A study as a zip to run on the person's own machine.

Four files: the input series as a CSV (read from Plover when the zip is
made), the study's query as plain DuckDB SQL with Plover's helper words
already expanded, a short Python script that runs it, and a note on what each
file is. No AI and no tokens: what they change locally stays theirs, which is
how a person who works in PyCharm wants it.
"""
import csv
import io
import re
import zipfile

from .. import analysis

RUNNER = '''"""Run this study on your own machine. Needs DuckDB: pip install duckdb

inputs.csv holds the input series, one column each (blank = missing), as read
from Plover when this file was downloaded; query.sql is the study's query as
plain SQL, with Plover's helper words already expanded. Edit either and run
again: python run.py
"""
import csv

import duckdb

con = duckdb.connect()
con.execute("CREATE TABLE data AS SELECT * FROM read_csv('inputs.csv', header=true, nullstr='')")
con.execute("ALTER TABLE data ALTER date TYPE DATE")
cur = con.execute(open("query.sql").read())
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
with open("result.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    w.writerows(rows)
print("%d rows -> result.csv" % len(rows))
for r in rows[-5:]:
    print(r)
'''


def folder_name(title):
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60] or "study"


def readme(title, spec, sources):
    lines = [title, "", "Inputs (read from Plover at download; the first row of inputs.csv names them):"]
    for s in sources:
        lines.append("  %s = %s (%s), %s, %s" % (s["name"], s["series"], s.get("title") or "",
                                                 s.get("release") or "latest", s.get("credit") or ""))
    lines += ["", "Query: query.sql (Plover's helpers expanded to plain DuckDB SQL).",
              "As written on Plover: " + (spec.get("query") or "SELECT * FROM data"), "",
              "Run: pip install duckdb; python run.py  ->  result.csv", "",
              "Missing values are blank, never zero. Official figures are as published; every "
              "column the query calculates is a Plover calculation, by the query above."]
    return "\n".join(lines) + "\n"


def build(title, spec):
    """(zip bytes, folder name). Raises analysis.AnalysisError when the
    inputs cannot be read or the query is not one SELECT."""
    rows, columns, sources = analysis.load(spec.get("series"), spec.get("start"), spec.get("end"),
                                           spec.get("as_of"), spec.get("join") or "both",
                                           spec.get("monthly"))
    sql = analysis.expand(analysis._check_query(spec.get("query")))
    name = folder_name(title)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
        z.writestr(name + "/inputs.csv", out.getvalue())
        z.writestr(name + "/query.sql", sql + "\n")
        z.writestr(name + "/run.py", RUNNER)
        z.writestr(name + "/README.txt", readme(title, spec, sources))
    return buf.getvalue(), name
