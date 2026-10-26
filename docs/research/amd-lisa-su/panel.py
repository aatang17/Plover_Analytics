"""Year-by-year panel from data/*.csv (fiscal year = year of period_end). Values in $m unless noted."""
import csv, glob, os, sys
def load(tk, tag):
    p = "data/%s_%s.csv" % (tk, tag)
    if not os.path.exists(p): return {}
    out = {}
    rows = [r for r in csv.DictReader(l for l in open(p) if not l.startswith("#"))]
    for r in rows:
        if r["period_start"]:  # duration: keep ~annual only
            import datetime as dt
            d = (dt.date.fromisoformat(r["period_end"]) - dt.date.fromisoformat(r["period_start"])).days
            if not 350 <= d <= 380: continue
        y = int(r["period_end"][:4]) if r["period_end"][5:7] > "03" else int(r["period_end"][:4]) - 1
        if tk == "NVDA": y = int(r["period_end"][:4])  # NVDA FY ends late Jan: label by FY (period_end year)
        out[y] = float(r["value"])
    return out
def first(*ds):
    ys = set().union(*ds); return {y: next(d[y] for d in ds if y in d) for y in ys}
def panel(tk):
    rev = first(load(tk,"RevenueFromContractWithCustomerExcludingAssessedTax"), load(tk,"SalesRevenueNet"), load(tk,"Revenues"))
    P = dict(rev=rev, gp=load(tk,"GrossProfit"), rd=load(tk,"ResearchAndDevelopmentExpense"), oi=load(tk,"OperatingIncomeLoss"),
             ni=load(tk,"NetIncomeLoss"), cfo=load(tk,"NetCashProvidedByUsedInOperatingActivities"), capex=load(tk,"PaymentsToAcquirePropertyPlantAndEquipment"),
             restr=load(tk,"RestructuringCharges"), sbc=load(tk,"ShareBasedCompensation"), dil=load(tk,"WeightedAverageNumberOfDilutedSharesOutstanding"),
             buyb=load(tk,"PaymentsForRepurchaseOfCommonStock"), cash=load(tk,"CashAndCashEquivalentsAtCarryingValue"),
             sti=first(load(tk,"ShortTermInvestments"), load(tk,"MarketableSecuritiesCurrent")),
             ltd=load(tk,"LongTermDebt"), ltdnc=load(tk,"LongTermDebtNoncurrent"), ltdc=load(tk,"LongTermDebtCurrent"), dc=load(tk,"DebtCurrent"),
             ltdcl=load(tk,"LongTermDebtAndCapitalLeaseObligations"), ltdclc=load(tk,"LongTermDebtAndCapitalLeaseObligationsCurrent"),
             gw=load(tk,"Goodwill"), eq=load(tk,"StockholdersEquity"), sh=load(tk,"CommonStockSharesOutstanding"), inv=load(tk,"InventoryNet"))
    return P
if __name__ == "__main__":
    tk = sys.argv[1]; P = panel(tk); keys = sys.argv[2].split(",") if len(sys.argv) > 2 else list(P)
    ys = sorted(set().union(*[P[k] for k in keys]))
    print("FY   " + " ".join("%9s" % k for k in keys))
    for y in ys:
        print(y, " ".join("%9s" % (("%.0f" % (P[k][y]/1e6)) if y in P[k] else "—") for k in keys))
