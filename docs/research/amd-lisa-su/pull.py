"""Pull every Plover series the AMD piece uses; save CSVs with a metadata header; append to log.md.

Run from anywhere:  python3 pull.py <port> [TICKER:Tag ...]
Numbers come only from /api/v1/us/financials/deep/{ticker}/series, frozen at AS_OF.
"""
import csv
import datetime as dt
import json
import os
import sys
import urllib.request

AS_OF = "2026-09-30"
PORT = sys.argv[1] if len(sys.argv) > 1 else "8007"
BASE = "http://localhost:%s" % PORT
PUBLIC = "https://ploveranalytics.com"  # citable form of the same path
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

TAGS = {
    "AMD": ["SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax", "GrossProfit",
            "ResearchAndDevelopmentExpense", "OperatingIncomeLoss", "NetIncomeLoss",
            "NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
            "RestructuringCharges", "ShareBasedCompensation", "WeightedAverageNumberOfDilutedSharesOutstanding",
            "PaymentsForRepurchaseOfCommonStock",
            "CashAndCashEquivalentsAtCarryingValue", "MarketableSecuritiesCurrent", "ShortTermInvestments",
            "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent", "DebtCurrent",
            "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "Goodwill", "StockholdersEquity", "CommonStockSharesOutstanding", "InventoryNet"],
    "INTC": ["SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax", "GrossProfit",
             "ResearchAndDevelopmentExpense", "OperatingIncomeLoss", "NetIncomeLoss",
             "PaymentsToAcquirePropertyPlantAndEquipment"],
    "NVDA": ["Revenues", "GrossProfit", "ResearchAndDevelopmentExpense", "OperatingIncomeLoss",
             "NetIncomeLoss"],
}


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def main():
    global TAGS
    if len(sys.argv) > 2:  # e.g. AMD:AmortizationOfIntangibleAssets — pull and log only these
        TAGS = {}
        for a in sys.argv[2:]:
            tk, tag = a.split(":")
            TAGS.setdefault(tk, []).append(tag)
    os.makedirs(DATA, exist_ok=True)
    log = os.path.join(HERE, "log.md")
    n = sum(1 for line in open(log, encoding="utf-8") if line.startswith("| ") and line[2:3].isdigit()) \
        if os.path.exists(log) else 0
    rows = []
    for tk, tags in TAGS.items():
        for tag in tags:
            path = "/api/v1/us/financials/deep/%s/series?tag=%s&freq=annual&basis=latest&as_of=%s" % (tk, tag, AS_OF)
            d = get(path)
            vals = d.get("values") or []
            if not vals:  # balances are read on period ends; ask for every filed period
                path = path.replace("freq=annual", "freq=all")
                d = get(path)
                vals = d.get("values") or []
            out = os.path.join(DATA, "%s_%s.csv" % (tk, tag))
            with open(out, "w", newline="", encoding="utf-8") as f:
                f.write("# source: %s\n# cite: %s%s\n# credit: %s\n# basis: latest as of %s; figures as filed, not recomputed\n"
                        % (d["provenance"]["note"], PUBLIC, path, d["provenance"]["credit"], AS_OF))
                w = csv.writer(f)
                w.writerow(["period_start", "period_end", "value", "unit", "fy", "fp", "form", "filed", "accn", "source_url"])
                for v in vals:
                    w.writerow([v.get("period_start"), v["period_end"], v["value"], v["unit"], v["fy"], v["fp"],
                                v["form"], v["filed"], v["accn"], v["source_url"]])
            n += 1
            ends = sorted(v["period_end"] for v in vals)
            rows.append("| %d | %s | GET %s | tag=%s | %d values, %s → %s | %s%s | latest filed ≤ %s |"
                        % (n, dt.date.today().isoformat(), "/api/v1/us/financials/deep/%s/series" % tk, tag,
                           len(vals), ends[0] if ends else "—", ends[-1] if ends else "—", PUBLIC, path, AS_OF))
    new = not os.path.exists(log)
    with open(log, "a", encoding="utf-8") as f:
        if new:
            f.write("# AMD under Lisa Su — research log\n\n"
                    "| # | When | Tool / URL | Arguments | Returned (one line) | Cite URL (with as_of) | Release / vintage |\n"
                    "|---|---|---|---|---|---|---|\n")
        f.write("\n".join(rows) + "\n")
    print("logged", len(rows))


if __name__ == "__main__":
    main()
