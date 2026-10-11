# Saved Futures in Plover Research

Databento hourly futures are a **private market-data namespace**, separate from
the public macroeconomic adapters. The macro schema stores dates and cannot
represent hourly bars. Its schema, registry and public data endpoints are
unchanged. This namespace follows the separate-schema pattern used by equity
data and the personal-account access used by Research connections.

## Storage and Import

The serving process opens `data/market-history/history.sqlite3` read-only.
`MARKET_HISTORY_DB` can point at another file for isolated verification.
Prices remain decimal strings; undefined prices remain SQL NULL. Volume is a
count of contracts. Hours are UTC interval starts. Publishers and contract IDs
are part of the observation key; ICE exchange trades and off-market trades
are separate feeds. CME Nikkei yen is a futures contract, not the cash index
or Osaka futures. NQ and NIY prices are index points; Brent is USD/barrel.

Run the importer from `observatory/` after the downloader has completed:

```sh
./.venv/bin/python tools/import_databento.py --source data/databento \
  --owner researcher@example.com --owner colleague@example.com
```

This makes **no network or billable vendor request**. Every original gzip is
checked against its receipt. Timestamp bounds, receipt counts, publishers,
contract mappings, continuous-roll identifiers and OHLC relationships are
validated. All products validate before a single transaction publishes any
rows or account grants. Content-addressed originals and immutable receipts
are also copied into `data/market-history/raw/`.

Identical source hashes skip stored observations. Changed hashes create a new
version, retaining the earlier version. Reimporting a source adds the listed
email grants; it does not remove existing grants. Account emails are stored
in the database, never hard-coded in application code. Both account-password
and email-link Research sign-ins resolve the same email-based grant. The
shared administrator password has no access.

## Research Usage

Open an article's **Research → New Tab → Your Saved Futures**, or search for
Brent, Nikkei, NQ or Databento. **Insert Chart** freezes the last 30 calendar
days of saved hourly closes into the draft. Expand **Chart Dates and Trade
Feed** to change dates, select OHLC or volume, or choose the ICE off-market
feed. Charts show up to 120 days and keep gaps at absent hours, undefined
prices and contract rolls. They carry dataset, continuous symbol, publisher,
version and date-range provenance. Editing chart cells changes its provenance
to author-entered values.

**Download CSV** exports the selected feed's full history when Start is blank.
CSV includes all original OHLCV values, UTC timestamps, instrument IDs, raw
contract symbols and source artifact hashes. The header states units, source,
version, range and unadjusted roll convention. It does not discard the first
bar at a contract change. Chart CSV preserves hourly timestamps.

Authenticated routes under `/admin/api/research/market/{slug}/`:

- `history`: `start`, `end` (inclusive dates), `publisher`, `revision`, `limit`
  (1–1,000) and `offset`; returns `next_offset` for pagination.
- `chart`: the same dates, publisher and version, plus `field` (OHLC or volume).
- `csv`: the same dates, publisher and version; streaming full-resolution data.

Private responses, search and the saved futures catalog use `Cache-Control: private,
no-store`. Research AI and authenticated Research MCP expose
`search_saved_futures` and `get_saved_futures` only to granted accounts. Every
read checks the account again, including a call using a guessed tool name.
These tools read the same module and prices as the authenticated endpoints.
No prices or email grants enter the public macro catalog or public MCP.

## Freshness and Deployment

`/api/v1/catalog/health` reports each saved dataset under `private_market_data`.
Only operational metadata is public. Prices and access principals are excluded.
After seven calendar days without newer observations, a dataset reports
attention. A missing/corrupt configured database reports monitoring unavailable.
Automatic downloads and imports are **not enabled**; these are saved historical
snapshots. Subsequent imports can extend the history without changing prior
versions. The stored history currently ends on 9 October 2026.

The database and raw directory must stay under the persistent production
`data/` volume and be backed up together. Do not place this paid archive in a
public seed, Git, a static web directory, or the public macro download API.
The import performed on 11 October 2026 contains 279,418 hourly observations
across three futures products. For deployment, validate the transferred archive
and store it on the persistent volume before rolling out the application.
Verify both account catalogs, chart reads, full CSV counts and denied access
after deployment. Off-machine backup is not configured by this change.
