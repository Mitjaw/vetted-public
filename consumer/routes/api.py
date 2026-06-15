"""
JSON API endpoints consumed by the consumer frontend via fetch().
All are auth-gated — no public access.
"""
import csv
import io
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, RedirectResponse
from sqlalchemy.orm import Session

from consumer.auth import get_current_user
from consumer.database import get_consumer_db
from consumer.limiter import limiter
import consumer.queries as q

router = APIRouter(prefix="/api")


def _history(user) -> int:
    """Extract history_days from user's subscription (0 = all-time)."""
    if user.subscription:
        return user.subscription.history_days
    return 30  # default: starter


def _gate(request: Request, db: Session):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return None, user
    return user, None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/search")
@limiter.limit("60/minute")
def search(request: Request, q_param: str = "", db: Session = Depends(get_consumer_db)):
    user, redir = _gate(request, db)
    if redir:
        return JSONResponse({"results": []}, status_code=401)
    results = q.search(q_param)
    return JSONResponse({"results": results})


# ---------------------------------------------------------------------------
# Export count preview
# ---------------------------------------------------------------------------

@router.get("/export/count")
@limiter.limit("30/minute")
def export_count(
    request: Request,
    ticker: str = "",
    channel_id: str = "",
    date_from: str = "",
    date_to: str = "",
    sentiment: str = "all",
    asset_type: str = "all",
    min_confidence: str = "",
    db: Session = Depends(get_consumer_db),
):
    user, redir = _gate(request, db)
    if redir:
        return JSONResponse({"count": 0}, status_code=401)

    filters = {
        "ticker":         ticker or None,
        "channel_id":     channel_id or None,
        "date_from":      date_from or None,
        "date_to":        date_to or None,
        "sentiment":      sentiment,
        "asset_type":     asset_type,
        "min_confidence": min_confidence or None,
        "history_days":   _history(user),
    }
    count = q.get_export_count(filters)
    return JSONResponse({"count": count})


# ---------------------------------------------------------------------------
# Export download
# ---------------------------------------------------------------------------

@router.get("/export/download")
@limiter.limit("5/minute")
def export_download(
    request: Request,
    fmt: str = "csv",
    ticker: str = "",
    channel_id: str = "",
    date_from: str = "",
    date_to: str = "",
    sentiment: str = "all",
    asset_type: str = "all",
    min_confidence: str = "",
    db: Session = Depends(get_consumer_db),
):
    user, redir = _gate(request, db)
    if redir:
        return redir

    filters = {
        "ticker":         ticker or None,
        "channel_id":     channel_id or None,
        "date_from":      date_from or None,
        "date_to":        date_to or None,
        "sentiment":      sentiment,
        "asset_type":     asset_type,
        "min_confidence": min_confidence or None,
        "history_days":   _history(user),
    }
    rows = q.get_export_rows(filters)

    if fmt == "json":
        content = json.dumps(rows, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=vetted_export.json"},
        )

    # CSV
    if not rows:
        return Response(
            content="",
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=vetted_export.csv"},
        )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vetted_export.csv"},
    )
