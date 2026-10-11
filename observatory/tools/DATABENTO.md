# Databento futures bars

Checked 11 October 2026. The extended hourly trial has been downloaded and verified. See the [saved data and reuse instructions](../data/databento/README.md) and [inventory](../data/databento/inventory.json).

## Hourly trial

The downloader now defaults to **OHLCV-1h**, one rolling contract per product:
Brent (`BRN.v.0`), CME Nikkei 225 yen (`NIY.v.0`), and E-mini Nasdaq-100
(`NQ.v.0`). Dates and products remain configurable. The CLI default dates remain
the initial seven-year window; the completed extended ranges and exact rerun
commands are documented below. The CME Nikkei future is a proxy, not the Osaka-listed contract.

Hourly bars contain open, high, low, close, and traded volume. Timestamps mark
the start of each hour. Hours without trades have no record and stay missing.
See [Databento's OHLCV conventions](https://databento.com/docs/schemas-and-data-formats/ohlcv).
Contract-roll jumps still need to be excluded from returns analysis.

**The extended hourly trial is saved locally.** Brent was requested from
23 December 2018; CME Nikkei and Nasdaq from 6 June 2010. All three run through
9 October 2026. The receipts record an estimated total usage of **US$12.69932635**,
before credits; the billing statement has not been checked. There are **279,418
records**, original annual files and receipts, and one reusable CSV per product.
The figures below are earlier minute-bar estimates and do not price this trial.

Use `--schema ohlcv-1m` to explicitly request the earlier minute resolution.
Hourly output names include `_ohlcv-1h`; existing minute files keep their original
names, so the resolutions cannot be mistaken for each other.

## Earlier minute-bar calculator estimate

The public [cost calculator](https://databento.com/pricing) displayed the following
for **11 October 2019 through 9 October 2026 inclusive (UTC)**, **OHLCV-1m**, and
one futures product at a time. The calculator labels this **2,556 days**.

| Product | Dataset | API parent symbol | Estimated usage cost (USD) | Calculator size |
| --- | --- | --- | ---: | ---: |
| [ICE Brent](https://databento.com/catalog/ifeu/IFEU.IMPACT/futures/BRN) | IFEU.IMPACT | BRN.FUT | 1,021.77 | 3.7 GB |
| [CME Nikkei 225, yen](https://databento.com/catalog/cme/GLBX.MDP3/futures/NIY) | GLBX.MDP3 | NIY.FUT | 8.72 | 133.8 MB |
| [CME E-mini Nasdaq-100](https://databento.com/catalog/cme/GLBX.MDP3/futures/NQ) | GLBX.MDP3 | NQ.FUT | 13.09 | 200.8 MB |
| Total | | | **1,043.58** | |

These are public calculator estimates, before any account credits, discounts,
or applicable taxes. All three displayed **No subscription required**. Brent
history starts 23 December 2018; the CME catalog starts 6 June 2010. The seven-year
window fits within those published ranges. Exact schema coverage is checked by
the script using the account's metadata before any paid request.

**Scope matters:** [parent symbols](https://databento.com/docs/examples/symbology/parent-symbology)
include every expiry and futures spreads. This is the calculator's product scope.
These are separate contract bars, not a single stitched series. The Nasdaq contract
is NQ (E-mini), not MNQ (Micro).

The downloader now defaults to **one rolling outright per product** (`--scope continuous`). The default roll
selects the contract with the previous day's highest volume. Change it with
`--roll calendar` or `--roll open-interest`. [Databento's continuous prices](https://databento.com/docs/standards-and-conventions/symbology)
are unadjusted and can jump at a roll. The portal does not offer continuous contract
selection, so an exact quote for that narrower request needs the free metadata API.
No continuous-contract price has been obtained yet.

## Create your own key

1. Log in and open [API Keys](https://databento.com/portal/keys).
2. Click **Create a new key**, name it `plover-futures-history`, then **Create**.
3. Privately add `DATABENTO_API_KEY=` followed by the key to `observatory/.env`.
   Add to your existing file; do not replace its other settings. No key belongs in chat.
4. Set the file's permissions with `chmod 600 .env` from `observatory/`.

The private `.env` and `data/` output directory are already ignored by Git. The
script reads only the Databento entry. It never prints credentials or error bodies,
and sends the credential only as an HTTPS authentication header to Databento's
historical API. See the official [key guide](https://databento.com/docs/portal/api-keys).

## Use the script

Run from `observatory/`. Python 3.9+ is enough; no additional packages are needed.

```sh
# Inspect the rolling-contract requests offline; no key or API calls.
.venv/bin/python tools/databento_download.py --plan

# Exact account estimate, free metadata calls only; no download.
.venv/bin/python tools/databento_download.py

# Only when intentionally quoting every expiry and spread (much larger).
.venv/bin/python tools/databento_download.py --scope all
```

Only after reviewing the estimate, run the same scope/date options with
`--download --max-cost-usd YOUR_APPROVED_DOLLAR_LIMIT`. The script quotes again,
refuses estimates above that limit, then requires you to type `DOWNLOAD` in the
local terminal before a paid request. There is no unattended confirmation bypass.

`--max-cost-usd` limits the estimate, not the provider's final invoice. Databento
bills delivered data. Use the portal's historical usage limits for an account
spending limit. No live-data subscriptions or license agreements are activated.

Downloads are streamed into annual `.csv.gz` files under `data/databento/`, with
readable UTC timestamps, scaled prices, instrument IDs, and publisher IDs.
Continuous-contract receipts include free date-specific mappings from the rolling
symbol to instrument IDs and from those IDs to original contract symbols. These
allow the analysis to identify rollovers without interpreting them as returns.
Each file has a JSON receipt with the request, estimate, record
count, and SHA-256. Completed files with matching receipts are skipped on reruns.
There are no automatic retries. An interrupted `.partial` file blocks a repeat
request until you review it; removing it and retrying can incur charges again.

The end date is **exclusive**: `--end 2026-10-10` includes all of 9 October.
Dates and products are configurable; for example `--products nikkei-yen nasdaq-100`.
Missing intervals remain missing. ICE on-market and off-market publishers remain
separate; do not sum or merge duplicate-looking bars without inspecting publisher IDs.
This is a local download tool. It does not import or publish paid data into Plover.

API implementation follows Databento's [Historical HTTP reference](https://databento.com/docs/api-reference-historical?historical=http).
Metadata and symbology calls are free; time-series requests are billable.

## Completed extended hourly trial

The user approved the extended scope at the quoted US$12.70 estimate. Downloads
completed on 11 October 2026. Brent has 9 annual files; Nikkei and Nasdaq each
have 17. Checksums, row counts, timestamps, contract mappings, duplicate keys,
volume and present-price OHLC relationships passed. Rerunning the exact requests
below recognized every original file as complete and made no vendor requests:

```sh
.venv/bin/python -B tools/databento_download.py --start 2018-12-23 --end 2026-10-10 --products brent --schema ohlcv-1h
.venv/bin/python -B tools/databento_download.py --start 2010-06-06 --end 2026-10-10 --products nikkei-yen nasdaq-100 --schema ohlcv-1h
```

First returned bars are 26 December 2018 for Brent and 7 June 2010 for Nikkei
and Nasdaq. Brent contains 244 volume records with blank OHLC prices; these
remain blank and are counted by publisher in the inventory. Source conventions
explicitly allow empty CSV price fields for undefined prices. ICE publisher
records remain separate. Contract-roll jumps need to be excluded from returns.

The reusable files are `data/databento/series/brent_ohlcv-1h.csv.gz`,
`data/databento/series/nikkei-yen_ohlcv-1h.csv.gz`, and
`data/databento/series/nasdaq-100_ohlcv-1h.csv.gz`. Reading them locally requires
no API calls or repeat purchase. This is local storage, with no cloud backup,
automatic refresh, or Plover/Rsrch import configured.
