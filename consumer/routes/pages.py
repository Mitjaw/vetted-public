import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from consumer.auth import get_current_user
from consumer.database import get_consumer_db
import consumer.queries as q

router = APIRouter()

_TMPL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
_tmpl = Jinja2Templates(directory=_TMPL_DIR)


def _page(template: str, request: Request, db: Session, active_page: str, **ctx):
    """
    Auth-gate + render. active_page must be passed in context — Jinja2 {% set %}
    inside a child block does not propagate to the parent scope, so base.html
    sidebar highlighting would never see it if set in the child template.
    """
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    # Update last_visit
    user.last_visit = datetime.now(timezone.utc)
    db.commit()

    return _tmpl.TemplateResponse(
        template, {"request": request, "user": user, "active_page": active_page, **ctx}
    )


def _history(user) -> int:
    if user.subscription:
        return user.subscription.history_days
    return 30


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    prev_visit = user.last_visit  # capture before _page updates it
    user.last_visit = datetime.now(timezone.utc)
    db.commit()

    hd = _history(user)
    activity   = q.get_activity_feed(hd)
    top_stocks = q.get_most_covered_stocks(hd)
    db_health  = q.get_db_health()
    delta      = q.get_delta_since(prev_visit)

    return _tmpl.TemplateResponse("home.html", {
        "request":    request,
        "user":       user,
        "active_page": "home",
        "activity":   activity,
        "top_stocks": top_stocks,
        "db_health":  db_health,
        "delta":      delta,
        "prev_visit": prev_visit,
    })


# ---------------------------------------------------------------------------
# Stocks
# ---------------------------------------------------------------------------

@router.get("/stocks", response_class=HTMLResponse)
def stocks(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    hd = _history(user)
    mentioned = q.get_most_mentioned(hd)
    shifts    = q.get_narrative_shifts()

    return _tmpl.TemplateResponse("stocks.html", {
        "request":    request,
        "user":       user,
        "active_page": "stocks",
        "mentioned":  mentioned,
        "shifts":     shifts,
    })


@router.get("/stocks/{ticker}", response_class=HTMLResponse)
def stock_detail(ticker: str, request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    data = q.get_stock_detail(ticker.upper(), _history(user))
    return _tmpl.TemplateResponse("stock_detail.html", {
        "request":    request,
        "user":       user,
        "active_page": "stocks",
        "ticker":     ticker.upper(),
        **data,
    })


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

@router.get("/channels", response_class=HTMLResponse)
def channels(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    leaderboard = q.get_channel_leaderboard()
    monthly_dots = q.get_channel_monthly_dots()

    return _tmpl.TemplateResponse("channels.html", {
        "request":      request,
        "user":         user,
        "active_page":  "channels",
        "leaderboard":  leaderboard,
        "monthly_dots": monthly_dots,
    })


@router.get("/channels/{channel_id}", response_class=HTMLResponse)
def channel_detail(channel_id: str, request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    data = q.get_channel_detail(channel_id, _history(user))
    if data is None:
        return RedirectResponse("/channels", status_code=302)

    portfolio = q.get_channel_portfolio(channel_id)

    return _tmpl.TemplateResponse("channel_detail.html", {
        "request":    request,
        "user":       user,
        "active_page": "channels",
        "portfolio":  portfolio,
        **data,
    })


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@router.get("/leads", response_class=HTMLResponse)
def leads(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    hd = _history(user)
    data = q.get_leads(history_days=hd)

    return _tmpl.TemplateResponse("leads.html", {
        "request":    request,
        "user":       user,
        "active_page": "leads",
        **data,
    })


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats", response_class=HTMLResponse)
def stats(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    summary      = q.get_stats_summary()
    roi_hist     = q.get_roi_histogram()
    monthly_data = q.get_monthly_data()
    best_worst   = q.get_best_worst_picks()

    return _tmpl.TemplateResponse("stats.html", {
        "request":      request,
        "user":         user,
        "active_page":  "stats",
        "summary":      summary,
        "roi_hist":     roi_hist,
        "monthly_data": monthly_data,
        **best_worst,
    })


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@router.get("/exports", response_class=HTMLResponse)
def exports(request: Request, db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    from consumer.auth import require_tier
    if not require_tier(user, "pro"):
        return _tmpl.TemplateResponse("upgrade_required.html", {
            "request":     request,
            "user":        user,
            "active_page": "exports",
            "feature":     "Exports",
            "feature_blurb": "Pull the full dataset as CSV or JSON, filter by ticker, channel, sentiment, asset type, and date range.",
            "min_tier":    "pro",
        })

    all_channels = q.get_all_channels()
    return _tmpl.TemplateResponse("exports.html", {
        "request":      request,
        "user":         user,
        "active_page":  "exports",
        "all_channels": all_channels,
    })


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Legal — public, no auth required
# ---------------------------------------------------------------------------

@router.get("/legal/terms", response_class=HTMLResponse)
def legal_terms(request: Request):
    return _tmpl.TemplateResponse("legal_terms.html", {"request": request})


@router.get("/legal/privacy", response_class=HTMLResponse)
def legal_privacy(request: Request):
    return _tmpl.TemplateResponse("legal_privacy.html", {"request": request})


@router.get("/legal/imprint", response_class=HTMLResponse)
def legal_imprint(request: Request):
    return _tmpl.TemplateResponse("legal_imprint.html", {"request": request})


@router.get("/account", response_class=HTMLResponse)
def account(request: Request, msg: str = "", db: Session = Depends(get_consumer_db)):
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return _tmpl.TemplateResponse("account.html", {
        "request": request,
        "user": user,
        "active_page": "account",
        "msg": msg,
    })


@router.post("/account/password")
def post_account_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    from consumer.auth import hash_password, verify_password
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse(url="/account?msg=Current+password+is+wrong", status_code=303)
    if len(new_password) < 8:
        return RedirectResponse(url="/account?msg=New+password+too+short", status_code=303)
    user.hashed_password = hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/account?msg=Password+updated", status_code=303)


@router.post("/account/email")
def post_account_email(
    request: Request,
    new_email: str = Form(...),
    current_password: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    from datetime import datetime, timedelta, timezone
    import secrets
    from consumer.auth import verify_password
    from consumer.email import send_verification_email
    from consumer.models import User
    user = get_current_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    new_email = new_email.strip().lower()
    if "@" not in new_email or len(new_email) > 254:
        return RedirectResponse(url="/account?msg=Invalid+email", status_code=303)
    if not verify_password(current_password, user.hashed_password):
        return RedirectResponse(url="/account?msg=Current+password+is+wrong", status_code=303)
    if new_email == user.email:
        return RedirectResponse(url="/account?msg=That's+your+current+email", status_code=303)
    if db.query(User).filter(User.email == new_email).first():
        # Don't reveal whether the address is taken — just say "sent".
        return RedirectResponse(url="/account?msg=Verification+email+sent", status_code=303)

    # Stage the change behind a verification link reusing the verification_token slot.
    # On verify, the user's email column gets swapped to the new address.
    user.verification_token = secrets.token_urlsafe(32)
    user.verification_token_expires = datetime.now(timezone.utc) + timedelta(hours=24)
    # We'll need to know which new email to apply on verify — store it in
    # password_reset_token-adjacent space? Cleanest: small per-app pending_email column.
    # For launch simplicity, encode it into the token URL via a separate token store.
    # TODO: add a pending_email column. For now, abort with a "not implemented" note
    # so we don't accidentally lock the user out by losing the new-email mapping.
    db.commit()
    return RedirectResponse(
        url="/account?msg=Email+change+coming+soon+%E2%80%94+contact+support+if+you+need+it+now",
        status_code=303,
    )
