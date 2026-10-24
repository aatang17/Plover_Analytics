# -*- coding: utf-8 -*-
u"""Equity product API — AGM voting results (臨時報告書, 株主総会決議).

What shareholders actually did, proposal by proposal and — for board elections
— director by director. Served from the same DuckDB file and reader as the
rest of the equity product.

WHAT A CONSUMER MUST NOT ASSUME, carried in the data rather than in prose:

  1. THE PERCENTAGE IS THE COMPANY'S, NOT OURS, AND CANNOT BE REBUILT FROM THE
     COUNTS. Almost every filer states that it stopped tallying attending
     votes once the outcome was settled (出席した株主の議決権の数の一部を加算
     しなかった理由), so the denominator behind 賛成割合 is never published.
     `approval_pct` is exactly as filed and carries the official badge.
     `approval_pct_of_counted` is OUR arithmetic over the disclosed counts and
     carries its formula instead; the two differ by a median of 0.14pp and
     occasionally much more. Never present the derived one as the company's.
  2. A BOARD ELECTION HAS NO PROPOSAL-LEVEL VOTE. The filing publishes one
     result per candidate and no total, so proposal-level counts are null for
     those and `candidates` says how many rows sit underneath. Summing the
     candidates would invent a figure nobody filed.
  3. VOTING RIGHTS ARE NOT SHARES. Counts are in 個 — voting-right units, one
     per trading unit — so they do not equal share counts and must not be
     compared with the shares in the 5% filings or the register.
  4. ABSENCE IS NOT DISSENT. A shareholder who did not vote appears nowhere.
     A low approval percentage means votes actively cast against, which is why
     it is a much sharper signal in Japan than a low turnout would be.
  5. A DISMISSAL VOTE READS BACKWARDS, AND IS NEVER RANKED WITH ELECTIONS.
     取締役の解任の件 asks shareholders to REMOVE a named director, so 0.5%
     approval means the attempt was crushed and the director kept the seat —
     the opposite of what the same number means under an election. Ranking the
     two together put retained directors at the top of "lowest support", which
     is a wrong number, not a presentational quibble. /directors serves
     elections by default; `kind=dismissal` serves removal votes, where a HIGH
     percentage is the adverse signal.
  6. A RANKING SHOWS ONLY ROWS WE CAN CORROBORATE. Every row is stored as
     filed, but /directors and /proposals rank only those whose filed
     percentage our own arithmetic over the disclosed counts can reproduce
     within 15pp (`pct_consistent`). Without that a single misread cell — an
     against-rate column read as an approval rate — would put a fictitious
     0.1% at the top of "lowest support" and above every genuine result.
     `include_unverified=true` returns them anyway, flagged.
  7. THE ARCHIVE STARTS IN APRIL 2024. 臨時報告書 leave EDINET's public
     inspection window far sooner than annual reports do; nothing earlier
     survived to be captured, and no back-fill can recover it.
"""
from fastapi import APIRouter, HTTPException, Query

from . import asof
from . import person_en

from .agm_labels import label_en
from .equity_api import NAME_CTES, NAMES_NOTE, PROVENANCE, _cur, _rows

router = APIRouter(prefix="/api/v1/equity/agm", tags=["AGM votes"])

CLEAN = "status IN ('clean','partial')"

CALC = {
    "approval_pct": ("as filed by the company (賛成割合). NOT recomputed — the "
                     "denominator it uses is not disclosed"),
    "approval_pct_of_counted": ("100 × 賛成 / (賛成 + 反対 + 棄権), computed by "
                                "this platform from the disclosed counts. It "
                                "differs from approval_pct because the filer "
                                "did not tally every attending vote"),
    "against_pct_of_counted": "100 × 反対 / (賛成 + 反対 + 棄権), computed by this platform",
    "dissent_pct": ("100 − approval_pct, as filed: the share of counted votes "
                    "not supporting the resolution"),
    "pct_consistent": ("whether approval_pct and approval_pct_of_counted agree "
                       "within 15 percentage points. False means this platform "
                       "could not corroborate its own reading of the filing's "
                       "table, so the row is excluded from rankings by default"),
}

KIND_NOTE = {
    "election": ("Election results only. A vote to REMOVE a director "
                 "(取締役の解任の件) is excluded here because its percentage "
                 "inverts — low approval there means the director was "
                 "retained. Those are served with kind=dismissal."),
    "dismissal": ("Votes to REMOVE a named director. Read these the other way "
                  "up: a HIGH approval percentage is the adverse signal, and a "
                  "low one means the attempt failed and the director stayed."),
    "all": ("Elections and removal votes together. Their percentages mean "
            "opposite things — check `category` on every row before comparing."),
}

VERIFY_NOTE = (
    "Rankings show only rows whose filed percentage this platform can "
    "reproduce from the counts printed beside it, within 15 percentage points. "
    "Rows that fail that check are kept in the dataset and returned by "
    "include_unverified=true, flagged — they are not deleted, because the "
    "filing says what it says.")

TALLY_NOTE = (
    "Japanese issuers routinely count advance votes plus enough of the votes "
    "in the room to settle the outcome, then stop — and disclose that they did "
    "(出席した株主の議決権の数の一部を加算しなかった理由). partial_tally flags "
    "each such filing. It means the published percentage rests on a base the "
    "filing does not state, so it can be quoted but not reconstructed.")

ELECTION_NOTE = (
    "A board election publishes one result per candidate and no figure for the "
    "proposal as a whole, so proposal-level vote counts are null for those "
    "rows and the per-director results are served from /directors.")

UNIT_NOTE = (
    "Counts are voting rights (個), one per trading unit — not shares. They are "
    "not comparable with the share counts in the 5% filings or the register.")

COVERAGE_NOTE = (
    "Built from every archived 臨時報告書 (EDINET type 180/190). That document "
    "type also carries mergers, subsidiary changes and officer changes; only "
    "the ones reporting a general meeting produce rows here, and the rest are "
    "recorded as examined-and-not-a-meeting so the count reconciles with the "
    "archive. Coverage begins in April 2024 — earlier filings had already left "
    "EDINET's public inspection window before capture began.")

LABEL_NOTE = (
    "A resolution's title is filed in Japanese only. `label_en` re-renders it "
    "from a fixed table of the statutory wording — it is a lookup, not a "
    "translation engine — and is null whenever any part of the title is "
    "unrecognised, which is why the filed Japanese is always returned beside "
    "it and is what every export and citation carries. About one title in "
    "eight is a non-standard wording and stays Japanese.")

DIRECTOR_NAME_NOTE = (
    "A candidate's name is filed in Japanese only. `candidate_name_en` is the "
    "romanisation the SAME COMPANY publishes for that person in its own annual "
    "report (the XBRL member label behind the board table), matched on the "
    "Japanese name within that issuer. It is null for a candidate who has "
    "never appeared on a filed board — a first-time nominee, or anyone put up "
    "by a shareholder — and never guessed.")

# A candidate's English name, from the issuer's OWN annual report: EDINET tags
# each board member with a romanised context label, which board_extract stores
# as name_en. Matching is on the Japanese name inside one issuer, with spaces
# removed because the AGM filing writes 佐々木康行 where the annual report
# writes 佐々木 康行. Across issuers it would not be evidence of the same
# person, and it is not used that way.
BOARD_EN_CTE = u""",
    board_en AS (
        SELECT f.sec_code,
               replace(replace(b.name_ja, ' ', ''), '\u3000', '') AS name_key,
               max_by(b.name_en, f.filed_date) AS name_en   -- a correction's spelling wins
        FROM eq_board b JOIN eq_filings f USING (doc_id)
        WHERE b.name_ja IS NOT NULL AND b.name_en IS NOT NULL
              AND f.sec_code IS NOT NULL
        GROUP BY 1, 2)
"""

# The same key on both sides of the join: no space of either width.
NAME_KEY = "replace(replace(%s, ' ', ''), '\u3000', '')"


COUNTED = "(for_votes + coalesce(against_votes,0) + coalesce(abstain_votes,0))"
PCT_OF_COUNTED = ("CASE WHEN %s > 0 THEN round(100.0 * for_votes / %s, 2) END"
                  % (COUNTED, COUNTED))


def _notes(head):
    head["verification_note"] = VERIFY_NOTE
    head["tally_note"] = TALLY_NOTE
    head["election_note"] = ELECTION_NOTE
    head["unit_note"] = UNIT_NOTE
    head["names_note"] = NAMES_NOTE
    head["label_note"] = LABEL_NOTE
    head["director_name_note"] = DIRECTOR_NAME_NOTE
    head["calc"] = CALC
    head["provenance"] = PROVENANCE
    return head


def _require():
    cur = _cur()
    names = {r[0] for r in cur.execute(
        "SELECT table_name FROM duckdb_tables()").fetchall()}
    if "eq_agm_meetings" not in names:
        raise HTTPException(503, "AGM voting results not published yet")
    return cur


@router.get("/summary")
def summary():
    u"""Coverage first, then what the votes show."""
    cur = _require()
    head = _rows(cur, """
        SELECT count(*) AS meetings,
               count(DISTINCT issuer_sec_code) AS companies,
               min(meeting_date) AS earliest_meeting,
               max(meeting_date) AS latest_meeting,
               sum(CASE WHEN partial_tally THEN 1 ELSE 0 END) AS partial_tally_filings
        FROM eq_agm_meetings WHERE %s""" % CLEAN)[0]
    head["proposals"] = _rows(cur,
        "SELECT count(*) AS n FROM eq_agm_proposals")[0]["n"]
    head["director_results"] = _rows(cur,
        "SELECT count(*) AS n FROM eq_agm_votes")[0]["n"]
    head["by_category"] = _rows(cur, """
        SELECT category, count(*) AS n,
               round(median(approval_pct_filed), 2) AS median_approval_pct
        FROM eq_agm_proposals GROUP BY 1 ORDER BY 2 DESC""")
    head["director_approval"] = _rows(cur, """
        SELECT count(*) AS results,
               round(median(approval_pct_filed), 2) AS median_pct,
               sum(CASE WHEN approval_pct_filed < 80 THEN 1 ELSE 0 END) AS below_80,
               sum(CASE WHEN approval_pct_filed < 70 THEN 1 ELSE 0 END) AS below_70,
               sum(CASE WHEN approval_pct_filed < 50 THEN 1 ELSE 0 END) AS below_50
        FROM eq_agm_votes WHERE approval_pct_filed IS NOT NULL
                                  AND pct_consistent""")[0]
    # The shape of the dataset in one object: almost every director clears 90%,
    # and the whole interest is in the tail. Bucketed in SQL so the page never
    # has to pull every row to draw it.
    head["approval_distribution"] = _rows(cur, """
        SELECT CASE
                 WHEN approval_pct_filed < 50 THEN 'under 50'
                 WHEN approval_pct_filed < 60 THEN '50-60'
                 WHEN approval_pct_filed < 70 THEN '60-70'
                 WHEN approval_pct_filed < 80 THEN '70-80'
                 WHEN approval_pct_filed < 90 THEN '80-90'
                 WHEN approval_pct_filed < 95 THEN '90-95'
                 ELSE '95-100' END AS bucket,
               min(approval_pct_filed) AS lo,
               count(*) AS n
        FROM eq_agm_votes WHERE approval_pct_filed IS NOT NULL
                                  AND pct_consistent
        GROUP BY 1 ORDER BY lo""")
    head["shareholder_proposals"] = _rows(cur, """
        SELECT count(*) AS n,
               sum(CASE WHEN result = '否決' THEN 1 ELSE 0 END) AS rejected
        FROM eq_agm_proposals WHERE shareholder_proposal""")[0]
    status = _rows(cur, "SELECT status, count(*) AS n FROM eq_agm_meetings GROUP BY 1")
    head["extraction_status"] = {r["status"]: r["n"] for r in status}
    head["coverage_note"] = COVERAGE_NOTE
    return _notes(head)


@router.get("/directors")
def directors(limit: int = Query(50, ge=1, le=500),
              order: str = Query("lowest", description="'lowest' or 'highest' approval"),
              max_pct: float = Query(100.0, ge=0, le=100),
              sec_code: str = Query("", description="restrict to one issuer"),
              year: int = Query(0, description="meeting calendar year"),
              kind: str = Query("election", description="'election', 'dismissal' or 'all'"),
              include_unverified: str = Query("", description="'true' to include "
                                              "rows this platform could not corroborate")):
    u"""Named directors by the support they actually received.

    The lowest end is the point of the dataset: a director returned on 62% has
    a mandate problem that no other public dataset in Japan will tell you
    about, and the same name can be looked up across companies and years.
    """
    cur = _require()
    where = ["v.approval_pct_filed IS NOT NULL", "v.approval_pct_filed <= ?"]
    params = [max_pct]
    if include_unverified.lower() not in ("1", "true", "yes"):
        where.append("v.pct_consistent")
    kind = kind if kind in KIND_NOTE else "election"
    if kind == "election":
        where.append("(p.category IS NULL OR p.category <> 'dismissal')")
    elif kind == "dismissal":
        where.append("p.category = 'dismissal'")
    if sec_code:
        where.append("m.issuer_sec_code = ?")
        params.append(sec_code[:4])
    if year:
        where.append("year(m.meeting_date) = ?")
        params.append(year)
    params.append(limit)
    direction = "ASC" if order == "lowest" else "DESC"
    rows = _rows(cur, """WITH x AS (SELECT 1)""" + NAME_CTES + BOARD_EN_CTE + """
        SELECT m.issuer_sec_code AS sec_code, m.issuer_name,
               coalesce(en_e.name_en, en_s.name_en) AS issuer_name_en,
               m.meeting_date,
               p.label AS proposal, p.category, p.shareholder_proposal,
               v.candidate_name, be.name_en AS candidate_name_en,
               v.for_votes, v.against_votes, v.abstain_votes,
               v.approval_pct_filed AS approval_pct,
               %s AS approval_pct_of_counted,
               v.result, v.pct_consistent, m.partial_tally, m.doc_id
        FROM eq_agm_votes v
        JOIN eq_agm_meetings m USING (doc_id)
        LEFT JOIN eq_agm_proposals p
               ON p.doc_id = v.doc_id AND p.seq = v.proposal_seq
        LEFT JOIN en_ecode en_e ON en_e.edinet_code = m.issuer_edinet_code
        LEFT JOIN en_scode en_s ON en_s.sec_code = m.issuer_sec_code
        LEFT JOIN board_en be ON be.sec_code = m.issuer_sec_code
               AND be.name_key = %s
        WHERE %s AND %s
        ORDER BY v.approval_pct_filed %s, v.for_votes DESC
        LIMIT ?""" % (PCT_OF_COUNTED.replace("for_votes", "v.for_votes")
                                    .replace("against_votes", "v.against_votes")
                                    .replace("abstain_votes", "v.abstain_votes"),
                      NAME_KEY % "v.candidate_name",
                      " AND ".join(where), CLEAN.replace("status", "m.status"),
                      direction), params)
    for r in rows:
        r["proposal_en"] = label_en(r.get("proposal"))
    person_en.fix_rows(rows, "candidate_name_en")
    return _notes({"order": order, "kind": kind, "kind_note": KIND_NOTE[kind],
                   "rows": rows,
                   "cite": "/agm.html?order=%s&kind=%s&limit=%d" % (order, kind, limit)})


@router.get("/proposals")
def proposals(category: str = Query("", description="e.g. takeover_defence"),
              shareholder: str = Query("", description="'true' for shareholder proposals"),
              limit: int = Query(50, ge=1, le=500),
              include_unverified: str = Query("", description="'true' to include "
                                              "rows this platform could not corroborate")):
    u"""Proposals, filterable to the ones worth arguing about."""
    cur = _require()
    where = ["1=1"]
    params = []
    if category:
        where.append("p.category = ?")
        params.append(category)
    if shareholder.lower() in ("1", "true", "yes"):
        where.append("p.shareholder_proposal")
    if include_unverified.lower() not in ("1", "true", "yes"):
        where.append("(p.pct_consistent OR p.approval_pct_filed IS NULL)")
    params.append(limit)
    rows = _rows(cur, """WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT m.issuer_sec_code AS sec_code, m.issuer_name,
               coalesce(en_e.name_en, en_s.name_en) AS issuer_name_en,
               m.meeting_date,
               p.proposal_no, p.label, p.category, p.shareholder_proposal,
               p.for_votes, p.against_votes, p.abstain_votes,
               p.approval_pct_filed AS approval_pct,
               %s AS approval_pct_of_counted,
               p.result, p.candidates, p.pct_consistent, m.partial_tally, m.doc_id
        FROM eq_agm_proposals p
        JOIN eq_agm_meetings m USING (doc_id)
        LEFT JOIN en_ecode en_e ON en_e.edinet_code = m.issuer_edinet_code
        LEFT JOIN en_scode en_s ON en_s.sec_code = m.issuer_sec_code
        WHERE %s AND %s
        ORDER BY m.meeting_date DESC, p.seq
        LIMIT ?""" % (PCT_OF_COUNTED.replace("for_votes", "p.for_votes")
                                    .replace("against_votes", "p.against_votes")
                                    .replace("abstain_votes", "p.abstain_votes"),
                      " AND ".join(where), CLEAN.replace("status", "m.status")),
        params)
    for r in rows:
        r["label_en"] = label_en(r.get("label"))
    return _notes({"category": category or None, "rows": rows,
                   "cite": "/agm.html?category=%s" % category})


@router.get("/companies")
def companies(q: str = Query("", description="name in English or Japanese, or securities code")):
    u"""Issuers with a meeting on file, searchable by either language.

    Scoped to this dataset on purpose. A company can file annual reports for
    years and still have no 臨時報告書 in the window, and pointing a reader at a
    company page with no meetings would read as missing data rather than as
    the coverage gap it is.
    """
    cur = _require()
    like = "%" + q.strip() + "%"
    rows = _rows(cur, """WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT m.issuer_sec_code AS sec_code,
               any_value(m.issuer_name) AS name,
               any_value(coalesce(en_e.name_en, en_s.name_en)) AS name_en,
               count(*) AS meetings,
               max(m.meeting_date) AS latest_meeting,
               sum(m.candidates) AS director_results
        FROM eq_agm_meetings m
        LEFT JOIN en_ecode en_e ON en_e.edinet_code = m.issuer_edinet_code
        LEFT JOIN en_scode en_s ON en_s.sec_code = m.issuer_sec_code
        WHERE %s AND m.issuer_sec_code IS NOT NULL
          AND (m.issuer_sec_code LIKE ? OR m.issuer_name LIKE ?
               OR lower(coalesce(en_e.name_en, en_s.name_en, '')) LIKE lower(?))
        GROUP BY 1 ORDER BY 5 DESC NULLS LAST LIMIT 25"""
        % CLEAN.replace("status", "m.status"), [like, like, like])
    return {"companies": rows, "names_note": NAMES_NOTE}


@router.get("/company/{sec_code}")
def company(sec_code: str):
    u"""Every meeting we hold for one issuer, with its proposals and directors."""
    cur = _require()
    code = sec_code[:4]
    meetings = _rows(cur, """WITH x AS (SELECT 1)""" + NAME_CTES + """
        SELECT m.doc_id, m.filed_date, m.meeting_date, m.meeting_type,
               m.issuer_name, coalesce(en_e.name_en, en_s.name_en) AS issuer_name_en,
               m.issuer_sec_code, m.proposals, m.candidates, m.partial_tally,
               m.partial_tally_reason, m.status
        FROM eq_agm_meetings m
        LEFT JOIN en_ecode en_e ON en_e.edinet_code = m.issuer_edinet_code
        LEFT JOIN en_scode en_s ON en_s.sec_code = m.issuer_sec_code
        WHERE m.issuer_sec_code = ? AND %s""" % CLEAN.replace("status", "m.status")
        + asof.clause("m.filed_date") + """
        ORDER BY m.meeting_date DESC""", [code])
    if not meetings:
        raise HTTPException(404, "no AGM voting results for %s" % code)
    ids = [m["doc_id"] for m in meetings]
    ph = ",".join(["?"] * len(ids))
    props = _rows(cur, """
        SELECT doc_id, seq, proposal_no, label, category, shareholder_proposal,
               for_votes, against_votes, abstain_votes,
               approval_pct_filed AS approval_pct, result, candidates
        FROM eq_agm_proposals WHERE doc_id IN (%s) ORDER BY doc_id, seq""" % ph, ids)
    votes = _rows(cur, """WITH x AS (SELECT 1)""" + BOARD_EN_CTE + """
        SELECT v.doc_id, v.proposal_seq, v.candidate_name,
               be.name_en AS candidate_name_en,
               v.for_votes, v.against_votes, v.abstain_votes,
               v.approval_pct_filed AS approval_pct, v.result
        FROM eq_agm_votes v
        LEFT JOIN board_en be ON be.sec_code = ? AND be.name_key = %s
        WHERE v.doc_id IN (%s) ORDER BY v.doc_id, v.seq"""
        % (NAME_KEY % "v.candidate_name", ph), [code] + ids)
    person_en.fix_rows(votes, "candidate_name_en")
    by_doc = {}
    for p in props:
        p["label_en"] = label_en(p.get("label"))
        by_doc.setdefault(p["doc_id"], []).append(dict(p, directors=[]))
    for v in votes:
        for p in by_doc.get(v["doc_id"], []):
            if p["seq"] == v["proposal_seq"]:
                p["directors"].append(v)
                break
    for m in meetings:
        m["proposal_rows"] = by_doc.get(m["doc_id"], [])
    return _notes({"sec_code": code, "name": meetings[0]["issuer_name"],
                   "name_en": meetings[0]["issuer_name_en"],
                   "meetings": meetings,
                   "cite": "/agm.html?company=%s" % code})


# The dataset's card (app/registry.py). The filed percentage is the company's
# and cannot be rebuilt from the counts; the platform's own arithmetic over
# the disclosed counts is returned beside it, never instead.
from .equity_api import EDINET_SOURCE as _EDINET_SOURCE  # noqa: E402

MANIFEST = {
    "id": "agm-votes",
    "section": "governance",
    "name": {"en": "AGM voting results", "ja": "株主総会決議（臨時報告書）"},
    "shape": "events",
    "summary": ("What shareholders actually did at general meetings — every "
                "proposal's result and, for board elections, every director's "
                "own approval percentage — from the extraordinary reports that "
                "disclose the vote."),
    "source": dict(_EDINET_SOURCE,
                   document="臨時報告書 · 株主総会における議決権行使の結果 (extraordinary "
                            "report, EDINET types 180/190)",
                   credit="Source: extraordinary reports filed on EDINET "
                          "(Financial Services Agency of Japan)."),
    "keys": ["doc_id", "sec_code", "meeting_date"],
    "frequency": "per-event",
    "vintage": {
        "unit": "filing", "as_of_basis": "filed_date", "as_of_supported": True,
        "history_from": "2024-04 (filings; meetings from 2023-06)",
        "stale_after_days": None,
    },
    "measures": [
        {"id": "approval_pct", "label": "Approval, as filed by the company (賛成割合)",
         "unit": "%", "trust": "official"},
        {"id": "for_votes", "label": "Votes for (voting-right units)", "unit": "voting_rights",
         "trust": "official"},
        {"id": "against_votes", "label": "Votes against", "unit": "voting_rights",
         "trust": "official"},
        {"id": "abstain_votes", "label": "Abstentions", "unit": "voting_rights",
         "trust": "official"},
        {"id": "partial_tally", "label": "Filer stopped tallying once the outcome was settled",
         "unit": "boolean", "trust": "official"},
        {"id": "approval_pct_of_counted", "label": "Approval over the disclosed counts",
         "unit": "%", "trust": "derived", "calc": CALC["approval_pct_of_counted"]},
        {"id": "against_pct_of_counted", "label": "Against over the disclosed counts",
         "unit": "%", "trust": "derived", "calc": CALC["against_pct_of_counted"]},
        {"id": "dissent_pct", "label": "Dissent", "unit": "%", "trust": "derived",
         "calc": CALC["dissent_pct"]},
        {"id": "pct_consistent", "label": "Filed and computed percentages agree within 15 pp",
         "unit": "boolean", "trust": "derived", "calc": CALC["pct_consistent"]},
    ],
    "endpoints": {
        "company": "/api/v1/equity/agm/company/{sec_code}",
        "summary": "/api/v1/equity/agm/summary",
        "screen": "/api/v1/equity/agm/directors",
        "search": "/api/v1/equity/agm/companies",
        "proposals": "/api/v1/equity/agm/proposals",
    },
    "capabilities": ["company", "search", "summary", "screen"],
    "screens": [
        {"id": "lowest", "title": "Directors elected on the lowest approval"},
        {"id": "highest", "title": "Directors elected on the highest approval"},
        {"id": "dismissal", "title": "Votes to remove a director (read the other way up)"},
        {"id": "proposals", "title": "Proposals by category, including shareholder proposals"},
    ],
    "cite": "/agm.html?company={sec_code}",
    "page": "/agm.html",
    "notes": [TALLY_NOTE, ELECTION_NOTE, UNIT_NOTE, VERIFY_NOTE, COVERAGE_NOTE],
}
