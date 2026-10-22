"""Ingestion runner: fetch -> archive -> parse -> validate -> publish.

Usage:  python -m app.ingest cpi-jp [--from-file path.csv]

Every run archives the raw artifact with its checksum before parsing.
If the checksum matches the newest artifact for the source, the run
stops (nothing new). A failed validation records nothing but the
artifact — the previous published release stays untouched.

Publishing replaces the dataset's live observations in one transaction
and marks the prior release 'superseded' — but it also appends what it
changed to observation_vintages, which is never rewritten. Series rows
are upserted so series_id stays stable across releases. Raw artifacts
are retained for every run.
"""
import argparse
import datetime
import hashlib
import json
import sys

from . import db, env

# Adapters that call a keyed API read their credential from the environment;
# .env is the local-development fallback the server already loads. Without
# this the ingest fails only for keyed sources, and only outside the server.
env.load()

from .adapters import (boj_assets, cpi_jp, cpi_jp_goods_services, cpi_jp_items,
                       cpi_jp_long, cpi_jp_sa, cpi_tokyo, cpi_tokyo_items,
                       jnto_visitors,
                       jta_accommodation,
                       juki_municipal, juki_population, maff_agri_prices,
                       maff_ja_coops,
                       maff_rice_cost,
                       maff_rice_price,
                       maff_rice_stock,
                       mof_debt, mof_fiscal, mof_fiscal_expenditure,
                       mof_fiscal_ministry, mof_fiscal_revenue,
                       mof_jgb, mof_tax_receipts,
                       mof_trade, mof_trade_autos, mof_trade_energy,
                       mof_trade_food, mof_trade_hs, mof_trade_machinery,
                       mof_trade_pharma,
                       soumu_local_finance, ssds_population,
                       ust_real_yields, ust_yields,
                       estat_gdp, estat_gfs, mof_hojin,
                       fsa_npl, fsa_bank_results, jba_banks,
                       boj_loan_rates, boj_deposit_rates, fsa_fi_list,
                       jpx_margin, jpx_investor_type,
                       cpi_us, cpi_us_sa, cpi_us_areas, cpi_us_weights,
                       us_avg_prices, us_wages, us_wage_tracker, us_labour,
                       lfs_unemployment, mhlw_job_openings, mhlw_monthly_labour,
                       cpi_hk, cpi_hk_rates, gdp_hk, labour_hk, retail_hk, trade_hk)

ADAPTERS = {"cpi-jp": cpi_jp, "cpi-jp-items": cpi_jp_items, "boj-assets": boj_assets,
            "cpi-jp-goods-services": cpi_jp_goods_services, "cpi-jp-sa": cpi_jp_sa,
            "cpi-jp-long": cpi_jp_long,
            "cpi-tokyo": cpi_tokyo, "cpi-tokyo-items": cpi_tokyo_items,
            "fiscal-jp": mof_fiscal,
            "tax-receipts-jp": mof_tax_receipts,
            "govt-debt-jp": mof_debt,
            "fiscal-jp-revenue": mof_fiscal_revenue,
            "fiscal-jp-expenditure": mof_fiscal_expenditure,
            "fiscal-jp-ministry": mof_fiscal_ministry,
            "govt-accounts-jp": estat_gfs,
            "local-finance-jp": soumu_local_finance,
            "jgb-yields": mof_jgb, "jnto-visitors": jnto_visitors,
            "ust-yields": ust_yields,
            "ust-real-yields": ust_real_yields,
            "population-jp": juki_population,
            "population-jp-history": ssds_population,
            "gdp-jp": estat_gdp,
            "corporate-finance-jp": mof_hojin,
            "population-jp-municipal": juki_municipal,
            "trade-semis": mof_trade, "trade-inputs": mof_trade_hs,
            "trade-autos": mof_trade_autos, "trade-energy": mof_trade_energy,
            "trade-machinery": mof_trade_machinery, "trade-pharma": mof_trade_pharma,
            "trade-food": mof_trade_food,
            "accommodation-jp": jta_accommodation,
            "rice-prices-jp": maff_rice_price,
            "rice-inventory-jp": maff_rice_stock,
            "agri-prices": maff_agri_prices,
            "rice-production-cost": maff_rice_cost,
            "ja-statistics": maff_ja_coops,
            "fsa-npl": fsa_npl, "fsa-bank-results": fsa_bank_results,
            "jba-banks": jba_banks,
            "boj-loan-rates": boj_loan_rates,
            "boj-deposit-rates": boj_deposit_rates,
            "fsa-regional-fi": fsa_fi_list,
            "margin-jp": jpx_margin,
            "investor-flows-jp": jpx_investor_type,
            "cpi-us": cpi_us, "cpi-us-sa": cpi_us_sa,
            "cpi-us-areas": cpi_us_areas, "cpi-us-weights": cpi_us_weights,
            "us-avg-prices": us_avg_prices, "us-wages": us_wages,
            "us-wage-tracker": us_wage_tracker, "us-labour": us_labour,
            "unemployment-jp": lfs_unemployment,
            "job-openings-jp": mhlw_job_openings,
            "wages-jp": mhlw_monthly_labour,
            # Hong Kong: the Census and Statistics Department (censtatd.py).
            "cpi-hk": cpi_hk, "cpi-hk-rates": cpi_hk_rates, "gdp-hk": gdp_hk,
            "labour-hk": labour_hk, "retail-hk": retail_hk, "trade-hk": trade_hk}

# "this (series, period) had no prior value at all" — distinct from a prior
# value that happens to be None.
_ABSENT = object()


def _many(con, sql, rows):
    """executemany that tolerates nothing to do.

    DuckDB raises on an empty parameter list, and empty is the normal case
    here: a month where no series was added, none was retired, or nothing was
    revised is a successful ingest, not a failed one.
    """
    if rows:
        con.executemany(sql, rows)


def _check_periods(adapter, slug, observations):
    """Every adapter must date an observation with a datetime.date.

    A string that merely looks like a date is the bug this catches, and it is
    a nasty one because it does not fail where it is made. The period an
    adapter emits is compared against the period read back from DuckDB, which
    is always a date; a string equals none of them, so the point-in-time diff
    concludes that every value is new and every stored value has been
    withdrawn, and writes the same (series, period) twice into one release.
    What surfaces, thousands of lines later, is a primary key violation on
    observation_vintages naming an id and a date from 1990.

    That is exactly how ust-yields and ust-real-yields failed every night
    from their first publish on 2026-09-15. The first ingest of a dataset
    cannot show it — there is nothing to compare against — so the fault ships
    looking healthy and appears only on the second run.

    Checked once here rather than left to each adapter's validate(), because
    it is a contract between every adapter and the store, and forty-three
    adapters is forty-three chances to get it wrong.
    """
    for o in observations:
        period = o.get("period")
        # bool is not a concern; datetime IS a date subclass and would store
        # a silent time component, so it is rejected rather than accepted.
        if type(period) is not datetime.date:
            raise adapter.ValidationError(
                "%s: observation period must be a datetime.date, got %r (%s). "
                "A string here breaks the vintage diff on every ingest after "
                "the first." % (slug, period, type(period).__name__))


def run(slug, from_file=None):
    adapter = ADAPTERS[slug]

    if from_file:
        raw = open(from_file, "rb").read()
        origin = "file://" + from_file
    else:
        print("fetching %s ..." % adapter.DOWNLOAD_URL)
        raw = adapter.fetch()
        origin = adapter.DOWNLOAD_URL
    sha = hashlib.sha256(raw).hexdigest()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    con = db.connect()
    try:
        # Prefer UPDATE-then-INSERT over INSERT OR REPLACE / ON CONFLICT.
        # DuckDB ≥1.4 rejects REPLACE (and some upserts) when child rows still
        # reference the parent, and also rejects UPDATEs that touch a column
        # that is itself a foreign key (sources.dataset → datasets.slug) while
        # artifacts reference the source. Metadata that can change is updated;
        # identity columns (slug, source_id, dataset) are left alone.
        if con.execute("SELECT 1 FROM datasets WHERE slug=?", [slug]).fetchone():
            con.execute(
                "UPDATE datasets SET title=?, country=?, agency=?, agency_ja=?, "
                "base=?, frequency=?, description=? WHERE slug=?",
                [adapter.DATASET[k] for k in
                 ("title", "country", "agency", "agency_ja", "base", "frequency", "description")]
                + [slug],
            )
        else:
            con.execute(
                "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?)",
                [adapter.DATASET[k] for k in
                 ("slug", "title", "country", "agency", "agency_ja", "base", "frequency", "description")],
            )
        source_id = adapter.SOURCE["source_id"]
        if con.execute("SELECT 1 FROM sources WHERE source_id=?", [source_id]).fetchone():
            con.execute(
                "UPDATE sources SET name=?, name_ja=?, url=?, license_note=? WHERE source_id=?",
                [adapter.SOURCE["name"], adapter.SOURCE["name_ja"],
                 adapter.SOURCE["url"], adapter.SOURCE["license_note"], source_id],
            )
        else:
            con.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?)",
                [source_id, slug, adapter.SOURCE["name"],
                 adapter.SOURCE["name_ja"], adapter.SOURCE["url"], adapter.SOURCE["license_note"]],
            )

        prior = con.execute(
            "SELECT a.sha256, a.path FROM source_artifacts a "
            "JOIN releases r ON r.artifact_id = a.artifact_id AND r.status = 'published' "
            "WHERE a.source_id=? ORDER BY a.retrieved_at DESC LIMIT 1",
            [adapter.SOURCE["source_id"]],
        ).fetchone()
        if prior and prior[0] == sha:
            print("unchanged: newest artifact already has sha256 %s..." % sha[:12])
            return 0
        # Sources whose response embeds a fetch timestamp never repeat
        # byte-for-byte; such adapters expose canonical_bytes() and the
        # unchanged check compares canonical content against the archived
        # copy of the newest published artifact.
        canonical = getattr(adapter, "canonical_bytes", None)
        if prior and canonical is not None:
            try:
                prior_raw = open(prior[1], "rb").read()
            except OSError:
                prior_raw = None
            if prior_raw is not None and canonical(prior_raw) == canonical(raw):
                print("unchanged: canonical content matches artifact %s..." % prior[0][:12])
                return 0

        # archive first, validate second — evidence is kept even for bad files
        db.RAW_DIR.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        suffix = getattr(adapter, "RAW_SUFFIX", ".csv")
        path = db.RAW_DIR / ("%s-%s-%s%s" % (slug, stamp, sha[:12], suffix))
        path.write_bytes(raw)
        artifact_id = con.execute(
            "INSERT INTO source_artifacts (source_id, url, retrieved_at, sha256, path, bytes) "
            "VALUES (?,?,?,?,?,?) RETURNING artifact_id",
            [adapter.SOURCE["source_id"], origin, now, sha, str(path), len(raw)],
        ).fetchone()[0]

        series, observations = adapter.parse(raw)
        _check_periods(adapter, slug, observations)
        summary = adapter.validate(series, observations)
        # The whole summary is stored on the release, but a dataset that
        # carries its geography in it (every municipality in Japan) would
        # otherwise put 230KB of it into the boot log on every ingest. Long
        # lists are summarised for the log only.
        printable = dict(
            (k, "<%d items>" % len(v) if isinstance(v, (list, dict)) and len(v) > 20 else v)
            for k, v in summary.items())
        print("validated: %s" % json.dumps(printable))

        latest = datetime.date.fromisoformat(summary["latest_period"])
        # A daily dataset gets daily vintages; a month-only label would give
        # many releases the same name, so the label carries the day.
        frequency = adapter.DATASET.get("frequency")
        label_fmt = "%d %B %Y" if frequency == "daily" else "%B %Y"
        # A quarter is dated by its first month; "Data through April 2026"
        # would read as a month, so a quarterly dataset names the quarter.
        if frequency == "quarterly":
            label_fmt = "Q%d %%Y" % ((latest.month - 1) // 3 + 1)
        # The live values this release is about to replace. Read before the
        # transaction rewrites them: the vintage delta is the difference
        # between these and the incoming ones.
        previous = {(code, period): value for code, period, value in con.execute(
            "SELECT s.code, o.period, o.value FROM observations o "
            "JOIN series s USING(series_id) WHERE s.dataset=?", [slug]).fetchall()}
        existing_codes = set(
            c for (c,) in con.execute(
                "SELECT code FROM series WHERE dataset=?", [slug]).fetchall())

        con.execute("BEGIN")
        con.execute("UPDATE releases SET status='superseded' WHERE dataset=? AND status='published'", [slug])
        release_id = con.execute(
            "INSERT INTO releases (dataset, artifact_id, label, latest_period, ingested_at, status, validation) "
            "VALUES (?,?,?,?,?,?,?) RETURNING release_id",
            [slug, artifact_id, "Data through %s" % latest.strftime(label_fmt),
             latest, now, "published", json.dumps(summary)],
        ).fetchone()[0]

        # Series rows are upserted, never replaced. series_id is the stable
        # identity that observation_vintages — and any external reference —
        # hangs off; delete-and-reinsert would hand every series a fresh id
        # from the sequence on every ingest and orphan the history. Identity
        # columns are left alone: DuckDB rejects an UPDATE touching a column
        # that is itself a foreign key while child rows reference the parent.
        _many(con,
            "UPDATE series SET name_en=?, name_ja=?, unit=?, weight_per_10000=?, "
            "sort_order=?, active=TRUE WHERE dataset=? AND code=?",
            [[s["name_en"], s["name_ja"], s.get("unit", "index"),
              s["weight_per_10000"], s["sort_order"], slug, s["code"]]
             for s in series if s["code"] in existing_codes],
        )
        _many(con,
            "INSERT INTO series (dataset, code, name_en, name_ja, unit, weight_per_10000, sort_order) "
            "VALUES (?,?,?,?,?,?,?)",
            [[slug, s["code"], s["name_en"], s["name_ja"], s.get("unit", "index"),
              s["weight_per_10000"], s["sort_order"]]
             for s in series if s["code"] not in existing_codes],
        )
        # A series the source has stopped publishing is retired, not removed:
        # its history stays queryable at the vintages where it existed.
        retired = sorted(existing_codes - set(s["code"] for s in series))
        _many(con,
            "UPDATE series SET active=FALSE WHERE dataset=? AND code=?",
            [[slug, code] for code in retired],
        )
        code_to_id = dict(con.execute(
            "SELECT code, series_id FROM series WHERE dataset=?", [slug]).fetchall())

        con.execute(
            "DELETE FROM observations WHERE series_id IN (SELECT series_id FROM series WHERE dataset=?)",
            [slug],
        )
        _many(con,
            "INSERT INTO observations VALUES (?,?,?,?)",
            [[code_to_id[o["code"]], o["period"], o["value"], release_id] for o in observations],
        )

        # Point-in-time history: append what this release introduced or
        # changed. An unchanged value writes nothing — the value as of any
        # date is the newest row at or before it, so republishing the same
        # number carries no information. A value that has *gone* does, hence
        # the explicit NULL tombstone.
        vintage = []
        for o in observations:
            prior = previous.pop((o["code"], o["period"]), _ABSENT)
            if prior is _ABSENT or prior != o["value"]:
                vintage.append([code_to_id[o["code"]], o["period"], o["value"], release_id])
        withdrawn = sorted(previous)
        vintage.extend([code_to_id[code], period, None, release_id]
                       for code, period in withdrawn)
        _many(con, "INSERT INTO observation_vintages VALUES (?,?,?,?)", vintage)

        con.execute("COMMIT")
        print("published release %d: %d series, %d observations, latest %s"
              % (release_id, len(series), len(observations), latest))
        print("vintage: %d new or revised values, %d withdrawn%s"
              % (len(vintage) - len(withdrawn), len(withdrawn),
                 ", %d series retired" % len(retired) if retired else ""))
        # The pages this release changed, told to the search indexes. Sent
        # only when INDEXNOW_KEY is set; never a reason for a non-zero exit.
        from . import indexnow
        if indexnow.notify_dataset(slug):
            print("indexnow: notified for %s" % slug)
        return 0
    except adapter.ValidationError as exc:
        print("VALIDATION FAILED — nothing published: %s" % exc, file=sys.stderr)
        return 1
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Observatory ingestion")
    ap.add_argument("dataset", choices=sorted(ADAPTERS))
    ap.add_argument("--from-file", help="parse a local CSV instead of downloading")
    args = ap.parse_args()
    sys.exit(run(args.dataset, args.from_file))


if __name__ == "__main__":
    main()
