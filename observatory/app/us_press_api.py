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
