import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from consumer.auth import hash_password, verify_password
from consumer.database import get_consumer_db
from consumer.email import send_password_reset_email, send_verification_email
from consumer.limiter import limiter
from consumer.models import Subscription, User

router = APIRouter()

_TMPL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
_tmpl = Jinja2Templates(directory=_TMPL_DIR)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def get_login(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return _tmpl.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
def post_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        return _tmpl.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password.", "email_value": email},
            status_code=200,
        )
    if not user.is_active:
        return _tmpl.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Please confirm your email first — check your inbox for the link.",
                "email_value": email,
            },
            status_code=200,
        )
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def post_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

@router.get("/signup", response_class=HTMLResponse)
def get_signup(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return _tmpl.TemplateResponse("signup.html", {"request": request, "error": None})


@router.post("/signup", response_class=HTMLResponse)
@limiter.limit("5/minute")
def post_signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        return _tmpl.TemplateResponse(
            "signup.html",
            {"request": request, "error": "That doesn't look like a valid email.", "email_value": email},
            status_code=200,
        )
    if len(password) < 8:
        return _tmpl.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Password must be at least 8 characters.", "email_value": email},
            status_code=200,
        )

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        # Don't leak whether the email is registered. Show the same "check inbox"
        # screen as a fresh signup. If the existing account is unverified, resend
        # the verification email; otherwise silently noop.
        if not existing.is_active:
            existing.verification_token = _new_token()
            existing.verification_token_expires = _now() + timedelta(hours=24)
            db.commit()
            send_verification_email(existing.email, existing.verification_token)
        return _tmpl.TemplateResponse(
            "check_inbox.html", {"request": request, "email": email}
        )

    user = User(
        email=email,
        hashed_password=hash_password(password),
        is_active=False,
        verification_token=_new_token(),
        verification_token_expires=_now() + timedelta(hours=24),
    )
    db.add(user)
    db.flush()

    # Default new users to the free tier.
    db.add(Subscription(user_id=user.id, tier="free", history_days=30, seats=1, export_schedule=None))
    db.commit()

    send_verification_email(user.email, user.verification_token)
    return _tmpl.TemplateResponse("check_inbox.html", {"request": request, "email": email})


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@router.get("/verify", response_class=HTMLResponse)
def get_verify(request: Request, token: str = "", db: Session = Depends(get_consumer_db)):
    if not token:
        return _tmpl.TemplateResponse("verify.html", {"request": request, "ok": False, "reason": "missing_token"})

    user = db.query(User).filter(User.verification_token == token).first()
    if user is None:
        return _tmpl.TemplateResponse("verify.html", {"request": request, "ok": False, "reason": "invalid_token"})

    expires = user.verification_token_expires
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _now():
            return _tmpl.TemplateResponse("verify.html", {"request": request, "ok": False, "reason": "expired"})

    user.is_active = True
    user.email_verified_at = _now()
    user.verification_token = None
    user.verification_token_expires = None
    db.commit()

    request.session["user_id"] = user.id
    return _tmpl.TemplateResponse("verify.html", {"request": request, "ok": True, "reason": None})


# ---------------------------------------------------------------------------
# Password reset — request link
# ---------------------------------------------------------------------------

@router.get("/forgot", response_class=HTMLResponse)
def get_forgot(request: Request):
    return _tmpl.TemplateResponse("forgot.html", {"request": request, "sent": False, "error": None})


@router.post("/forgot", response_class=HTMLResponse)
@limiter.limit("5/minute")
def post_forgot(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.password_reset_token = _new_token()
        user.password_reset_token_expires = _now() + timedelta(hours=1)
        db.commit()
        send_password_reset_email(user.email, user.password_reset_token)
    # Always show "sent" — never confirm whether the email is registered.
    return _tmpl.TemplateResponse("forgot.html", {"request": request, "sent": True, "error": None, "email": email})


# ---------------------------------------------------------------------------
# Password reset — apply new password
# ---------------------------------------------------------------------------

@router.get("/reset", response_class=HTMLResponse)
def get_reset(request: Request, token: str = "", db: Session = Depends(get_consumer_db)):
    user = db.query(User).filter(User.password_reset_token == token).first() if token else None
    valid = bool(user and user.password_reset_token_expires)
    if valid:
        expires = user.password_reset_token_expires
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        valid = expires >= _now()
    return _tmpl.TemplateResponse(
        "reset.html",
        {"request": request, "token": token, "valid": valid, "error": None},
    )


@router.post("/reset", response_class=HTMLResponse)
@limiter.limit("10/minute")
def post_reset(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_consumer_db),
):
    if len(password) < 8:
        return _tmpl.TemplateResponse(
            "reset.html",
            {"request": request, "token": token, "valid": True, "error": "Password must be at least 8 characters."},
            status_code=200,
        )

    user = db.query(User).filter(User.password_reset_token == token).first() if token else None
    if user is None or user.password_reset_token_expires is None:
        return _tmpl.TemplateResponse(
            "reset.html",
            {"request": request, "token": token, "valid": False, "error": "This reset link is no longer valid."},
            status_code=200,
        )
    expires = user.password_reset_token_expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < _now():
        return _tmpl.TemplateResponse(
            "reset.html",
            {"request": request, "token": token, "valid": False, "error": "This reset link has expired."},
            status_code=200,
        )

    user.hashed_password = hash_password(password)
    user.password_reset_token = None
    user.password_reset_token_expires = None
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)
