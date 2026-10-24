# -*- coding: utf-8 -*-
"""US executive pay — the proxy's pay-versus-performance table, S&P 500 wide.

equity/sec_proxy_pay.py reads every S&P 500 member's proxy statements
(DEF 14A, 2023 on) into data/sec.duckdb. This module serves them:

  1. **The index** (`/api/v1/us/pay`): one row per member for its latest
     fiscal year — the CEO's reported pay and "compensation actually paid",
     the other named executives' averages, and shareholder return.
  2. **One company** (`/api/v1/us/pay/{ticker}`): every fiscal year its
     proxies cover, each CEO separately where a year had more than one, and
     every proxy read with its status.

What the figures are
--------------------
All exactly as filed in the SEC's `ecd` taxonomy (Item 402(v)); nothing is
recomputed. "Total pay" is the Summary Compensation Table total. "Actually
paid" is the SEC-defined figure the company computes by marking equity awards
to market, so it can be far above total pay or below zero. Shareholder return
is the value at the fiscal year end of $100 invested at the start of the
table's first year. Where a year had two CEOs the proxy tags each by name;
their figures are listed separately and never summed.

Point in time
-------------
Each proxy repeats up to five years. `basis=latest` (default) takes each
value from the most recent proxy that carries it; `basis=first` from the
first; `as_of=YYYY-MM-DD` drops proxies filed after that day first.
"""
import datetime
import re

from fastapi import APIRouter, HTTPException, Query

from . import sec_api, us_link

router = APIRouter(prefix="/api/v1/us/pay", tags=["US executive pay"])

STALE_AFTER_DAYS = 3      # app/backfill.py checks every member every day

PROVENANCE = {
    "trust": "official",
    "note": ("Figures exactly as tagged in each company's proxy statement (DEF 14A) "
             "under the SEC's executive-compensation taxonomy (Item 402(v), pay versus "
             "performance). Read from the XBRL instance EDGAR publishes beside each "
             "filing; every file is archived with its SHA-256. Members: the S&P 500 as "
             "held by State Street's SPDR S&P 500 ETF (SPY) on the snapshot date."),
    "credit": "Source: U.S. Securities and Exchange Commission, EDGAR; State Street (SPY holdings).",
    "url_pattern": "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/",
}

CALC = {
    "total_pay": ("CEO total pay = the Summary Compensation Table total, as tagged "
                  "(ecd:PeoTotalCompAmt). Other executives = the average over the named "
                  "executive officers other than the CEO (ecd:NonPeoNeoAvgTotalCompAmt)."),
    "actually_paid": ("Compensation actually paid = the SEC's Item 402(v) figure, computed by "
                      "the company: total pay with equity awards re-valued to fair value "
                      "(ecd:PeoActuallyPaidCompAmt). It can exceed total pay many times or be "
                      "negative when the share price falls."),
    "tsr": ("Shareholder return = the value at the fiscal year end of $100 invested at the start "
            "of the table's first year, dividends reinvested (ecd:TotalShareholderRtnAmt); peer "
            "group the same for the company's chosen peers."),
    "basis": ("latest = each value from the most recent proxy that reports it; first = from "
              "the first. Selection among filed values, never a calculation. Shareholder return "
              "is the exception: it is cumulative from a base that moves with every proxy, so "
              "all its years come from the latest proxy that reports it, whatever the basis, and "
              "tsr_base names the day the $100 is invested."),
    "ceos": ("A year with more than one CEO is tagged per person; each is listed and none is "
             "added together. The index view shows the CEO in office at the fiscal year end with "
             "that person's own figures: the one whose tagged figures run to the year end when "
             "another's stop short, else the one certifying the year's 10-K as principal "
             "executive; with neither, every one is listed. Co-CEOs at the year end get a line each."),
    "names": ("Names are spelled out from the documents, never from a figure: a surname or "
              "placeholder as tagged (\"Mr. Davis\", \"Peo1\") takes the full name the latest "
              "proxy writes most often beside the CEO's title, or the name signed on the 10-K "
              "certification (Exhibit 31) for that year; one spelling per person, the signed one "
              "where there is one."),
}

# field -> (prefix, tag); non-dimensional facts only
FIELDS = [
    ("ceo_total_pay", "ecd", "PeoTotalCompAmt"),
    ("ceo_actually_paid", "ecd", "PeoActuallyPaidCompAmt"),
    ("neo_avg_total_pay", "ecd", "NonPeoNeoAvgTotalCompAmt"),
    ("neo_avg_actually_paid", "ecd", "NonPeoNeoAvgCompActuallyPaidAmt"),
    ("net_income", "us-gaap", "NetIncomeLoss"),
    ("measure_value", "ecd", "CoSelectedMeasureAmt"),
]
# Shareholder return is cumulative from the start of each proxy's first
# year, and that start moves forward a year with every proxy (Caterpillar's
# 2025 proxy counts from 2020, its 2026 proxy from 2021). Values from two
# proxies are on two bases, so the return series is read from ONE proxy —
# the latest filed — never assembled year by year.
TSR_FIELDS = [("tsr", "TotalShareholderRtnAmt"), ("peer_tsr", "PeerGroupTotalShareholderRtnAmt")]
PEO_AXIS = "ecd:ExecutiveCategoryAxis=ecd:PeoMember"
NEO_AXIS = "ecd:ExecutiveCategoryAxis=ecd:NonPeoNeoMember"
INDIVIDUAL = "ecd:IndividualAxis="


def _require():
    cur = sec_api._cur()
    names = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if not {"sec_px_facts", "us_index_members"} <= names:
        raise HTTPException(503, "US executive pay not loaded on this server yet")
    return cur


def _d(v):
    return v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v


def _date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")


def _check(name, value, allowed):
    v = (value or "").strip().lower()
    if v not in allowed:
        raise HTTPException(400, "%s must be one of %s" % (name, ", ".join(allowed)))
    return v


def _snapshot(cur):
    row = sec_api._rows(cur, """SELECT snapshot_id, index_name, source_url, as_of, fetched_at,
                                       holdings, companies, unmatched
                                FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)'
                                ORDER BY snapshot_id DESC LIMIT 1""")
    if not row:
        raise HTTPException(503, "no S&P 500 member list stored yet")
    s = row[0]
    for k in ("as_of", "fetched_at"):
        s[k] = _d(s[k])
    return s


def _picked(cur, ciks, basis, ceiling):
    """One value per (company, fiscal year end, prefix, tag, dims): the proxy
    chosen by basis, then the most precise of that proxy's duplicates."""
    order = "x.filed DESC, x.accn DESC" if basis == "latest" else "x.filed, x.accn"
    ph = ",".join("?" * len(ciks))
    return sec_api._rows(cur, """
        SELECT * EXCLUDE (rn) FROM (
            SELECT f.cik, f.prefix, f.tag, f.period_start, f.period_end, f.dims, f.value_num,
                   f.value_text, f.unit, f.accn, x.filed,
                   row_number() OVER (
                       PARTITION BY f.cik, f.prefix, f.tag, f.period_end, f.dims
                       ORDER BY %s,
                                CASE WHEN f.decimals = 'INF' THEN 99
                                     ELSE TRY_CAST(f.decimals AS INTEGER) END DESC NULLS LAST) AS rn
            FROM sec_px_facts f JOIN sec_px_filings x ON x.accn = f.accn AND x.status = 'ok'
            WHERE f.cik IN (%s) AND f.period_start IS NOT NULL
              -- A CEO who served part of a year has their name and pay dated
              -- to that part (Caterpillar's Umpleby to 30 April, Creed from
              -- May), so those are read at any length and placed by date.
              AND (date_diff('day', f.period_start, f.period_end) BETWEEN 300 AND 380
                   OR f.tag = 'PeoName'
                   OR (f.tag IN ('PeoTotalCompAmt', 'PeoActuallyPaidCompAmt')
                       AND f.dims LIKE '%%ecd:IndividualAxis=%%'))
              AND (f.prefix = 'ecd' OR (f.prefix = 'us-gaap' AND f.tag = 'NetIncomeLoss'))
              AND (CAST(? AS DATE) IS NULL OR x.filed <= ?))
        WHERE rn = 1""" % (order, ph), list(ciks) + [ceiling, ceiling])


def _individual(dims):
    for part in (dims or "").split("|"):
        if part.startswith(INDIVIDUAL):
            return part[len(INDIVIDUAL):]
    return None


def _member_label(member):
    """'unh:StephenHemsleyMember' -> 'Stephen Hemsley': the filer's own
    identifier, spaced, for a CEO whose name is tagged nowhere else."""
    local = member.split(":", 1)[-1]
    if local.endswith("Member"):
        local = local[:-len("Member")]
    local = local.replace(".", ". ")
    return " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", local).split())


SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
HONORIFIC = re.compile(r"^(?:Mr|Mrs|Ms|Miss|Dr|Messrs)\.?(?=[\s.]|[A-Z])\.?\s*")
CEO_TITLE = re.compile(r"Chief Executive|Principal Executive|\bCEO\b")


def _surname(name):
    words = [w for w in re.split(r"[\s.,]+", (name or "").replace("’", "'"))
             if w and w.lower() not in SUFFIXES]
    return words[-1].lower() if words else ""


def _plain(name):
    """A tagged name without the title it is often tagged with ("Mr. Davis"
    -> "Davis"), for a proxy whose document has not been read for names."""
    return HONORIFIC.sub("", name).strip() or name


def _people(cur, ciks):
    """The names read from the documents (sec_px_names): {cik: {"tagged": {as
    tagged: [full names]}, "certs": {fiscal year end: [(name, title)]}}}.
    Labels only — no figure comes from them. Empty before the extractor's
    name readers have run."""
    have = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    if "sec_px_names" not in have:
        return {}
    out = {}
    for cik, kind, tagged, seq, name, title, end, accn, version in cur.execute("""
            SELECT cik, kind, tagged, seq, name, title, period_end, accn, parser_version
            FROM sec_px_names WHERE cik IN (%s) AND name IS NOT NULL
            ORDER BY cik, kind, filed, accn, parser_version, tagged, seq""" % ",".join("?" * len(ciks)),
            list(ciks)).fetchall():
        o = out.setdefault(cik, {"tagged": {}, "certs": {}, "_read": {}})
        if kind == "proxy":
            # The latest proxy, read by the latest readers, replaces any
            # earlier reading of the same tagged string.
            if o["_read"].get(tagged) != (accn, version):
                o["_read"][tagged] = (accn, version)
                o["tagged"][tagged] = {}
            o["tagged"][tagged][seq] = name
        else:
            # "I, Ariane Gorin, Chief Executive Officer of Expedia Group, Inc.,
            # certify": the name is what comes before the title.
            name = re.split(r",(?!\s*(?:Jr|Sr|II|III|IV)\b)", name)[0].strip()
            o["certs"].setdefault(end, []).append((name, title))
    for o in out.values():
        o.pop("_read")
        o["tagged"] = {k: [v[i] for i in sorted(v)] for k, v in o["tagged"].items()}
    return out


def _signers(certs, end):
    """Who certifies the 10-K for this year as principal executive (Netflix:
    both co-CEOs). Where no title could be read, every signer — the caller
    only ever matches them against the year's own CEOs."""
    rows = certs.get(end, [])
    titled = [n for n, t in rows if t]
    return [n for n, t in rows if t and CEO_TITLE.search(t)] if titled else [n for n, _ in rows]


def _sole_signer(certs, end):
    """The one principal executive certifying the year, or None: by title,
    else the first exhibit (31.1 is the CEO's by convention)."""
    rows = certs.get(end, [])
    peo = [n for n, t in rows if t and CEO_TITLE.search(t)]
    if any(t for _, t in rows):
        return peo[0] if len(set(peo)) == 1 else None
    return rows[0][0] if rows else None


def _spelling(name, certs):
    """One spelling per person: the 10-K signature where the person signed
    one with a written-out given name ("Joe Creed" -> "Joseph E. Creed"; not
    "A. Manifold" for Albert Manifold), matched by surname and given name."""
    s = _surname(name)
    words = [w for w in name.split() if w.lower().strip(".,") not in SUFFIXES]
    for end in sorted(certs, reverse=True):
        for n, _ in certs[end]:
            given = [w for w in n.split()[:-1] if re.fullmatch(r"[^\W\d_][^\W\d_'’\-]+", w)]
            if _surname(n) != s or not given:
                continue
            if len(words) < 2 or n[:1] == name[:1] or given[0] in words:
                return n
    return name


def _tidy(name):
    """House style for a name: "Jr."/"Sr." after a comma, numerals without
    one ("Walter W. Bettinger II"), a full stop after a lone initial."""
    n = re.sub(r",?\s+(Jr|Sr)\.?$", r", \1.", name)
    n = re.sub(r",\s+(II|III|IV)$", r" \1", n)
    return re.sub(r"\b([A-Z])(?=\s+[A-Z][a-z])", r"\1.", n)


def _years(rows, names=None):
    """{cik: {period_end: year dict}} from picked facts.

    CEO names are matched to the year they fall in, and to a CEO's own
    figures by the individual the filer tagged them with — or, where the
    name carries no such tag, by surname within the member's identifier.
    `names` (from _people) spells out what the tagging abbreviates; each
    year then marks which CEOs were in office at its end.
    """
    names = names or {}
    out, tagged_names, people = {}, {}, {}

    def year_of(r):
        return out.setdefault(r["cik"], {}).setdefault(r["period_end"], {
            "fiscal_year_end": _d(r["period_end"]), "fiscal_year_start": _d(r["period_start"]),
            "_start": r["period_start"], "_end": r["period_end"],
            "values": dict({f: None for f, _, _ in FIELDS}, **{f: None for f, _ in TSR_FIELDS}),
            "sources": {}, "ceo_names": [],
            "ceos": {}, "measure_name": None, "measure_unit": None})

    for r in rows:
        dims, tag = r["dims"] or "", r["tag"]
        ind = _individual(dims)
        is_peo = PEO_AXIS in dims or (ind is not None and NEO_AXIS not in dims)
        if tag == "PeoName":
            # Some filers tag every named executive's name with PeoName under
            # the non-CEO member; only the CEO's count.
            if dims == "" or is_peo:
                # A leading footnote mark is not part of a name ("(1)Ms.
                # Harris", "1. Patricia K. Poppe"); a 'name' longer than any
                # name is a tagged table (Crown Castle), not a person.
                raw = r["value_text"] or ""
                name = re.sub(r"^\(?\d{1,2}\)?\.?\s*", "", " ".join(raw.split()))
                if name and len(name) <= 80:
                    tagged_names.setdefault(r["cik"], []).append((r["period_end"], ind, raw, name))
            continue
        days = (r["period_end"] - r["period_start"]).days
        if tag in ("PeoTotalCompAmt", "PeoActuallyPaidCompAmt") and ind and is_peo:
            people.setdefault(r["cik"], []).append(r)
            if 300 <= days <= 380:
                year_of(r)          # a year whose CEO figures are all tagged by person
            continue
        if not 300 <= days <= 380:
            continue
        y = year_of(r)
        if tag == "CoSelectedMeasureName" and dims == "":
            y["measure_name"] = " ".join((r["value_text"] or "").split())
            continue
        if dims:
            continue
        for field, prefix, t in FIELDS:
            if r["prefix"] == prefix and tag == t:
                y["values"][field] = r["value_num"]
                y["sources"][field] = {"accn": r["accn"], "filed": _d(r["filed"])}
                if field == "measure_value":
                    y["measure_unit"] = r["unit"]

    def inside(y, end):
        # A date belongs to the fiscal year it falls in; P&G dates one name a
        # month after its year end.
        return y["_start"] <= end <= y["_end"] + datetime.timedelta(days=45)

    for cik, years in out.items():
        spelled = names.get(cik, {}).get("tagged", {})
        certs = names.get(cik, {}).get("certs", {})

        def full(raw, cleaned):
            # What the documents spell out for a tagged string, else the
            # string without its title.
            return spelled.get(raw) or [_plain(cleaned)]

        prev = None
        for end in sorted(years):
            y = years[end]
            for r in sorted(people.get(cik, []), key=lambda r: (r["period_end"], r["filed"])):
                if not inside(y, r["period_end"]):
                    continue
                key = "total_pay" if r["tag"] == "PeoTotalCompAmt" else "actually_paid"
                c = y["ceos"].setdefault(_individual(r["dims"]), {"member": _individual(r["dims"])})
                c[key] = r["value_num"]
                c.setdefault("sources", {})[key] = {"accn": r["accn"], "filed": _d(r["filed"])}
                c["_to"] = max(c.get("_to", r["period_end"]), r["period_end"])
            dated = [(e, ind, n) for e, ind, raw, cleaned in
                     sorted(tagged_names.get(cik, []), key=lambda x: x[0])
                     if inside(y, e) for n in full(raw, cleaned)]
            window = [(ind, n) for _, ind, n in dated]
            for ind, n in window:
                if n not in y["ceo_names"]:
                    y["ceo_names"].append(n)
            named = {_surname(n) for _, n in window}
            paid = [c for c in y["ceos"].values()
                    if any(c.get(k) for k in ("total_pay", "actually_paid"))]
            ceos, seen = [], set()
            for c in y["ceos"].values():
                # Former CEOs kept in the table with zeros for the year (P&G,
                # Home Depot, Starbucks — which keeps three) did not serve in
                # it: someone with only zeros is dropped when another CEO
                # that year has pay, or when the year's names leave them out.
                zero = c not in paid
                if zero and (paid or (named and not any(
                        n and n in c["member"].lower() for n in named))):
                    continue
                # A combined proxy's other registrant (PG&E's utility,
                # pcg:PEO1PacificGasElectricCoMember) is not this company's CEO.
                if re.search(r"(?i):PEO\d*[A-Za-z]+(?:Co|Company|Inc|Corp|Corporation|LLC|LP)Member$",
                             c["member"]):
                    continue
                # Trane tags "First PEO" and "Second PEO" with identical
                # figures and names one person: one CEO tagged twice.
                key = (c.get("total_pay"), c.get("actually_paid"))
                if key in seen:
                    continue
                seen.add(key)
                ceos.append(c)
            loose = list(dict.fromkeys(n for ind, n in window if ind is None))
            signed = _signers(certs, end)

            def matched(c):
                return [n for ind, n in window if ind == c["member"]] + [
                    n for n in loose if _surname(n) and _surname(n) in c["member"].lower()]
            # Intel's 2026 proxy names "Mr. Tan" and tags his pay to
            # intc:TranMember: one name left over and one CEO left unnamed
            # are the same person.
            spare = [n for n in loose if not any(
                _surname(n) and _surname(n) in c["member"].lower() for c in ceos)]
            unnamed = [c for c in ceos if not matched(c)]
            for c in ceos:
                own = [n for ind, n in window if ind == c["member"]]
                by_surname = [n for n in loose if _surname(n) and _surname(n) in c["member"].lower()]
                # Ceg tags "Mr. Dominguez" for a Peo1Member; Microsoft tags no
                # name at all and the 10-K's signature names its one CEO.
                alone = loose if len(ceos) == 1 and len(loose) == 1 else []
                if not alone and len(spare) == 1 and unnamed == [c]:
                    alone = spare
                sole = _sole_signer(certs, end) if len(ceos) == 1 else None
                label = spelled.get(c["member"]) or [_plain(_member_label(c["member"]))]
                if re.fullmatch(r"(?i)(?:first |second )?peo\s*\d*", label[0]):
                    label = []
                tagged_as = own + by_surname + alone + (spelled.get(c["member"]) or [])
                # Where the tag and the member disagree on a spelling (Global
                # Payments tags "Cameron Beady", Visa's member is "Mclnerney"),
                # the one a 10-K signature bears. A signer stands in only
                # where nothing is tagged: a 10-K is signed after the year
                # ends, sometimes by a successor.
                signed_any = {_surname(n) for rows in certs.values() for n, _ in rows}
                c["name"] = (next((n for n in tagged_as if _surname(n) in signed_any), None) or
                             (tagged_as + ([sole] if sole else []) + label +
                              [_member_label(c["member"])])[0])
                # A name dated to part of the year dates the person's service
                # (Caterpillar: "Mr. Umpleby" to 30 April, his pay to December).
                ends = [e for e, _, n in dated if n == c["name"]]
                if ends:
                    c["_to"] = max(ends)
            _ordinal_placeholders(ceos, certs, end, prev)
            y["ceos"] = sorted(ceos, key=lambda c: c["name"])
            if y["ceos"]:
                # The year's CEOs are the people it pays, named as tagged.
                y["ceo_names"] = [c["name"] for c in y["ceos"]]
            else:
                # One CEO may be named twice ("Ms. Ullal", "Jayshree Ullal"):
                # one name per surname, the fuller spelling.
                by = {}
                for n in y["ceo_names"]:
                    k = _surname(n)
                    if k not in by or len(n) > len(by[k]):
                        by[k] = n
                y["ceo_names"] = [n for n in y["ceo_names"] if by.get(_surname(n)) == n]
                sole = _sole_signer(certs, end)
                if not y["ceo_names"] and sole and y["values"]["ceo_total_pay"] is not None:
                    y["ceo_names"] = [sole]
            for c in y["ceos"]:
                c["name"] = _tidy(_spelling(c["name"], certs))
            y["ceo_names"] = [_tidy(_spelling(n, certs)) for n in y["ceo_names"]]
            _year_end(y, prev, signed)
            y["ceo_count"] = len(y["ceos"]) or len(y["ceo_names"]) or (
                1 if y["values"]["ceo_total_pay"] is not None else 0)
            prev = y
        # One spelling per person across the company's years, the latest
        # year's ("D. James Umpleby III" in an old proxy is Jim Umpleby).
        latest = {}
        for end in sorted(years, reverse=True):
            y = years[end]
            for n in [c["name"] for c in y["ceos"]] + y["ceo_names"]:
                latest.setdefault(_surname(n), n)
        for y in years.values():
            for c in y["ceos"]:
                c["name"] = latest.get(_surname(c["name"]), c["name"])
                c.pop("_to", None)
            for k in ("ceo_names", "year_end_ceos"):
                y[k] = list(dict.fromkeys(latest.get(_surname(n), n) for n in y.get(k, [])))
            del y["_start"], y["_end"]
    return out


PLACEHOLDER = re.compile(r"(?i)^(first|second|third)?\s*peo\s*(\d*)$")
ORDINAL = {"first": 1, "second": 2, "third": 3}


def _ordinal_placeholders(ceos, certs, end, prev):
    """American Tower 2024: "First PEO" and "Second PEO", no names anywhere.
    Numbered in order of service, so the last is the successor who signs
    this year's 10-K and the one before signed last year's. Named only when
    both signatures are there and differ; otherwise left as tagged."""
    marks = [PLACEHOLDER.match(c["name"]) for c in ceos]
    if len(ceos) != 2 or not all(marks) or prev is None:
        return
    order = [ORDINAL.get((m.group(1) or "").lower()) or int(m.group(2) or 0) for m in marks]
    now = _sole_signer(certs, end)
    # The successor can sign the year before too (Vondran signed American
    # Tower's 2023 10-K in February 2024): the last signer before them.
    before = next((b for b in (_sole_signer(certs, e) for e in sorted(certs, reverse=True) if e < end)
                   if b and now and _surname(b) != _surname(now)), None)
    if not now or not before or order[0] == order[1]:
        return
    first, second = (ceos[0], ceos[1]) if order[0] < order[1] else (ceos[1], ceos[0])
    first["name"], second["name"] = before, now


def _year_end(y, prev, signed):
    """Mark each of the year's CEOs `at_year_end`: in office when it closed.

    One CEO is. Of several: those whose own figures run to the year end when
    another's stop short (Caterpillar's Umpleby to April); else those who
    certify the 10-K for the year as principal executive (Netflix's two
    co-CEOs, or the successor). Failing both, every one is — never a guess:
    being new that year is no evidence (Netflix added Peters as co-CEO in
    2023 beside Sarandos)."""
    ceos = y["ceos"]
    pick = ceos
    if len(ceos) > 1:
        late = y["_end"] - datetime.timedelta(days=20)
        reach = [c for c in ceos if c.get("_to") and c["_to"] >= late]
        surnames = {_surname(n) for n in signed}
        cert = [c for c in ceos if _surname(c["name"]) in surnames]
        for choice in (reach, cert):
            if 0 < len(choice) < len(ceos) or (choice is cert and choice):
                pick = choice
                break
    for c in ceos:
        c["at_year_end"] = c in pick
    if ceos:
        y["year_end_ceos"] = [c["name"] for c in pick]
        return
    # Names without figures by person (Kinder Morgan: "Mr. Kean and Ms. Dang"):
    # the 10-K signer, where that settles it.
    signers = {_surname(n) for n in signed}
    hit = [n for n in y["ceo_names"] if _surname(n) in signers]
    y["year_end_ceos"] = hit if len(y["ceo_names"]) > 1 and hit else list(y["ceo_names"])


def _returns(cur, ciks, ceiling):
    """{cik: {"accn", "filed", "base", "by_end": {period_end: {tsr, peer_tsr}}}} from each
    company's latest proxy (filed by `ceiling`) that tags a shareholder return."""
    ph = ",".join("?" * len(ciks))
    rows = cur.execute("""
        WITH ref AS (
            SELECT f.cik, arg_max(x.accn, x.filed) AS accn, max(x.filed) AS filed
            FROM sec_px_facts f JOIN sec_px_filings x ON x.accn = f.accn AND x.status = 'ok'
            WHERE f.cik IN (%s) AND f.prefix = 'ecd' AND f.tag = 'TotalShareholderRtnAmt'
              AND f.dims = '' AND (CAST(? AS DATE) IS NULL OR x.filed <= ?)
            GROUP BY f.cik)
        SELECT f.cik, ref.accn, ref.filed, f.tag, f.period_start, f.period_end,
               arg_max(f.value_num, CASE WHEN f.decimals = 'INF' THEN 99
                                         ELSE TRY_CAST(f.decimals AS INTEGER) END) AS value
        FROM sec_px_facts f JOIN ref ON ref.accn = f.accn
        WHERE f.prefix = 'ecd' AND f.dims = '' AND f.tag IN (%s)
        GROUP BY ALL""" % (ph, ",".join("?" * len(TSR_FIELDS))),
        list(ciks) + [ceiling, ceiling] + [t for _, t in TSR_FIELDS]).fetchall()
    field = {t: f for f, t in TSR_FIELDS}
    out = {}
    for cik, accn, filed, tag, start, end, value in rows:
        r = out.setdefault(cik, {"accn": accn, "filed": filed, "base": None, "by_end": {}})
        r["by_end"].setdefault(end, {})[field[tag]] = value
        # The base is the start of the table's first year: a cumulative
        # context carries it; one-year contexts give it as the earliest start.
        if start is not None and (r["base"] is None or start < r["base"]):
            r["base"] = start
    return out


def _attach_returns(years, ret, cik):
    """Put one proxy's return series on the years; other years stay empty."""
    r = ret.get(cik)
    for end, y in years.items():
        for f, _ in TSR_FIELDS:
            y["values"][f] = None
        hit = r and r["by_end"].get(end)
        if hit:
            for f, _ in TSR_FIELDS:
                if hit.get(f) is not None:
                    y["values"][f] = hit[f]
                    y["sources"][f] = {"accn": r["accn"], "filed": _d(r["filed"])}
        y["tsr_base"] = _d(r["base"]) if r and hit else None


def _source(cik, accn):
    return "https://www.sec.gov/Archives/edgar/data/%d/%s/" % (cik, accn.replace("-", ""))


def _shown_name(cur, cik, filed):
    """The one name every US page shows for a company (us_link.company_name)."""
    return us_link.company_name(cik, filed, cur)


def _names(cur, ciks):
    """The registrant's own name from its latest proxy (dei:EntityRegistrantName)."""
    ph = ",".join("?" * len(ciks))
    return {r[0]: r[1] for r in cur.execute("""
        SELECT f.cik, arg_max(f.value_text, x.filed)
        FROM sec_px_facts f JOIN sec_px_filings x ON x.accn = f.accn
        WHERE f.cik IN (%s) AND f.prefix = 'dei' AND f.tag = 'EntityRegistrantName'
        GROUP BY f.cik""" % ph, list(ciks)).fetchall()}


# ---- 1. the index ------------------------------------------------------------------
@router.get("", openapi_extra={"x-example": "/api/v1/us/pay"})
def index(basis: str = Query("latest", description="latest | first"),
          as_of: str = Query("", description="YYYY-MM-DD: only proxies filed by then")):
    """Every S&P 500 member's latest fiscal year of pay versus performance."""
    cur = _require()
    basis = _check("basis", basis, ("latest", "first"))
    ceiling = _date(as_of)
    snap = _snapshot(cur)
    members = sec_api._rows(cur, """
        SELECT cik, ticker, tickers, name AS fund_name, weight_pct FROM us_index_members
        WHERE snapshot_id = ? ORDER BY weight_pct DESC""", [snap["snapshot_id"]])
    ciks = [m["cik"] for m in members]
    years = _years(_picked(cur, ciks, basis, ceiling), _people(cur, ciks))
    ret = _returns(cur, ciks, ceiling)
    for cik, cy in years.items():
        _attach_returns(cy, ret, cik)
    names = _names(cur, ciks)
    state = {r[0]: r[1:] for r in cur.execute("""
        SELECT cik, arg_max(status, checked_at), max(checked_at) FROM sec_px_checks
        WHERE cik IN (%s) GROUP BY cik""" % ",".join("?" * len(ciks)), ciks).fetchall()}
    proxies = {r[0]: r[1] for r in cur.execute("""
        SELECT cik, count(*) FILTER (WHERE status = 'ok') FROM sec_px_filings
        WHERE cik IN (%s) GROUP BY cik""" % ",".join("?" * len(ciks)), ciks).fetchall()}
    rows, counts = [], {"with_pay": 0, "no_pay_table": 0, "not_checked": 0, "failed": 0}
    for m in members:
        cy = years.get(m["cik"], {})
        # The latest year that carries the CEO's pay or return: a proxy's
        # newest column, never a year the table leaves blank.
        fy = max((e for e, y in cy.items()
                  if y["ceos"] or any(v is not None for v in y["values"].values())), default=None)
        y = cy.get(fy)
        check = state.get(m["cik"])
        if y is not None:
            status = "ok"
        elif check is None:
            status = "not_checked"
        elif check[0] == "failed":
            status = "failed"
        else:
            status = "no_pay_table"
        counts["with_pay" if status == "ok" else status] += 1
        # The list shows who was CEO at the year end, with that person's own
        # figures; anyone who left during the year is on the company's page.
        at_end = [c for c in (y["ceos"] if y else []) if c.get("at_year_end")]
        values = dict(y["values"]) if y else None
        if y and len(at_end) == 1 and (len(y["ceos"]) > 1 or values["ceo_total_pay"] is None):
            # Merck tags its only CEO by name rather than as "the CEO": the
            # same filed figures, read from that one person's facts.
            values["ceo_total_pay"] = at_end[0].get("total_pay")
            values["ceo_actually_paid"] = at_end[0].get("actually_paid")
        names_now = (y["year_end_ceos"] if y else [])
        multi = len(at_end) > 1 or (not at_end and len(names_now) > 1)
        src = y and (y["sources"].get("ceo_total_pay") or y["sources"].get("tsr") or
                     next(iter(y["sources"].values()), None) or
                     (y["ceos"] and next(iter(y["ceos"][0].get("sources", {}).values()), None)))
        rows.append({
            "cik": m["cik"], "ticker": m["ticker"], "tickers": m["tickers"].split(","),
            "name": _shown_name(cur, m["cik"], names.get(m["cik"]) or m["fund_name"]),
            "sec_name": names.get(m["cik"]) or m["fund_name"], "logo": us_link.logo(m["cik"]),
            "weight_pct": m["weight_pct"], "status": status,
            "fiscal_year_end": y["fiscal_year_end"] if y else None,
            "ceo_names": names_now,
            "ceo_count": len(names_now),
            # Co-CEOs at the year end (Netflix): each with their own figures,
            # never added together.
            "ceos": [{"name": c["name"], "total_pay": c.get("total_pay"),
                      "actually_paid": c.get("actually_paid")} for c in at_end] if multi else [],
            "left_in_year": [c["name"] for c in (y["ceos"] if y else []) if not c.get("at_year_end")],
            "values": ({k: (None if multi and k.startswith("ceo_") else v)
                        for k, v in values.items()} if y else None),
            "measure_name": y["measure_name"] if y else None,
            "tsr_base": y.get("tsr_base") if y else None,
            "source": dict(src, url=_source(m["cik"], src["accn"])) if src else None,
            "proxies_read": proxies.get(m["cik"], 0),
            "last_checked": _d(check[1]) if check else None,
        })
    return {"index": snap, "companies": rows, "count": len(rows), "coverage": counts,
            "basis": basis, "as_of": _d(ceiling), "calc": CALC, "provenance": PROVENANCE}


# ---- 2. one company --------------------------------------------------------------
@router.get("/{company}", openapi_extra={"x-example": "/api/v1/us/pay/JPM"})
def company(company: str,
            basis: str = Query("latest", description="latest | first"),
            as_of: str = Query("", description="YYYY-MM-DD: only proxies filed by then")):
    """Every fiscal year of one member's pay versus performance, and every proxy read."""
    cur = _require()
    basis = _check("basis", basis, ("latest", "first"))
    ceiling = _date(as_of)
    snap = _snapshot(cur)
    key = (company or "").strip().upper().replace(".", "-")
    hit = sec_api._rows(cur, """
        SELECT cik, ticker, tickers, name AS fund_name, weight_pct FROM us_index_members
        WHERE snapshot_id = ? AND (upper(ticker) = ? OR list_contains(string_split(upper(tickers), ','), ?)
                                   OR CAST(cik AS VARCHAR) = ltrim(regexp_replace(?, '(?i)^cik:', ''), '0'))""",
                          [snap["snapshot_id"], key, key, key])
    if not hit:
        raise HTTPException(404, "%r is not in the S&P 500 member list of %s; GET /api/v1/us/pay "
                                 "lists it" % (company, snap["as_of"]))
    m = hit[0]
    cy = _years(_picked(cur, [m["cik"]], basis, ceiling), _people(cur, [m["cik"]])).get(m["cik"], {})
    _attach_returns(cy, _returns(cur, [m["cik"]], ceiling), m["cik"])
    years = []
    for end in sorted(cy, reverse=True):
        y = cy[end]
        if not any(v is not None for v in y["values"].values()) and not y["ceos"]:
            continue
        y["sources"] = {k: dict(v, url=_source(m["cik"], v["accn"])) for k, v in y["sources"].items()}
        years.append(y)
    filings = sec_api._rows(cur, """
        SELECT accn, form, filed, status, facts, detail, instance_url, sha256
        FROM sec_px_filings WHERE cik = ? ORDER BY filed DESC, fetched_at DESC""", [m["cik"]])
    for f in filings:
        f["filed"] = _d(f["filed"])
        f["url"] = _source(m["cik"], f["accn"])
    name = _names(cur, [m["cik"]]).get(m["cik"]) or m["fund_name"]
    return {"company": {"cik": m["cik"], "ticker": m["ticker"], "tickers": m["tickers"].split(","),
                        "name": _shown_name(cur, m["cik"], name), "sec_name": name, "logo": us_link.logo(m["cik"]),
                        "fund_name": m["fund_name"], "weight_pct": m["weight_pct"]},
            "index": snap, "years": years, "filings": filings, "basis": basis,
            "as_of": _d(ceiling), "calc": CALC, "provenance": PROVENANCE}


# ---- health ----------------------------------------------------------------------
def health():
    """One entry in the shape /catalog/health lists the equity extractors in."""
    try:
        cur = _require()
    except HTTPException:
        return []
    last, members, checked_recent, latest_filed, first_filed = cur.execute("""
        SELECT (SELECT max(checked_at) FROM sec_px_checks),
               (SELECT count(*) FROM us_index_members
                 WHERE snapshot_id = (SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)')),
               (SELECT count(DISTINCT cik) FROM sec_px_checks
                 WHERE checked_at >= now()::TIMESTAMP - INTERVAL 3 DAY),
               (SELECT max(filed) FROM sec_px_filings WHERE status = 'ok'),
               (SELECT min(filed) FROM sec_px_filings WHERE status = 'ok')""").fetchone()
    failed = [r[0] for r in cur.execute("""
        SELECT m.ticker FROM us_index_members m
        JOIN (SELECT cik, arg_max(status, checked_at) AS s FROM sec_px_checks GROUP BY cik) c
          USING (cik)
        WHERE m.snapshot_id = (SELECT max(snapshot_id) FROM us_index_snapshots WHERE index_name = 'S&P 500 (SPY holdings)')
          AND c.s = 'failed' ORDER BY m.ticker""").fetchall()]
    days = (datetime.datetime.utcnow() - last).days if last else None
    stale = days is None or days > STALE_AFTER_DAYS
    flags = []
    if failed:
        flags.append("latest check failed: %s" % ", ".join(failed[:20]))
    if members and checked_recent < members:
        flags.append("%d of %d members not checked in the last 3 days" % (
            members - checked_recent, members))
    return [{
        "dataset": "sec-proxy-pay",
        "status": "attention" if (stale or failed) else "ok",
        "archive_read_through": _d(latest_filed),
        "days_behind": days,
        "stale_after_days": STALE_AFTER_DAYS,
        "stale": stale,
        "last_extracted_at": last.isoformat() + "Z" if last else None,
        "archive_read_back_to": _d(first_filed),
        "shape_flags": flags,
    }]
