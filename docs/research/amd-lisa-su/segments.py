"""AMD revenue and operating income by reportable segment, as tagged in each 10-K's XBRL instance.

SEC company facts carry no dimensions, so segment figures come from the filings themselves:
each 10-K's instance document is downloaded from EDGAR, archived with its SHA-256 under
data/raw/, and every annual segment fact on StatementBusinessSegmentsAxis is written out
as filed. Nothing is recomputed. Appends one log.md row per filing.
"""
import csv
import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "..", "..", "..", "observatory", ".env")
UA = [l.split("=", 1)[1].strip().strip('"') for l in open(ENV) if l.startswith("EDGAR_USER_AGENT")][0]
FILINGS = {  # 10-K accession -> fiscal year (from data/AMD_*.csv, form 10-K)
    "0001193125-15-054362": 2014, "0000002488-16-000111": 2015, "0000002488-17-000043": 2016,
    "0000002488-18-000042": 2017, "0000002488-19-000011": 2018, "0000002488-20-000008": 2019,
    "0001628280-21-001185": 2020, "0000002488-22-000016": 2021, "0000002488-23-000047": 2022,
    "0000002488-24-000012": 2023, "0000002488-25-000012": 2024, "0000002488-26-000018": 2025,
}
TAGS = {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
        "SalesRevenueGoodsNet", "OperatingIncomeLoss"}
# Filers add a second axis over the years: ConsolidationItemsAxis (operating segments vs
# reconciling items) from FY2019, ProductOrServiceAxis (Gaming inside Client and Gaming) in FY2025.
EXTRA = {"ConsolidationItemsAxis", "ProductOrServiceAxis"}
AXIS = "StatementBusinessSegmentsAxis"


def fetch(url):
    time.sleep(0.2)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def instance_name(items):
    names = [i["name"] for i in items]
    inline = [n for n in names if n.endswith("_htm.xml")]
    if inline:
        return inline[0]
    xs = [n for n in names if n.endswith(".xml") and not re.search(r"(_cal|_def|_lab|_pre)\.xml$|FilingSummary|edgar\.xml", n)]
    return xs[0] if xs else None


def local(tag):
    return tag.split("}")[-1]


def parse(raw):
    root = ET.fromstring(raw)
    ctx = {}
    for c in root.iter():
        if local(c.tag) != "context":
            continue
        dims = {}
        start = end = None
        for e in c.iter():
            n = local(e.tag)
            if n == "explicitMember":
                dims[e.get("dimension").split(":")[-1]] = e.text.strip().split(":")[-1]
            elif n == "startDate":
                start = e.text.strip()
            elif n == "endDate":
                end = e.text.strip()
        ctx[c.get("id")] = (start, end, dims)
    out = []
    for e in root:
        n = local(e.tag)
        if n not in TAGS or e.get("contextRef") not in ctx:
            continue
        start, end, dims = ctx[e.get("contextRef")]
        if not start or AXIS not in dims or len(dims) > 2 or not set(dims) - {AXIS} <= EXTRA:
            continue
        extra = ";".join("%s=%s" % (k, v) for k, v in sorted(dims.items()) if k != AXIS)
        days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        if not 350 <= days <= 380 or e.text is None:
            continue
        out.append((n, start, end, dims[AXIS], extra, float(e.text)))
    return out


def main():
    raw_dir = os.path.join(HERE, "data", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    log = os.path.join(HERE, "log.md")
    kept = [l for l in open(log, encoding="utf-8") if "axis " + AXIS not in l]  # re-run replaces own rows
    open(log, "w", encoding="utf-8").writelines(kept)
    n = sum(1 for l in kept if re.match(r"\| \d+ \|", l))
    rows, logrows = [], []
    for accn, fy in sorted(FILINGS.items(), key=lambda x: x[1]):
        folder = "https://www.sec.gov/Archives/edgar/data/2488/%s/" % accn.replace("-", "")
        items = json.loads(fetch(folder + "index.json"))["directory"]["item"]
        name = instance_name(items)
        raw = fetch(folder + name)
        sha = hashlib.sha256(raw).hexdigest()
        with open(os.path.join(raw_dir, "%s_%s" % (accn, name)), "wb") as f:
            f.write(raw)
        facts = parse(raw)
        seen = set()
        for tag, start, end, member, extra, val in facts:
            key = (tag, end, member, extra)
            if key in seen:
                continue
            seen.add(key)
            rows.append([fy, accn, tag, start, end, member, extra, val, folder + name, sha])
        n += 1
        logrows.append("| %d | %s | GET %s%s | 10-K FY%d, axis %s | %d segment facts | %s | filed 10-K %s, sha256 %s |"
                       % (n, dt.date.today().isoformat(), folder, name, fy, AXIS, len(seen), folder, accn, sha[:16]))
    with open(os.path.join(HERE, "data", "AMD_segments.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("# source: AMD Form 10-K XBRL instances, SEC EDGAR; segment facts as filed on %s\n"
                "# credit: Source: U.S. Securities and Exchange Commission, EDGAR.\n" % AXIS)
        w = csv.writer(f)
        w.writerow(["filing_fy", "accn", "tag", "period_start", "period_end", "segment", "other_dims", "value", "instance_url", "sha256"])
        w.writerows(rows)
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n".join(logrows) + "\n")
    print(len(rows), "segment facts from", len(logrows), "filings")


if __name__ == "__main__":
    main()
