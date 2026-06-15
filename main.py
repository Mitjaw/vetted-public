import os
import logging
import re
import secrets
import csv

import logging_config
logging_config.configure()

logger = logging.getLogger(__name__)
import json
import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

import db_manager
import scanner
import channel_fetcher
import scheduler as vetted_scheduler

load_dotenv()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

security = HTTPBasic()


def verify(credentials: HTTPBasicCredentials = Depends(security)):
    correct = secrets.compare_digest(
        credentials.password.encode(),
        os.getenv("DASHBOARD_PASSWORD", "").encode(),
    )
    if not correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db_manager.upsert_stats_snapshot()
    except Exception:
        logger.exception("startup stats snapshot failed")

    if os.getenv("VETTED_SCHEDULER_ENABLED", "1") == "1":
        try:
            vetted_scheduler.start()
        except Exception:
            logger.exception("scheduler failed to start")
    else:
        logger.info("scheduler disabled via VETTED_SCHEDULER_ENABLED=0")

    yield

    try:
        vetted_scheduler.stop()
    except Exception:
        logger.exception("scheduler shutdown failed")


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# Pipeline status — shared state for background pipeline runs
# ---------------------------------------------------------------------------

_pipeline_status = {
    "running": False,
    "started_at": None,
    "completed_at": None,
    "steps": {
        "backfill":    {"state": "pending", "detail": ""},
        "reanalyze":   {"state": "pending", "detail": ""},
        "roi":         {"state": "pending", "detail": ""},
        "milestones":  {"state": "pending", "detail": ""},
        "ticker_meta": {"state": "pending", "detail": ""},
    },
    "error": None,
}
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# GET /health — unauth'd liveness + readiness signal for Caddy / monitoring
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    db_ok = True
    db_error = None
    try:
        db_manager.get_home_stats()
    except Exception as exc:
        db_ok = False
        db_error = str(exc)[:200]

    sched = vetted_scheduler.get_status()
    status_str = "ok" if db_ok and sched.get("running") else "degraded"
    return JSONResponse({
        "status": status_str,
        "db_connected": db_ok,
        "db_error": db_error,
        "scheduler": sched,
        "pipeline_running": _pipeline_status.get("running", False),
    })


# ---------------------------------------------------------------------------
# Scheduler dashboard — owner-controlled cron jobs
# ---------------------------------------------------------------------------

@app.get("/admin/scheduler")
def scheduler_page(
    request: Request,
    msg: str = "",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    return templates.TemplateResponse(
        "scheduler.html",
        {"request": request, "scheduler": vetted_scheduler.get_status(), "msg": msg},
    )


@app.post("/admin/scheduler/job/{job_id}/pause")
def scheduler_pause_job(job_id: str, credentials: HTTPBasicCredentials = Depends(verify)):
    ok = vetted_scheduler.pause(job_id)
    msg = f"Paused {job_id}" if ok else f"Could not pause {job_id}"
    return RedirectResponse(url=f"/admin/scheduler?msg={msg}", status_code=303)


@app.post("/admin/scheduler/job/{job_id}/resume")
def scheduler_resume_job(job_id: str, credentials: HTTPBasicCredentials = Depends(verify)):
    ok = vetted_scheduler.resume(job_id)
    msg = f"Resumed {job_id}" if ok else f"Could not resume {job_id}"
    return RedirectResponse(url=f"/admin/scheduler?msg={msg}", status_code=303)


@app.post("/admin/scheduler/job/{job_id}/run-now")
def scheduler_run_now(job_id: str, credentials: HTTPBasicCredentials = Depends(verify)):
    ok = vetted_scheduler.trigger_now(job_id)
    msg = f"Started {job_id} on a background thread" if ok else f"Unknown job {job_id}"
    return RedirectResponse(url=f"/admin/scheduler?msg={msg}", status_code=303)


@app.post("/admin/scheduler/pause-all")
def scheduler_pause_all(credentials: HTTPBasicCredentials = Depends(verify)):
    n = vetted_scheduler.pause_all()
    return RedirectResponse(url=f"/admin/scheduler?msg=Paused+{n}+job(s)", status_code=303)


@app.post("/admin/scheduler/resume-all")
def scheduler_resume_all(credentials: HTTPBasicCredentials = Depends(verify)):
    n = vetted_scheduler.resume_all()
    return RedirectResponse(url=f"/admin/scheduler?msg=Resumed+{n}+job(s)", status_code=303)


def _extract_video_id(raw: str) -> str:
    """Extract the 11-char YouTube video ID from a URL or return the input as-is."""
    raw = raw.strip()
    # youtu.be/ID or youtu.be/ID?...
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", raw)
    if m:
        return m.group(1)
    # youtube.com/watch?v=ID or /embed/ID or /shorts/ID
    m = re.search(r"(?:v=|/embed/|/shorts/|/v/)([A-Za-z0-9_-]{11})", raw)
    if m:
        return m.group(1)
    return raw


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@app.get("/")
def home(
    request: Request,
    days: int = 30,
    min_channels: int = 3,
    lang: str = "all",
    sentiment: str = "all",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    consensus = []
    leaderboard = []
    recent_scans = []
    channels_total = 0
    _error_msg = None

    try:
        consensus = db_manager.get_consensus_picks(days, min_channels, lang, sentiment)
        leaderboard = db_manager.get_channel_leaderboard(days)
        stats = db_manager.get_home_stats()
        recent_scans = stats["recent_scans"]
        channels_total = stats["channels_total"]
    except Exception:
        logger.exception("home route failed")
        _error_msg = "Data temporarily unavailable — check server logs."

    # Market mood derived from consensus data
    total_bull = sum(r["bullish_count"] for r in consensus)
    total_bear = sum(r["bearish_count"] for r in consensus)
    total_neut = sum(r["neutral_count"] for r in consensus)
    total_all = total_bull + total_bear + total_neut
    mood_pct = round(total_bull / total_all * 100, 1) if total_all > 0 else 0
    mood_label = "Bullish" if mood_pct >= 55 else ("Bearish" if mood_pct <= 35 else "Mixed")

    # Best confirmed 30d ROI pick from consensus
    with_roi = [r for r in consensus if r.get("avg_roi_30d") is not None]
    best_roi = max(with_roi, key=lambda r: r["avg_roi_30d"], default=None)

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "consensus": consensus,
            "leaderboard": leaderboard,
            "recent_scans": recent_scans,
            "channels_total": channels_total,
            "days": days,
            "min_channels": min_channels,
            "lang": lang,
            "sentiment": sentiment,
            "mood_pct": mood_pct,
            "mood_label": mood_label,
            "mood_bull": total_bull,
            "mood_bear": total_bear,
            "mood_neut": total_neut,
            "best_roi": best_roi,
            "msg": _error_msg,
        },
    )


@app.get("/api/leaderboard")
def api_leaderboard(days: int = 30, credentials: HTTPBasicCredentials = Depends(verify)):
    from fastapi.responses import JSONResponse
    return JSONResponse(db_manager.get_channel_leaderboard(days))


# ---------------------------------------------------------------------------
# GET /stock/{ticker}
# ---------------------------------------------------------------------------

@app.get("/stock/{ticker}")
def stock(ticker: str, request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        data = db_manager.get_stock_detail(ticker, days)
    except Exception:
        logger.exception("get_stock_detail failed ticker=%s days=%s", ticker, days)
        return templates.TemplateResponse("stock.html", {
            "request": request,
            "ticker": ticker.upper(),
            "mentions": [],
            "sentiment_summary": {"bullish": 0, "bearish": 0, "neutral": 0},
            "roi_data": [],
            "days": days,
            "msg": "Data temporarily unavailable — check server logs.",
        })
    return templates.TemplateResponse(
        "stock.html",
        {
            "request": request,
            "ticker": ticker.upper(),
            "mentions": data["mentions"],
            "sentiment_summary": data["sentiment_summary"],
            "roi_data": data["roi_data"],
            "days": days,
        },
    )


# ---------------------------------------------------------------------------
# GET /channel/{channel_id}
# ---------------------------------------------------------------------------

@app.get("/channel/{channel_id}")
def channel(channel_id: str, request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        data = db_manager.get_channel_detail(channel_id)
    except Exception:
        logger.exception("get_channel_detail failed channel_id=%s", channel_id)
        return templates.TemplateResponse("channel.html", {
            "request": request,
            "channel": None,
            "videos": [],
            "pick_accuracy": None,
            "top_stocks": [],
            "channel_mentions": [],
            "msg": "Data temporarily unavailable — check server logs.",
        })
    return templates.TemplateResponse(
        "channel.html",
        {
            "request": request,
            "channel": data["channel"],
            "videos": data["videos"],
            "pick_accuracy": data["pick_accuracy"],
            "top_stocks": data["top_stocks"],
            "channel_mentions": data["channel_mentions"],
        },
    )


# ---------------------------------------------------------------------------
# GET /channels
# ---------------------------------------------------------------------------

@app.get("/channels")
def channels(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        channels_list = db_manager.get_channels_list()
    except Exception:
        logger.exception("get_channels_list failed")
        channels_list = []
    return templates.TemplateResponse(
        "channels.html",
        {"request": request, "channels": channels_list,
         **({"msg": "Data temporarily unavailable — check server logs."} if not channels_list else {})},
    )


# ---------------------------------------------------------------------------
# GET /stocks
# ---------------------------------------------------------------------------

@app.get("/stocks")
def stocks(request: Request, days: int = 90, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        stocks_list = db_manager.get_stocks_list(days)
    except Exception:
        logger.exception("get_stocks_list failed days=%s", days)
        stocks_list = []
    return templates.TemplateResponse(
        "stocks.html",
        {"request": request, "stocks": stocks_list, "days": days,
         **({"msg": "Data temporarily unavailable — check server logs."} if not stocks_list else {})},
    )


# ---------------------------------------------------------------------------
# GET /api/stock/{ticker}/chart-data
# ---------------------------------------------------------------------------

@app.get("/api/channel/{channel_id}/stock/{ticker}/chart-data")
def channel_stock_chart_data(channel_id: str, ticker: str, credentials: HTTPBasicCredentials = Depends(verify)):
    from fastapi.responses import JSONResponse
    data = db_manager.get_channel_stock_chart_data(channel_id, ticker)
    return JSONResponse({
        "labels": [row["week_start"] for row in data],
        "total": [row["total"] for row in data],
        "distinct": [row["distinct_videos"] for row in data],
        "bullish": [row["bullish"] for row in data],
        "bearish": [row["bearish"] for row in data],
        "neutral": [row["neutral"] for row in data],
    })


@app.get("/api/stock/{ticker}/chart-data")
def stock_chart_data(ticker: str, days: int = 365, credentials: HTTPBasicCredentials = Depends(verify)):
    from fastapi.responses import JSONResponse
    data = db_manager.get_mention_chart_data(ticker, days if days > 0 else None)
    return JSONResponse({
        "labels": [row["week_start"] for row in data],
        "total": [row["total"] for row in data],
        "distinct": [row["distinct_videos"] for row in data],
        "bullish": [row["bullish"] for row in data],
        "bearish": [row["bearish"] for row in data],
        "neutral": [row["neutral"] for row in data],
    })


# ---------------------------------------------------------------------------
# GET /export
# ---------------------------------------------------------------------------

@app.get("/export")
def export_page(request: Request, credentials: HTTPBasicCredentials = Depends(verify)):
    try:
        channels_list = db_manager.get_channels_list()
    except Exception:
        logger.exception("export_page get_channels_list failed")
        channels_list = []
    try:
        table_meta = db_manager.get_export_table_metadata()
    except Exception:
        logger.exception("export_page get_export_table_metadata failed")
        table_meta = {}
    tables = list(db_manager._EXPORTABLE_TABLES.keys())
    return templates.TemplateResponse(
        "export.html",
        {"request": request, "channels": channels_list, "tables": tables, "table_info": table_meta},
    )


# ---------------------------------------------------------------------------
# GET /analyst  &  GET /analyst/export
# ---------------------------------------------------------------------------

@app.get("/analyst")
def analyst(
    request: Request,
    table_mode: str = "ticker",
    chart_mode: str = "timeline",
    date_from: str = "",
    date_to: str = "",
    channels_filter: str = "",
    lang_filter: str = "",
    sentiment: str = "all",
    recommendation: str = "all",
    min_confidence: int = 0,
    tickers_filter: str = "",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    filters = {
        "date_from": date_from or None,
        "date_to": date_to or None,
        "channels": [c.strip() for c in channels_filter.split(",") if c.strip()],
        "lang": [l.strip() for l in lang_filter.split(",") if l.strip()],
        "sentiment": sentiment,
        "recommendation": recommendation,
        "min_confidence": min_confidence,
        "tickers": [t.strip().upper() for t in tickers_filter.split(",") if t.strip()],
    }

    _ctx = {
        "request": request,
        "table_mode": table_mode,
        "chart_mode": chart_mode,
        "date_from": date_from,
        "date_to": date_to,
        "channels_filter": channels_filter,
        "lang_filter": lang_filter,
        "sentiment": sentiment,
        "recommendation": recommendation,
        "min_confidence": min_confidence,
        "tickers_filter": tickers_filter,
    }

    try:
        table_data = db_manager.get_analyst_data(table_mode, filters)
        chart_data = db_manager.get_analyst_chart_data(chart_mode, filters)
        all_channels = db_manager.get_channels_list()
    except Exception:
        logger.exception("analyst route failed")
        return templates.TemplateResponse("analyst.html", {
            **_ctx,
            "table_data": [],
            "chart_data": {},
            "all_channels": [],
            "row_count": 0,
            "msg": "Data temporarily unavailable — check server logs.",
        })

    return templates.TemplateResponse("analyst.html", {
        **_ctx,
        "table_data": table_data,
        "chart_data": chart_data,
        "all_channels": all_channels,
        "row_count": len(table_data),
    })


@app.get("/analyst/export")
def analyst_export(
    request: Request,
    table_mode: str = "raw",
    date_from: str = "",
    date_to: str = "",
    channels_filter: str = "",
    lang_filter: str = "",
    sentiment: str = "all",
    recommendation: str = "all",
    min_confidence: int = 0,
    tickers_filter: str = "",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    filters = {
        "date_from": date_from or None,
        "date_to": date_to or None,
        "channels": [c.strip() for c in channels_filter.split(",") if c.strip()],
        "lang": [l.strip() for l in lang_filter.split(",") if l.strip()],
        "sentiment": sentiment,
        "recommendation": recommendation,
        "min_confidence": min_confidence,
        "tickers": [t.strip().upper() for t in tickers_filter.split(",") if t.strip()],
    }
    data = db_manager.get_analyst_data(table_mode, filters)
    if not data:
        return RedirectResponse(url="/analyst", status_code=303)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)
    fname = f"analyst_{table_mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


CSV_COLUMNS = [
    "date", "channel", "video_title", "ticker", "company_name",
    "sentiment", "confidence", "recommendation", "context",
    "price_at_publish", "roi_7d", "roi_30d",
]


# ---------------------------------------------------------------------------
# GET /export/csv
# ---------------------------------------------------------------------------

@app.get("/export/csv")
def export_csv(
    request: Request,
    ticker: str = None,
    channel_id: str = None,
    date_from: str = None,
    date_to: str = None,
    sentiment: str = None,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    try:
        rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
    except Exception:
        logger.exception("export_csv get_export_rows failed")
        rows = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([row.get(col) for col in CSV_COLUMNS])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vetted_export.csv"},
    )


# ---------------------------------------------------------------------------
# GET /export/json
# ---------------------------------------------------------------------------

@app.get("/export/json")
def export_json(
    request: Request,
    ticker: str = None,
    channel_id: str = None,
    date_from: str = None,
    date_to: str = None,
    sentiment: str = None,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    try:
        rows = db_manager.get_export_rows(ticker, channel_id, date_from, date_to, sentiment)
    except Exception:
        logger.exception("export_json get_export_rows failed")
        rows = []

    return StreamingResponse(
        iter([json.dumps(rows)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=vetted_export.json"},
    )


# ---------------------------------------------------------------------------
# GET /export/ai-analysis
# ---------------------------------------------------------------------------

@app.get("/export/ai-analysis")
def export_ai_analysis(credentials: HTTPBasicCredentials = Depends(verify)):
    """Flat denormalized CSV for AI correlation analysis."""
    try:
        rows = db_manager.get_ai_analysis_export()
    except Exception:
        logger.exception("export_ai_analysis failed")
        rows = []

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    output.seek(0)
    fname = f"vetted_ai_analysis_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------------------------------------------------------------------
# GET /export/verified-mentions
# ---------------------------------------------------------------------------

@app.get("/export/verified-mentions")
def export_verified_mentions(credentials: HTTPBasicCredentials = Depends(verify)):
    """Flat denormalized CSV — only mentions confirmed present in transcript (verified=1)."""
    try:
        rows = db_manager.get_verified_mentions_export()
    except Exception:
        logger.exception("export_verified_mentions failed")
        rows = []

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    output.seek(0)
    fname = f"vetted_verified_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------------------------------------------------------------------
# GET /export/raw/{table}
# ---------------------------------------------------------------------------

@app.get("/export/raw/{table}")
def export_raw_table(
    table: str,
    fmt: str = Query("csv", alias="format"),
    date_from: str = None,
    date_to: str = None,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Export a raw table as CSV or JSON with optional date range filter."""
    try:
        rows = db_manager.get_raw_table_export(table, date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("export_raw_table failed table=%s", table)
        rows = []

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    fname_base = f"{table}_{ts}"

    if fmt == "json":
        return StreamingResponse(
            iter([json.dumps(rows, default=str)]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={fname_base}.json"},
        )
    else:
        if not rows:
            output = io.StringIO()
            output.seek(0)
            return StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={fname_base}.csv"},
            )
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(data=rows)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={fname_base}.csv"},
        )


# ---------------------------------------------------------------------------
# POST /admin/channels/add
# ---------------------------------------------------------------------------

@app.post("/admin/channels/add")
async def admin_add_channel(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    form = await request.form()
    channel_id = form.get("channel_id", "").strip()
    name = form.get("name", "").strip()
    url = form.get("url", "").strip()
    language = form.get("language", "en").strip()

    if channel_id:
        db_manager.add_channel(channel_id, name, url, language)

    return RedirectResponse(url="/channels", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/scan/{channel_id}
# ---------------------------------------------------------------------------

@app.post("/admin/scan/all")
def admin_scan_all(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    background_tasks.add_task(scanner.scan_all_channels)
    return RedirectResponse(url="/", status_code=303)


@app.post("/admin/backfill/all")
def admin_backfill_all(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    background_tasks.add_task(scanner.backfill_all_channels)
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/admin/reanalyze")
def admin_reanalyze(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
    date_from: str = Form(None),
    date_to: str = Form(None),
    active_only: str = Form(None),
):
    """Re-run extraction on stored transcripts that have zero mentions, with optional filters."""
    background_tasks.add_task(
        scanner.reanalyze_stored_transcripts,
        date_from=date_from or None,
        date_to=date_to or None,
        active_only=bool(active_only),
    )
    return RedirectResponse(url="/channels?msg=Reanalyze+started", status_code=303)


@app.post("/admin/pipeline")
def admin_pipeline(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
    date_from: str = Form(None),
    date_to: str = Form(None),
    channel_ids: list[str] = Form(default=[]),
    skip_backfill: str = Form(None),
    skip_reanalyze: str = Form(None),
    skip_roi: str = Form(None),
    skip_ticker_meta: str = Form(None),
    skip_milestones: str = Form(None),
    active_only: str = Form(None),
):
    if _pipeline_status["running"]:
        return RedirectResponse(url="/channels?msg=Pipeline+already+running", status_code=303)

    _pipeline_status.update({
        "running": True,
        "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at": None,
        "error": None,
        "steps": {
            "backfill":    {"state": "pending", "detail": ""},
            "reanalyze":   {"state": "pending", "detail": ""},
            "roi":         {"state": "pending", "detail": ""},
            "milestones":  {"state": "pending", "detail": ""},
            "ticker_meta": {"state": "pending", "detail": ""},
        },
    })

    background_tasks.add_task(
        scanner.run_full_pipeline,
        date_from=date_from or None,
        date_to=date_to or None,
        channel_ids=channel_ids or None,
        skip_backfill=bool(skip_backfill),
        skip_reanalyze=bool(skip_reanalyze),
        skip_roi=bool(skip_roi),
        skip_ticker_meta=bool(skip_ticker_meta),
        skip_milestones=bool(skip_milestones),
        active_only=bool(active_only),
        status=_pipeline_status,
    )
    return RedirectResponse(url="/channels?msg=Pipeline+started", status_code=303)


@app.get("/admin/pipeline/status")
def pipeline_status(credentials: HTTPBasicCredentials = Depends(verify)):
    return _pipeline_status


@app.post("/admin/update-rois")
def admin_update_rois(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Sync prices, fetch missing baseline prices, backfill 7d/30d, update milestones."""
    def _run_all():
        scanner.sync_price_daily()
        scanner.fetch_roi_baselines()
        scanner.update_rois()
        scanner.update_roi_milestones()
    background_tasks.add_task(_run_all)
    return RedirectResponse(url="/channels?msg=ROI+update+started", status_code=303)


@app.post("/admin/ticker-meta/refresh")
def admin_ticker_meta_refresh(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Fetch sector/industry/market-cap metadata for new or stale tickers."""
    background_tasks.add_task(scanner.fetch_ticker_metadata)
    return RedirectResponse(url="/channels?msg=Ticker+metadata+refresh+started", status_code=303)


@app.post("/admin/milestones/backfill")
def admin_milestones_backfill(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """
    Two-step backfill: (1) migrate existing 7d/30d from roi_tracking into roi_milestones,
    then (2) compute all new/missing milestone rows.
    """
    def _run():
        n = db_manager.backfill_roi_milestones_from_tracking()
        logger.info("Milestone backfill from roi_tracking: %d rows", n)
        scanner.update_roi_milestones()
    background_tasks.add_task(_run)
    return RedirectResponse(url="/channels?msg=Milestones+backfill+started", status_code=303)


@app.get("/admin/stats")
def admin_stats(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    stats = db_manager.get_admin_stats()
    progression = db_manager.get_stats_progression()
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "stats": stats,
        "progression": progression,
    })


@app.post("/admin/stats/refresh")
def admin_stats_refresh(credentials: HTTPBasicCredentials = Depends(verify)):
    """Trigger a manual stats snapshot update and redirect back to stats page."""
    try:
        db_manager.upsert_stats_snapshot()
    except Exception:
        logger.exception("manual stats refresh failed")
    return RedirectResponse(url="/admin/stats?msg=Stats+refreshed", status_code=303)


# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------

@app.get("/admin/evals")
def admin_evals(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
    msg: str = None,
):
    from evals import store as eval_store
    from evals import custom_store as cs
    from evals.configs import CONFIGS

    templates_list  = eval_store.list_templates()
    results_grouped = eval_store.list_results_grouped()

    hidden = cs.hidden_builtins()
    visible_configs = [c for c in CONFIGS if c["name"] not in hidden]

    defaults = cs.defaults_for_pipeline("three_pass")

    return templates.TemplateResponse("evals.html", {
        "request":              request,
        "templates":            templates_list,
        "configs":              visible_configs,
        "custom_configs":       cs.list_configs(),
        "available_models":     cs.AVAILABLE_MODELS,
        "results_grouped":      results_grouped,
        "layer_results":        eval_store.list_layer_results(),
        "msg":                  msg,
        "custom_store_defaults": defaults,
    })


@app.get("/api/video-info")
def api_video_info(
    video_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Return basic info for a video_id — used by the Add Template form."""
    video_id = _extract_video_id(video_id)
    import sqlite3
    conn = sqlite3.connect(db_manager.DB_NAME)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT v.video_id, v.title, c.language, v.transcript IS NOT NULL as has_transcript
        FROM videos v
        LEFT JOIN channels c ON c.id = v.channel_id
        WHERE v.video_id = ?
        """,
        (video_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found in DB")
    return dict(row)


@app.get("/api/channel/resolve")
def api_channel_resolve(
    input: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Resolve a YouTube channel @handle, URL, or UC ID to a canonical UC... ID. FREE."""
    channel_id = channel_fetcher.resolve_channel_id(input.strip())
    if not channel_id:
        raise HTTPException(status_code=404, detail="Could not resolve channel")
    return {"channel_id": channel_id}


@app.get("/api/channel/discover")
def api_channel_discover(
    channel_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """
    Discover all videos for a channel via YouTube Data API (FREE — no TranscriptAPI credits).
    Returns count of videos that exist in the channel vs those already stored in DB.
    The difference = how many transcript credits a full backfill would cost.
    """
    import sqlite3
    all_videos = channel_fetcher.get_all_videos(channel_id, max_videos=2000)
    if not all_videos:
        return {"total": 0, "in_db": 0, "missing": 0, "estimated_credits": 0}

    # Check which video IDs already have a stored transcript
    conn = sqlite3.connect(db_manager.DB_NAME)
    conn.row_factory = sqlite3.Row
    ids_with_transcript = {
        row["video_id"]
        for row in conn.execute(
            "SELECT video_id FROM videos WHERE transcript IS NOT NULL"
        ).fetchall()
    }
    conn.close()

    total   = len(all_videos)
    in_db   = sum(1 for v in all_videos if v["video_id"] in ids_with_transcript)
    missing = total - in_db

    return {
        "total":             total,
        "in_db":             in_db,
        "missing":           missing,
        "estimated_credits": missing,
    }


@app.get("/api/channel/latest")
def api_channel_latest(
    channel_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """
    Fetch the latest ~15 videos for a channel via TranscriptAPI RSS feed. FREE — no credits.
    Returns the raw video list: [{video_id, title, published_at}].
    """
    videos = channel_fetcher.get_recent_videos(channel_id, hours=24 * 365)  # large window to get all 15
    return {"videos": videos, "count": len(videos)}


@app.get("/api/channel/check-recent")
def api_channel_check_recent(
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """
    Check all tracked channels for new videos in the last 48h via TranscriptAPI RSS. FREE.
    Runs all channel checks in parallel (ThreadPoolExecutor) to keep latency low.
    """
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor, as_completed

    conn = sqlite3.connect(db_manager.DB_NAME)
    conn.row_factory = sqlite3.Row
    channels_rows = conn.execute(
        "SELECT id, name FROM channels WHERE skip_backfill = 0"
    ).fetchall()
    existing_ids = {
        row["video_id"]
        for row in conn.execute("SELECT video_id FROM videos").fetchall()
    }
    conn.close()

    channels_list = [{"id": ch["id"], "name": ch["name"]} for ch in channels_rows]

    def check_one(ch):
        videos   = channel_fetcher.get_recent_videos(ch["id"], hours=48)
        new_vids = [v for v in videos if v["video_id"] not in existing_ids]
        return {"channel_id": ch["id"], "name": ch["name"],
                "new_count": len(new_vids), "videos": new_vids}

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(check_one, ch): ch for ch in channels_list}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    # Sort by channel name for consistent ordering
    results.sort(key=lambda r: r["name"].lower())
    total_new = sum(r["new_count"] for r in results)
    return {"channels": results, "total_new": total_new}


@app.post("/admin/evals/template/add")
async def add_eval_template(
    background_tasks: BackgroundTasks,
    request: Request,
    video_id:         str = Form(...),
    title:            str = Form(""),
    channel:          str = Form(""),
    language:         str = Form("en"),
    notes:            str = Form(""),
    annotations_json: str = Form("[]"),
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import store as eval_store
    import extract
    video_id = _extract_video_id(video_id)

    is_edit = eval_store.template_exists(video_id)

    try:
        annotations = json.loads(annotations_json)
    except Exception:
        annotations = []

    # Fetch transcript if not already in DB — stored in the template so the runner
    # can evaluate this video even if the channel has never been backfilled.
    transcript = None
    import sqlite3
    try:
        conn = sqlite3.connect(db_manager.DB_NAME)
        row  = conn.execute(
            "SELECT transcript FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            transcript = row[0]
    except Exception:
        pass

    if not transcript:
        transcript = extract.get_transcript(video_id)

    eval_store.save_template({
        "video_id":    video_id,
        "title":       title,
        "channel":     channel,
        "language":    language,
        "notes":       notes,
        "annotations": annotations,
        "transcript":  transcript,  # stored so runner works without DB backfill
    })
    action = "updated" if is_edit else "saved"
    msg = f"Template+{action}"
    if not transcript:
        msg = f"Template+{action}+(no+transcript+found+%E2%80%94+fetch+via+backfill+first)"
    return RedirectResponse(url=f"/admin/evals?msg={msg}", status_code=303)


@app.post("/admin/evals/recheck")
def recheck_evals(
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Re-score all result files against current ground truth annotations."""
    from evals import store as eval_store
    stats = eval_store.recheck_all_results()
    msg = f"Recheck+complete+%E2%80%94+{stats['entries_updated']}+entries+updated+across+{stats['files_updated']}+files"
    if stats["skipped"]:
        msg += f"%2C+{stats['skipped']}+skipped+(no+raw+mentions+stored)"
    return RedirectResponse(url=f"/admin/evals?msg={msg}", status_code=303)


@app.post("/admin/evals/recalculate-costs")
def recalculate_eval_costs(
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Recompute cost_usd in all result files using current pricing."""
    from evals import store as eval_store
    stats = eval_store.recalculate_costs()
    msg = (
        f"Cost+recalculation+complete+%E2%80%94+"
        f"{stats['entries_updated']}+entries+updated+across+{stats['files_updated']}+files"
    )
    if stats["skipped"]:
        msg += f"%2C+{stats['skipped']}+skipped+(builtin%2Fmixed-model+or+no+change)"
    return RedirectResponse(url=f"/admin/evals?msg={msg}", status_code=303)


@app.post("/admin/evals/result/delete")
def delete_eval_result(
    filename: str = Form(...),
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import store as eval_store
    eval_store.delete_result(filename)
    return RedirectResponse(url="/admin/evals?msg=Result+deleted", status_code=303)


@app.post("/admin/evals/template/{video_id}/delete")
def delete_eval_template(
    video_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import store as eval_store
    eval_store.delete_template(video_id)
    return RedirectResponse(url="/admin/evals?msg=Template+deleted", status_code=303)


@app.post("/admin/evals/run")
async def run_evals(
    background_tasks: BackgroundTasks,
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import runner as eval_runner
    form_data  = await request.form()
    selected   = form_data.getlist("configs")   or None
    video_ids  = form_data.getlist("video_ids") or None
    try:
        repeat = max(1, min(10, int(form_data.get("repeat", 1))))
    except (TypeError, ValueError):
        repeat = 1
    # Collect per-config version overrides submitted as config_version_{name} fields
    config_versions = {}
    for key, val in form_data.items():
        if key.startswith("config_version_") and val:
            cfg_name = key[len("config_version_"):]
            try:
                config_versions[cfg_name] = int(val)
            except ValueError:
                pass
    for _ in range(repeat):
        background_tasks.add_task(eval_runner.run_as_task, selected, video_ids, config_versions or None)
    msg = f"Eval+run+started+(%C3%97{repeat})" if repeat > 1 else "Eval+run+started"
    return RedirectResponse(url=f"/admin/evals?msg={msg}", status_code=303)


@app.get("/admin/evals/config/defaults")
def evals_config_defaults(
    pipeline: str = "two_pass",
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store as cs
    return {"defaults": cs.defaults_for_pipeline(pipeline), "models": cs.AVAILABLE_MODELS}


@app.get("/admin/evals/config/{name}")
def get_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store as cs
    from evals.configs import CONFIGS
    cfg = cs.get_config(name)
    if cfg:
        return cfg
    builtin = next((c for c in CONFIGS if c["name"] == name), None)
    if not builtin:
        raise HTTPException(status_code=404, detail="Config not found")
    pipeline = "single_pass" if "single" in name else "two_pass"
    defaults = cs.defaults_for_pipeline(pipeline)
    return {
        "name":        name,
        "description": builtin["description"],
        "pipeline":    pipeline,
        "is_builtin":  True,
        **defaults,
    }


@app.post("/admin/evals/custom-config/create")
async def create_eval_config(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    import re as _re
    from evals import custom_store as cs
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    slug = _re.sub(r"[^a-z0-9_]", "_", name.lower())
    if cs.config_exists(slug):
        raise HTTPException(status_code=409, detail=f"Config '{slug}' already exists")
    pipeline = body.get("pipeline", "two_pass")
    if pipeline not in ("two_pass", "single_pass", "three_pass", "graph"):
        raise HTTPException(status_code=400, detail="pipeline must be two_pass, single_pass, three_pass, or graph")
    defaults = cs.defaults_for_pipeline(pipeline)
    if pipeline == "graph":
        cfg = {
            "name":        slug,
            "description": body.get("description", slug),
            "pipeline":    "graph",
            "passes":      body.get("passes", defaults["passes"]),
            "connections": body.get("connections", defaults["connections"]),
        }
    else:
        cfg = {
            "name":        slug,
            "description": body.get("description", slug),
            "pipeline":    pipeline,
            "pass1":       body.get("pass1", defaults["pass1"]),
        }
        if pipeline in ("two_pass", "three_pass"):
            cfg["pass2"] = body.get("pass2", defaults.get("pass2"))
        if pipeline == "three_pass":
            cfg["pass3"] = body.get("pass3", defaults.get("pass3"))
    saved = cs.save_config(cfg)
    return {"name": saved, "ok": True}


@app.post("/admin/evals/custom-config/{name}/delete")
def delete_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import custom_store as cs
    if not cs.delete_config(name):
        raise HTTPException(status_code=404, detail="Config not found")
    return RedirectResponse(url="/admin/evals?msg=Config+deleted", status_code=303)


@app.post("/admin/evals/builtin-config/{name}/delete")
def delete_builtin_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Hide a built-in eval config so it no longer appears in the UI."""
    from evals import custom_store as cs
    from evals.configs import CONFIGS
    if not any(c["name"] == name for c in CONFIGS):
        raise HTTPException(status_code=404, detail="Built-in config not found")
    cs.hide_builtin(name)
    return RedirectResponse(url="/admin/evals?msg=Config+removed", status_code=303)


@app.post("/admin/evals/builtin-config/{name}/fork")
async def fork_builtin_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Fork a built-in config into a custom config (v1 = standard defaults for its pipeline)."""
    from evals import custom_store as cs
    from evals.configs import CONFIGS
    builtin = next((c for c in CONFIGS if c["name"] == name), None)
    if not builtin:
        raise HTTPException(status_code=404, detail="Built-in config not found")
    if cs.config_exists(name):
        cs.hide_builtin(name)
        return {"name": name, "ok": True, "already_existed": True}
    # Infer pipeline from config name
    if "three_pass" in name:
        pipeline = "three_pass"
    elif "single" in name:
        pipeline = "single_pass"
    else:
        pipeline = "two_pass"
    defaults = cs.defaults_for_pipeline(pipeline)
    cfg = {
        "name":        name,
        "description": builtin.get("description", name),
        "pipeline":    pipeline,
        **defaults,
    }
    saved = cs.save_config(cfg)
    cs.hide_builtin(name)
    return {"name": saved, "ok": True, "already_existed": False}


@app.post("/admin/evals/custom-config/{name}/edit")
async def edit_eval_config(
    name: str,
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Save a new version of an existing custom config."""
    from evals import custom_store as cs
    if not cs.config_exists(name):
        raise HTTPException(status_code=404, detail="Config not found")
    body = await request.json()
    cfg = cs.get_config(name)
    pipeline = cfg["pipeline"]
    if "passes" in body:
        # Graph format — also migrate config-level pipeline field
        pass_data = {
            "passes":      body["passes"],
            "connections": body.get("connections", []),
        }
        if pipeline != "graph":
            cfg["pipeline"] = "graph"
            import json as _json
            with open(cs._path(name), "w") as fh:
                _json.dump(cfg, fh, indent=2)
    else:
        pass_data = {"pass1": body["pass1"]}
        if pipeline in ("two_pass", "three_pass"):
            pass_data["pass2"] = body["pass2"]
        if pipeline == "three_pass":
            pass_data["pass3"] = body["pass3"]
    if "description" in body:
        cfg["description"] = body["description"]
        import json as _json
        with open(cs._path(name), "w") as fh:
            _json.dump(cfg, fh, indent=2)
    new_v = cs.add_version(name, pass_data)
    return {"name": name, "version": new_v, "ok": True}


@app.post("/admin/evals/custom-config/{name}/duplicate")
def duplicate_eval_config(
    name: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    import re as _re
    from evals import custom_store as cs
    cfg = cs.get_config(name)
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    base = _re.sub(r"_copy(_\d+)?$", "", cfg["name"])
    new_name = f"{base}_copy"
    i = 2
    while cs.config_exists(new_name):
        new_name = f"{base}_copy_{i}"
        i += 1
    latest = cs.latest_version(name)
    if "passes" in latest:
        new_cfg = {
            "name":        new_name,
            "description": cfg.get("description", new_name),
            "pipeline":    "graph",
            "passes":      latest["passes"],
            "connections": latest.get("connections", []),
        }
    else:
        new_cfg = {
            "name":        new_name,
            "description": cfg.get("description", new_name),
            "pipeline":    cfg["pipeline"],
            **{k: latest[k] for k in ("pass1", "pass2", "pass3") if k in latest},
        }
    cs.save_config(new_cfg)
    return RedirectResponse(url=f"/admin/evals?msg=Config+duplicated+as+{new_name}", status_code=303)


@app.post("/admin/evals/test-single-pass")
async def test_single_pass(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    """Run one pass in isolation against a ground truth template.

    Accepts either:
      - version_data: {passes, connections} sent directly from the editor (preferred)
      - config_name + optional version_num: loads from disk (must be graph format)
    """
    body = await request.json()
    config_name  = body.get("config_name", "")
    version_num  = body.get("version")
    pass_id      = body.get("pass_id", "")
    video_id     = body.get("video_id", "")
    version_data = body.get("version_data")  # sent from editor — current in-memory state

    from evals import executor as ev_executor
    from evals import store as eval_store

    if version_data:
        # Use the state sent directly from the editor — works for unsaved configs too
        version = version_data
    else:
        from evals import custom_store as cs
        cfg = cs.get_config(config_name)
        if not cfg:
            raise HTTPException(status_code=404, detail="Config not found")
        all_versions = sorted(cfg["versions"], key=lambda v: v["version"])
        if version_num is not None:
            version = next((v for v in all_versions if v["version"] == version_num), all_versions[-1])
        else:
            version = all_versions[-1]
        if "passes" not in version:
            raise HTTPException(status_code=400, detail="Only graph-format configs support single-pass test")

    template = eval_store.get_template(video_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    transcript = template.get("transcript", "")
    if not transcript:
        raise HTTPException(status_code=422, detail="No transcript stored in this template")

    title    = template.get("title", "")
    language = template.get("language", "en")
    gt_tickers = [a["ticker"] for a in template.get("annotations", [])]

    try:
        raw, parsed, usage, output_type = ev_executor.execute_single_pass_test(
            version, pass_id, transcript, title, language, gt_tickers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    scores = None
    from evals import scorer
    annotations = template.get("annotations", [])
    if annotations:
        try:
            if output_type == "mentions":
                model_out = parsed.get("mentions", [])
            else:
                # Discovery/stock_list: convert to mention-like dicts for scoring
                model_out = [
                    {"ticker": s.get("ticker", ""), "sentiment": "", "recommendation": ""}
                    for s in parsed.get("stocks", [])
                ]
            scores = scorer.score(annotations, model_out)
            # Convert sets to sorted lists for JSON serialization
            for k in ("found", "missed", "hallucinated"):
                if isinstance(scores.get(k), set):
                    scores[k] = sorted(scores[k])
        except Exception:
            pass

    # Identify which pass was run (for labelling saved results)
    pass_cfg = next((p for p in version.get("passes", []) if p["id"] == pass_id), None)

    result = {
        "raw":         raw,
        "parsed":      parsed,
        "output_type": output_type,
        "usage":       usage,
        "scores":      scores,
        "video_id":    video_id,
        "title":       template.get("title", ""),
        "config_name": config_name,
        "pass_id":     pass_id,
        "pass_label":  pass_cfg.get("label", pass_id) if pass_cfg else pass_id,
        "pass_role":   pass_cfg.get("role", "")        if pass_cfg else "",
        "model":       pass_cfg.get("model", "")       if pass_cfg else "",
    }

    # Auto-save to disk — save_layer_result sets id + timestamp on the dict
    saved = dict(result)
    entry_id = eval_store.save_layer_result(saved)
    result["id"]        = saved.get("id", entry_id)
    result["timestamp"] = saved.get("timestamp", "")

    return result


@app.delete("/admin/evals/layer-result/{entry_id}")
def delete_layer_result(
    entry_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    from evals import store as eval_store
    deleted = eval_store.delete_layer_result(entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Result not found")
    return {"ok": True}


@app.post("/admin/backfill/timeframe")
def admin_backfill_timeframe(
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
    channel_id: str = Form("all"),
    date_from: str = Form(...),
    date_to: str = Form(...),
):
    if channel_id == "all":
        background_tasks.add_task(scanner.backfill_all_channels, date_from=date_from, date_to=date_to)
    else:
        background_tasks.add_task(scanner.backfill_channel, channel_id, date_from=date_from, date_to=date_to)
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/admin/backfill/{channel_id}")
def admin_backfill_channel(
    channel_id: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    background_tasks.add_task(scanner.backfill_channel, channel_id)
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/admin/channels/{channel_id}/toggle-skip-backfill")
def admin_toggle_skip_backfill(
    channel_id: str,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    channels = db_manager.get_channels_list()
    channel = next((c for c in channels if c["id"] == channel_id), None)
    if channel:
        db_manager.set_channel_skip_backfill(channel_id, not bool(channel.get("skip_backfill")))
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/admin/scan/{channel_id}")
def admin_scan_channel(
    channel_id: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPBasicCredentials = Depends(verify),
):
    background_tasks.add_task(scanner.scan_single_channel, channel_id)
    return RedirectResponse(url="/channels", status_code=303)
