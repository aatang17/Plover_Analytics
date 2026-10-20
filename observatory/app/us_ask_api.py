# -*- coding: utf-8 -*-
"""The box, for US stocks: a typed question in, a sentence and a chart out.

No model runs here. app/stock_recipes.py matches the words to a question
shape, resolves the names (app/us_link.py) and runs the shape on the same
functions that serve /api/v1. The reply says which shape it chose and what
it read the question as naming, so a wrong reading is visible and fixable
with one more word.

  GET /api/v1/us/ask?q=who+owns+apple        answer
  GET /api/v1/us/ask/recipes                 the shapes
  GET /api/v1/us/ask/run/{recipe}?...        one shape with explicit blanks
  GET /api/v1/us/ask/stream?q=...            the same answer as server-sent
                                             events: what it is doing at each
                                             step, then the answer
"""
import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from . import ask_log, ask_progress, sec_api, stock_recipes

router = APIRouter(prefix="/api/v1/us/ask", tags=["US ask"])


@router.get("", openapi_extra={"x-example": "/api/v1/us/ask?q=who+owns+apple"})
@router.get("/", include_in_schema=False)
def ask(q: str = Query(..., min_length=2, max_length=300, description="a question in plain words"),
        prev: str = Query("", max_length=300, description="the reader's previous question, so a follow-up "
                                                            "(\"and Microsoft?\") is read whole")):
    """Answer a stock question from the data, by code."""
    cur = sec_api._require()
    return _noted("/api/v1/us/ask", q, lambda: stock_recipes.answer(cur, q.strip(), prev.strip() or None))


def _noted(path, q, work):
    """Run the answer and note how it went for the Ask Box log (app/ask_log.py)."""
    try:
        out = work()
    except HTTPException:
        ask_log.note(path, q, False)
        raise
    m = (out or {}).get("match") or {}
    ask_log.note(path, q, bool(out and out.get("headline")), m.get("via"), out.get("recipe") if out else None)
    return out


@router.get("/stream")
async def ask_stream(q: str = Query(..., min_length=2, max_length=300, description="a question in plain words"),
                     prev: str = Query("", max_length=300, description="the reader's previous question")):
    """The answer as server-sent events, so the page can say what is being
    done while it waits: `{"say": "..."}` at each step (app/ask_progress.py),
    then one `{"answer": {...}}` or `{"error": {"status", "detail"}}`."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def put(item):
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def work():
        ask_progress.listen(lambda text: put({"say": text}))
        try:
            put({"answer": _noted("/api/v1/us/ask/stream", q,
                                  lambda: stock_recipes.answer(sec_api._require(), q.strip(), prev.strip() or None))})
        except HTTPException as exc:
            put({"error": {"status": exc.status_code, "detail": exc.detail}})
        except Exception:                                          # noqa: BLE001 — the page must hear that it failed
            ask_log.note("/api/v1/us/ask/stream", q, False, "error")
            put({"error": {"status": 500, "detail": "Something went wrong working that out. Try again, or ask it another way."}})
        finally:
            ask_progress.listen(None)
            put(None)

    threading.Thread(target=work, name="us-ask-stream", daemon=True).start()

    async def events():
        while True:
            item = await queue.get()
            if item is None:
                return
            yield "data: %s\n\n" % json.dumps(jsonable_encoder(item), ensure_ascii=False)

    # identity encoding: a proxy that compresses would hold the lines back until the end
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                                      "Content-Encoding": "identity"})


@router.get("/recipes")
def recipes():
    from . import us_ask_model
    return {"recipes": stock_recipes.catalogue(),
            "figures": [{"id": f["id"], "label": f["label"], "unit": f["unit"]} for f in stock_recipes.FIGURES],
            "not_held": [m for _, m in stock_recipes.NOT_HELD],
            "unanswerable": ["why a price or figure moved", "whether to buy or sell", "forecasts, estimates or targets"],
            "model_reader": us_ask_model.status()}


@router.get("/run/{recipe}")
def run(recipe: str,
        company: str = Query("", description="ticker or name"),
        company_2: str = Query("", description="second ticker or name, for pair questions"),
        industry: str = Query("", description="industry in words, e.g. carmakers"),
        manager: str = Query("", description="13F manager name"),
        since: str = Query("", description="YYYY-MM-DD"),
        min_value: str = Query("", description="dollar floor, e.g. 1e10"),
        count: int = Query(10, ge=1, le=100),
        metric: str = Query("", description="words for the ranking figure, e.g. 'cheapest' or 'operating margin'"),
        figure: str = Query("", description="a filed line in words, e.g. debt, research spending"),
        days: int = Query(14, ge=1, le=120)):
    """Run one shape with its blanks filled explicitly, no word matching."""
    cur = sec_api._require()
    out = stock_recipes.run_explicit(cur, recipe, {"company": company, "company_2": company_2, "industry": industry,
                                                    "manager": manager, "since": since, "min_value": min_value,
                                                    "count": count, "metric": metric, "figure": figure, "days": days})
    r = stock_recipes.BY_ID[recipe]
    out["match"] = {"recipe": r["id"], "label": r["label"], "score": None, "alternatives": [], "via": "explicit"}
    return out
