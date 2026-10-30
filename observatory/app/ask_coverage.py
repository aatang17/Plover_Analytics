# -*- coding: utf-8 -*-
"""Is every figure Plover holds answerable, however it is asked?

Every filed figure (app/us_figures.py) in every form a reader asks it:

  alone      "What is Alphabet's capital expenditure?"
  history    "Alphabet capital expenditure over the last 5 years"
  compare    "Compare capital expenditure of Alphabet, Microsoft and Meta"
  ranked     "Which S&P 500 company has the highest capital expenditure?"
  share      "Alphabet capital expenditure as a share of revenue"
  against    "Alphabet capital expenditure vs net income"

Two checks, because one costs money:

  check_code()  no model. The forms the fixed answers should take are asked with
                the model off, and for the forms the model answers, the query it
                would write is run on the real tables. A column missing or empty
                fails here. Cheap: run it before a deploy and in the test suite.
  check_model() the model answers the question on the real data. A refusal
                fails. A sample a night through the whole list (ask_review.py),
                or all of it by hand:

    ./.venv/bin/python -m app.ask_coverage            # code only
    ./.venv/bin/python -m app.ask_coverage --model 20 # and 20 model questions
"""
import argparse
import datetime
import json
import time

from fastapi import HTTPException

from . import ask_miss, sec_api, us_figures, us_link, us_steps

PAUSE_SECONDS = 0.1
CODE_FORMS = {"alone": "What is {co}'s {fig}?", "history": "{co} {fig} over the last 5 years",
              "compare": "Compare {fig} of {co}, {co2} and {co3}", "ranked": "Which S&P 500 company has the highest {fig}?"}
MODEL_FORMS = {"share": "{co} {fig} as a share of revenue", "against": "{co} {fig} vs net income"}
ALL_FORMS = dict(CODE_FORMS, **MODEL_FORMS)
# a share of revenue of a percentage, or of a share count, is not a question anyone means
_MONEY_ONLY = {"share", "against"}
# the labels people do not say: the words a reader uses for these
SPOKEN = {"cf_operating": "operating cash flow", "cf_investing": "investing cash flow", "cf_financing": "financing cash flow",
          "rd": "research spending", "sga": "selling, general and administrative expense", "da": "depreciation and amortisation",
          "ppe": "property, plant and equipment", "nii": "net interest income", "sbc": "stock-based compensation",
          "rd_intensity": "R&D intensity", "debt_to_equity": "debt-to-equity", "retained": "retained earnings"}
# a cover-page count and the parts a bank's revenue is built from are read by the tables, not asked for alone
_TABLE_ONLY = {"shares_outstanding", "interest_income", "noninterest_income", "lease_income", "eps_basic"}


def spoken(entry):
    return SPOKEN.get(entry["id"], entry["label"])


def _short(name):
    return ask_miss._short(us_link.display_name(name)) or name


def companies_for(cur):
    """{column: [three short company names]}: the largest S&P 500 members that
    filed the figure, so a miss is the box's and not the company's."""
    rows = sorted((r for r in us_steps.base_rows(cur) if r.get("in_sp500") and r.get("market_cap_usd")),
                  key=lambda r: -r["market_cap_usd"])
    out = {}
    for e in us_figures.catalogue():
        out[e["col"]] = [_short(r["name"]) for r in rows if r.get(e["col"]) is not None][:3]
    return out


def questions(cur, forms=None):
    """[{col, id, form, question}], every figure in every form it makes sense in."""
    named = companies_for(cur)
    out = []
    for e in us_figures.catalogue():
        cos = named.get(e["col"]) or []
        if e["id"] in _TABLE_ONLY or len(cos) < 3:
            continue
        for form, text in (forms or ALL_FORMS).items():
            if form in _MONEY_ONLY and e["unit"] != "USD":
                continue
            if form in _MONEY_ONLY and e["id"] in ("revenue", "net_income"):
                continue                                     # revenue as a share of revenue
            out.append({"col": e["col"], "id": e["id"], "form": form,
                        "question": text.format(co=cos[0], co2=cos[1], co3=cos[2], fig=spoken(e))})
    return out


def _ask(cur, question):
    """(True, answer) or (False, why): the box's whole answer path."""
    from . import stock_recipes
    try:
        out = stock_recipes.answer(cur, question)
    except HTTPException as exc:
        return False, "%s: %s" % (getattr(exc, "miss", None) or ask_miss.classify(str(exc.detail)), exc.detail)
    except us_steps.StepsError as exc:
        return False, "failed: %s" % exc
    head = (out or {}).get("headline")
    return (True, out) if head else (False, "no headline")


def read_right(out, entry):
    """Did the answer measure the figure asked about? Its words are in the
    headline or the table's columns; "highest gross profit" answered with net
    income would otherwise pass as answered."""
    table = (out.get("table") or {}).get("columns") or []
    text = " ".join([str(out.get("headline") or "")] + [str(c) for c in table]).lower().replace("\u2019", "'")
    words = {entry["label"].lower(), spoken(entry).lower(), entry["label"].lower().replace("shareholders'", "stockholders'"),
             "diluted eps" if entry["id"] == "eps_diluted" else entry["label"].lower(),
             "cash" if entry["id"] == "cash" else entry["label"].lower()}
    return any(w in text for w in words)


def check_tables(cur):
    """The queries the model would write for each figure, on the real tables.
    Failures: [{col, why}]; an empty column or a query that will not run."""
    fails = []
    today = datetime.date.today()
    start = datetime.date(today.year, 1, 1)
    for e in us_figures.catalogue():
        col = e["col"]
        queries = [("latest", "SELECT ticker, %s FROM stocks WHERE %s IS NOT NULL ORDER BY %s DESC LIMIT 5" % (col, col, col))]
        if e["id"] != "shares_outstanding":
            queries.append(("yearly", "SELECT ticker, fiscal_year, %s FROM annual WHERE %s IS NOT NULL ORDER BY fiscal_year DESC, ticker LIMIT 5" % (col, col)))
        if e["unit"] == "USD":
            queries.append(("share", "SELECT ticker, 100.0 * %s / revenue_usd AS share_pct FROM stocks WHERE revenue_usd > 0 AND %s IS NOT NULL LIMIT 5" % (col, col)))
        for name, sql in queries:
            try:
                rows, _ = us_steps.run(cur, sql, start)
            except us_steps.StepsError as exc:
                fails.append({"col": col, "why": "%s: %s" % (name, exc)})
                continue
            if not rows:
                fails.append({"col": col, "why": "%s: nothing filed under this figure" % name})
    return fails


def check_code(cur):
    """The fixed answers, model off, and the tables. {asked, failed: [...]}."""
    from . import us_ask_model
    with us_ask_model.no_model():
        asked, failed = 0, []
        entries = {e["id"]: e for e in us_figures.catalogue()}
        for q in questions(cur, CODE_FORMS):
            asked += 1
            ok, said = _ask(cur, q["question"])
            if not ok:
                failed.append(dict(q, why=said))
            elif not read_right(said, entries[q["id"]]):
                failed.append(dict(q, why="measured something else: %s" % said.get("headline")))
            time.sleep(PAUSE_SECONDS)                     # readers come first on the same server
        for f in check_tables(cur):
            failed.append({"col": f["col"], "form": "tables", "question": "", "why": f["why"]})
        return {"asked": asked, "failed": failed}


def check_model(cur, limit, offset=0):
    """`limit` model-answered questions, starting `offset` into the list and
    wrapping round, so a night's sample moves on through it. {asked, failed}."""
    every = questions(cur, MODEL_FORMS)
    if not every:
        return {"asked": 0, "failed": [], "total": 0}
    picked = [every[(offset + i) % len(every)] for i in range(min(limit, len(every)))]
    failed = []
    for q in picked:
        ok, said = _ask(cur, q["question"])
        if not ok:
            failed.append(dict(q, why=said))
    return {"asked": len(picked), "failed": failed, "total": len(every)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=int, default=0, help="also ask this many questions the model answers")
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args(argv)
    cur = sec_api._require()
    code = check_code(cur)
    print("Code: %d asked, %d failed" % (code["asked"], len(code["failed"])))
    for f in code["failed"]:
        print("  %s | %s | %s" % (f.get("form"), f.get("question") or f.get("col"), str(f.get("why"))[:160]))
    failed = len(code["failed"])
    if args.model:
        res = check_model(cur, args.model, args.offset)
        print("Model: %d of %d asked, %d failed" % (res["asked"], res["total"], len(res["failed"])))
        for f in res["failed"]:
            print("  %s | %s | %s" % (f["form"], f["question"], str(f["why"])[:160]))
        failed += len(res["failed"])
    print(json.dumps({"failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
