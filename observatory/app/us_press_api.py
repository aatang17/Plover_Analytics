# -*- coding: utf-8 -*-
"""S&P 500 press releases, served: /api/v1/us/press. Read-only over the
store app/us_press.py keeps (data/us-press.sqlite, opened mode=ro).

  GET /api/v1/us/press           which members have a news feed, and the newest poll
  GET /api/v1/us/press/{ticker}  a company's latest releases and the capital-return
                                 figures read from them, each with its sentence
"""
from fastapi import APIRouter, HTTPException, Query

from . import us_press

router = APIRouter(prefix="/api/v1/us/press", tags=["US press releases"])

PROVENANCE = {
    "trust": "company",
    "credit": us_press.CREDIT,
    "note": ("Headlines, dates and addresses as each company's feed gives them. Figures are read from the "
             "release text and shown with the sentence they came from. A release is the company's own "
             "announcement, not a filing: an approval to buy back shares is not shares bought."),
}


def _require():
    """The registry's availability gate: the release store is on this server."""
    if not us_press.DB_PATH.exists():
        raise HTTPException(503, "Press releases are not loaded on this server yet.")


@router.get("", openapi_extra={"x-example": "/api/v1/us/press"})
def coverage():
    """Which S&P 500 members have a news feed, and the newest poll."""
    feeds = us_press.load_feeds()
    return {"members": len(feeds), "with_feed": sum(1 for r in feeds.values() if r.get("feed")),
            "health": us_press.health()[0], "provenance": PROVENANCE}


@router.get("/{ticker}", openapi_extra={"x-example": "/api/v1/us/press/NVDA"})
def releases(ticker: str, limit: int = Query(20, ge=1, le=200)):
    """A company's latest press releases and the capital-return figures read from them."""
    c = us_press.company(ticker, limit)
    if c is None:
        raise HTTPException(404, "%s is not an S&P 500 member Plover watches for press releases." % ticker.upper())
    c["provenance"] = PROVENANCE
    return c


MANIFEST = {
    "id": "us-press",
    "section": "us-reference",
    "name": {"en": "US press releases (S&P 500)", "ja": "米国企業のプレスリリース（S&P 500）"},
    "shape": "company",
    "summary": ("The latest press releases of S&P 500 members, from each company's own news feed and "
                "the releases it files as 8-K exhibits, with the capital-return figures (buyback "
                "authorisations, dividends) read from the release text, each shown beside its sentence."),
    "source": {
        "publisher": "Each company (news feeds); U.S. Securities and Exchange Commission (8-K exhibits)",
        "document": "Company newsroom feeds, and press releases filed as exhibits to Form 8-K",
        "url": "https://www.sec.gov/edgar/search/",
        "credit": us_press.CREDIT,
        "license_note": "Headlines and links as each company publishes them; the releases stay the companies' own.",
    },
    "keys": ["ticker", "published"],
    "frequency": "per-event",
    "vintage": {"unit": "filing", "as_of_basis": "filed_date", "as_of_supported": False,
                "history_from": "the newest releases each feed carries, polled daily",
                "stale_after_days": us_press.STALE_AFTER_DAYS},
    "measures": [
        {"id": "headline", "label": "Headline, as published", "unit": "text", "trust": "official"},
        {"id": "capital_return", "label": "Capital-return figure read from the release", "unit": "USD",
         "trust": "derived",
         "calc": ("read from the release text by pattern, never estimated; each figure carries the "
                  "sentence it came from")},
    ],
    "endpoints": {
        "company": "/api/v1/us/press/{ticker}",
        "coverage": "/api/v1/us/press",
    },
    "capabilities": ["company"],
    "cite": "/us-company.html?t={ticker}",
    "page": "/us-company.html",
    "notes": [
        "A release is the company's own announcement, not a filing: an approval to buy back shares is "
        "not shares bought.",
        "Members only: a company outside the S&P 500, or one whose newsroom blocks automated reading, "
        "has no releases here.",
    ],
}
