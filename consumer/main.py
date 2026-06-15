"""
Consumer FastAPI app — port 8001.
Completely isolated from the owner dashboard (main.py, port 8000).
vetted.db is accessed READ-ONLY only — never mutated by consumer code.
"""
import os
import sys
from contextlib import asynccontextmanager

# Make sibling modules (logging_config, etc.) importable when consumer
# is launched via `uvicorn consumer.main:app` from the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import logging_config
logging_config.configure()

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from consumer.database import engine
from consumer.limiter import limiter
from consumer.models import Base
from consumer.routes.auth import router as auth_router
from consumer.routes.billing import router as billing_router
from consumer.routes.pages import router as pages_router
from consumer.routes.api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sqlite3 as _sqlite3
    from consumer.database import CONSUMER_DB_PATH
    Base.metadata.create_all(bind=engine)

    # In-place migrations for an existing consumer.db. SQLAlchemy create_all
    # only creates missing tables — existing tables don't pick up new columns.
    _migrations = [
        ("users", "last_visit",                    "TIMESTAMP"),
        ("users", "email_verified_at",             "TIMESTAMP"),
        ("users", "verification_token",            "VARCHAR(64)"),
        ("users", "verification_token_expires",    "TIMESTAMP"),
        ("users", "password_reset_token",          "VARCHAR(64)"),
        ("users", "password_reset_token_expires",  "TIMESTAMP"),
        ("users", "stripe_customer_id",            "VARCHAR(64)"),
        ("subscriptions", "stripe_subscription_id", "VARCHAR(64)"),
        ("subscriptions", "period_end",            "TIMESTAMP"),
        ("subscriptions", "cancel_at_period_end",  "BOOLEAN DEFAULT 0 NOT NULL"),
    ]
    conn = _sqlite3.connect(CONSUMER_DB_PATH)
    for table, col, ddl in _migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            conn.commit()
        except _sqlite3.OperationalError:
            pass  # column already exists
    conn.close()
    yield


app = FastAPI(title="Vetted", lifespan=lifespan)

# Rate limiting (per-IP) — see consumer/limiter.py for the key function.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_secret = os.getenv("CONSUMER_SECRET_KEY", "dev-insecure-change-in-production")
app.add_middleware(SessionMiddleware, secret_key=_secret, https_only=False)

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/consumer/static", StaticFiles(directory=_static_dir), name="consumer-static")

app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(pages_router)
app.include_router(api_router)


@app.get("/health")
def health():
    """Unauthenticated liveness + readiness signal."""
    from fastapi.responses import JSONResponse
    from consumer.database import get_vetted_conn, CONSUMER_DB_PATH
    import sqlite3 as _sqlite3

    vetted_ok = True
    vetted_error = None
    try:
        conn = get_vetted_conn()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        vetted_ok = False
        vetted_error = str(exc)[:200]

    consumer_ok = True
    consumer_error = None
    try:
        c = _sqlite3.connect(CONSUMER_DB_PATH)
        c.execute("SELECT 1").fetchone()
        c.close()
    except Exception as exc:
        consumer_ok = False
        consumer_error = str(exc)[:200]

    status_str = "ok" if vetted_ok and consumer_ok else "degraded"
    return JSONResponse({
        "status": status_str,
        "vetted_db_connected": vetted_ok,
        "vetted_db_error": vetted_error,
        "consumer_db_connected": consumer_ok,
        "consumer_db_error": consumer_error,
    })
