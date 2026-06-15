"""
Auth helpers for the consumer app.
Uses raw bcrypt — passlib is broken on bcrypt 5.x + Python 3.13.

get_current_user returns User or RedirectResponse — does NOT raise.
Route handlers must check: if isinstance(result, RedirectResponse): return result
"""
import bcrypt
from fastapi import Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.requests import Request

from consumer.database import get_consumer_db
from consumer.models import User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def get_current_user(
    request: Request,
    db: Session = Depends(get_consumer_db),
) -> User | RedirectResponse:
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=302)
    return user


def require_tier(user: User, min_tier: str) -> bool:
    """True if the user's subscription tier meets or exceeds min_tier.
    Use the upgrade-card pattern (render a different template) when False —
    we don't 403 because that breaks the conversion path.
    """
    from consumer.models import tier_at_least
    return tier_at_least(user, min_tier)
