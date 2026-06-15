# Consumer App Foundation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `consumer/` FastAPI application with session auth, user/subscription models, and a fully navigable shell (stubbed pages + working account page) as the deployable foundation for the Vetted B2B consumer dashboard.

**Architecture:** Separate FastAPI app in `consumer/` that runs on port 8001, completely isolated from the owner dashboard (`main.py`, port 8000). Connects to `vetted.db` read-only and manages its own `consumer.db` (users, subscriptions) via SQLAlchemy ORM. Session-based auth using Starlette `SessionMiddleware` with signed cookie + raw bcrypt passwords.

**Tech Stack:** FastAPI, SQLAlchemy (consumer.db), raw sqlite3 read-only (vetted.db), bcrypt, itsdangerous (Starlette sessions), Jinja2, pytest + TestClient

---

## Scope

This is **Plan 1 of 3**. Scope is limited to:
- App scaffold, DB connections, SQLAlchemy models
- bcrypt auth helpers + session-based auth dependency
- Login/logout routes + login page
- 52px icon sidebar base template
- Stub pages for all 7 routes (/, /stocks, /channels, /leads, /stats, /exports, /account)
- Working /account page (shows email, tier, sign out)

Data queries against vetted.db are **Plan 2** (Core Data Pages).

## Known Traps

1. **`passlib` is broken** on bcrypt 5.x + Python 3.14 — use `import bcrypt` directly (already installed)
2. **`@app.on_event` is deprecated** — use `lifespan=` context manager
3. **Static path is `/consumer/static`** (not `/static`) to avoid collision with owner dashboard
4. **`get_current_user` returns, doesn't raise** — route handlers check `if isinstance(current_user, RedirectResponse): return current_user`
5. **`tests/consumer/__init__.py` must exist** — otherwise conftest.py scope is wrong

---

## File Structure

**Create:**
```
consumer/__init__.py
consumer/main.py              # FastAPI app, lifespan, SessionMiddleware, router mounts
consumer/database.py          # consumer.db (SQLAlchemy) + vetted.db (read-only sqlite3)
consumer/models.py            # User, Subscription (SQLAlchemy ORM)
consumer/auth.py              # hash_password, verify_password, get_current_user dependency
consumer/routes/__init__.py
consumer/routes/auth.py       # GET/POST /login, POST /logout
consumer/routes/pages.py      # All 7 page routes (stubs + /account)
consumer/templates/base.html  # 52px sidebar layout
consumer/templates/login.html
consumer/templates/home.html  # stub
consumer/templates/stocks.html
consumer/templates/channels.html
consumer/templates/leads.html
consumer/templates/stats.html
consumer/templates/exports.html
consumer/templates/account.html
consumer/static/consumer.css
scripts/seed_consumer.py
tests/consumer/__init__.py
tests/consumer/conftest.py
tests/consumer/test_database.py
tests/consumer/test_models.py
tests/consumer/test_auth.py
tests/consumer/test_routes.py
```

**Modify:**
- `requirements.txt` — add `sqlalchemy`, `itsdangerous`
- `.env` — add `CONSUMER_SECRET_KEY` (user does this manually)

**Do not touch:**
- `main.py`, `db_manager.py`, `templates/`, `static/` — owner dashboard, read-only

---

## Chunk 1: Database Layer + Models

### Task 0: Install dependencies

- [ ] Add to `requirements.txt`:
  ```
  sqlalchemy
  itsdangerous
  bcrypt
  ```

- [ ] Install:
  ```bash
  pip install sqlalchemy itsdangerous bcrypt
  ```

- [ ] Commit:
  ```bash
  git add requirements.txt
  git commit -m "feat(consumer): add sqlalchemy and itsdangerous dependencies"
  ```

---

### Task 1: Scaffold + database.py

**Files:** Create `consumer/__init__.py`, `consumer/database.py`, `tests/consumer/__init__.py`, `tests/consumer/conftest.py`

- [ ] **Create directory structure:**
  ```bash
  mkdir -p consumer/routes consumer/templates consumer/static
  touch consumer/__init__.py consumer/routes/__init__.py
  mkdir -p tests/consumer scripts
  touch tests/consumer/__init__.py
  ```

- [ ] **Write `tests/consumer/conftest.py`:**
  ```python
  import pytest
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker
  from consumer.models import Base

  @pytest.fixture(scope="function")
  def engine():
      eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
      Base.metadata.create_all(eng)
      yield eng
      Base.metadata.drop_all(eng)
      eng.dispose()

  @pytest.fixture(scope="function")
  def db_session(engine):
      Session = sessionmaker(bind=engine)
      session = Session()
      yield session
      session.close()
  ```

- [ ] **Write `tests/consumer/test_database.py`:**
  ```python
  import sqlite3
  import pytest
  from sqlalchemy.orm import Session
  from consumer.database import get_consumer_db, CONSUMER_DB_URL

  def test_consumer_db_url_is_consumer_db():
      assert "consumer.db" in CONSUMER_DB_URL
      assert "vetted.db" not in CONSUMER_DB_URL

  def test_get_consumer_db_yields_session():
      from consumer.database import get_consumer_db
      gen = get_consumer_db()
      session = next(gen)
      assert isinstance(session, Session)
      try:
          next(gen)
      except StopIteration:
          pass

  def test_vetted_conn_is_read_only(tmp_path):
      import consumer.database as cdb
      fake = tmp_path / "vetted.db"
      fake.touch()
      original = cdb.VETTED_DB_PATH
      cdb.VETTED_DB_PATH = str(fake)
      try:
          conn = cdb.get_vetted_conn()
          with pytest.raises(sqlite3.OperationalError):
              conn.execute("CREATE TABLE x (id INTEGER)")
          conn.close()
      finally:
          cdb.VETTED_DB_PATH = original
  ```

- [ ] **Run tests — expect FAIL (module missing):**
  ```bash
  pytest tests/consumer/test_database.py -v
  ```

- [ ] **Write `consumer/database.py`:**
  ```python
  """
  Database connections for the consumer app.
  - consumer.db: SQLAlchemy ORM (read-write) — user/subscription state
  - vetted.db: raw sqlite3 read-only — owner data, never mutated by consumer code
  """
  import os
  import sqlite3
  from typing import Generator
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker, Session

  _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

  CONSUMER_DB_PATH = os.path.join(_BASE_DIR, "consumer.db")
  CONSUMER_DB_URL = f"sqlite:///{CONSUMER_DB_PATH}"

  engine = create_engine(CONSUMER_DB_URL, connect_args={"check_same_thread": False})
  SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

  def get_consumer_db() -> Generator[Session, None, None]:
      """FastAPI dependency: yields a consumer.db SQLAlchemy session."""
      db = SessionLocal()
      try:
          yield db
      finally:
          db.close()

  # vetted.db is one directory up from consumer/
  VETTED_DB_PATH = os.path.join(os.path.dirname(_BASE_DIR), "vetted.db")

  def get_vetted_conn() -> sqlite3.Connection:
      """
      Return a read-only sqlite3 connection to vetted.db.
      Caller is responsible for closing it.
      ROI baseline: always closing price on video publish date — see db_manager.py.
      """
      conn = sqlite3.connect(f"file:{VETTED_DB_PATH}?mode=ro", uri=True)
      conn.row_factory = sqlite3.Row
      return conn
  ```

- [ ] **Run tests — expect PASS:**
  ```bash
  pytest tests/consumer/test_database.py -v
  ```
  Expected: 3 passed

- [ ] **Commit:**
  ```bash
  git add consumer/ tests/consumer/
  git commit -m "feat(consumer): add app scaffold and read-only database connections"
  ```

---

### Task 2: SQLAlchemy models

**Files:** Create `consumer/models.py`, `tests/consumer/test_models.py`

- [ ] **Write `tests/consumer/test_models.py`:**
  ```python
  import pytest
  from sqlalchemy import inspect
  from sqlalchemy.exc import IntegrityError
  from consumer.models import Base, User, Subscription

  def test_user_table_columns(engine):
      cols = {c["name"] for c in inspect(engine).get_columns("users")}
      assert cols == {"id", "email", "hashed_password", "is_active", "created_at"}

  def test_subscription_table_columns(engine):
      cols = {c["name"] for c in inspect(engine).get_columns("subscriptions")}
      assert cols == {"id", "user_id", "tier", "history_days", "seats", "export_schedule", "created_at"}

  def test_create_user(db_session):
      from datetime import datetime
      user = User(email="alice@example.com", hashed_password="$2b$12$x", is_active=True)
      db_session.add(user)
      db_session.commit()
      db_session.refresh(user)
      assert user.id is not None
      assert user.email == "alice@example.com"
      assert isinstance(user.created_at, datetime)

  def test_create_subscription(db_session):
      user = User(email="bob@example.com", hashed_password="x", is_active=True)
      db_session.add(user); db_session.commit(); db_session.refresh(user)
      sub = Subscription(user_id=user.id, tier="starter", history_days=30, seats=1)
      db_session.add(sub); db_session.commit(); db_session.refresh(sub)
      assert sub.id is not None
      assert sub.tier == "starter"
      assert sub.history_days == 30

  def test_user_subscription_relationship(db_session):
      user = User(email="carol@example.com", hashed_password="x", is_active=True)
      db_session.add(user); db_session.commit()
      sub = Subscription(user_id=user.id, tier="pro", history_days=365, seats=1)
      db_session.add(sub); db_session.commit()
      db_session.refresh(user)
      assert user.subscription is not None
      assert user.subscription.tier == "pro"

  def test_email_unique_constraint(db_session):
      db_session.add(User(email="dup@example.com", hashed_password="x", is_active=True))
      db_session.commit()
      db_session.add(User(email="dup@example.com", hashed_password="y", is_active=True))
      with pytest.raises(IntegrityError):
          db_session.commit()
  ```

- [ ] **Run tests — expect FAIL:**
  ```bash
  pytest tests/consumer/test_models.py -v
  ```

- [ ] **Write `consumer/models.py`:**
  ```python
  from datetime import datetime, timezone
  from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
  from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

  class Base(DeclarativeBase):
      pass

  class User(Base):
      __tablename__ = "users"
      __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

      id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
      email: Mapped[str] = mapped_column(String(254), nullable=False)
      hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
      is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True),
          default=lambda: datetime.now(timezone.utc),
          nullable=False,
      )
      subscription: Mapped["Subscription"] = relationship("Subscription", back_populates="user", uselist=False)

  class Subscription(Base):
      __tablename__ = "subscriptions"

      id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
      user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
      tier: Mapped[str] = mapped_column(String(32), nullable=False)        # starter|pro|enterprise
      history_days: Mapped[int] = mapped_column(Integer, nullable=False)   # 30|365|0 (0=all-time)
      seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
      export_schedule: Mapped[str | None] = mapped_column(String(32), nullable=True)
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True),
          default=lambda: datetime.now(timezone.utc),
          nullable=False,
      )
      user: Mapped[User] = relationship("User", back_populates="subscription")
  ```

- [ ] **Run tests — expect PASS:**
  ```bash
  pytest tests/consumer/test_models.py -v
  ```
  Expected: 5 passed

- [ ] **Commit:**
  ```bash
  git add consumer/models.py tests/consumer/test_models.py
  git commit -m "feat(consumer): add User and Subscription SQLAlchemy models"
  ```

---

## Chunk 2: Auth

### Task 3: bcrypt helpers + session auth dependency

**Files:** Create `consumer/auth.py`, `tests/consumer/test_auth.py`

- [ ] **Write `tests/consumer/test_auth.py`:**
  ```python
  import pytest
  from unittest.mock import MagicMock
  from fastapi.responses import RedirectResponse
  from consumer.auth import hash_password, verify_password, get_current_user
  from consumer.models import User

  def test_hash_password_is_bcrypt():
      h = hash_password("mysecret")
      assert isinstance(h, str)
      assert h.startswith("$2b$")

  def test_verify_password_correct():
      h = hash_password("correcthorse")
      assert verify_password("correcthorse", h) is True

  def test_verify_password_wrong():
      h = hash_password("correcthorse")
      assert verify_password("batterystaple", h) is False

  def test_verify_password_empty():
      h = hash_password("notempty")
      assert verify_password("", h) is False

  def test_get_current_user_no_session():
      req = MagicMock(); req.session = {}
      result = get_current_user(req, MagicMock())
      assert isinstance(result, RedirectResponse)

  def test_get_current_user_valid(db_session):
      user = User(email="z@example.com", hashed_password="x", is_active=True)
      db_session.add(user); db_session.commit(); db_session.refresh(user)
      req = MagicMock(); req.session = {"user_id": user.id}
      result = get_current_user(req, db_session)
      assert isinstance(result, User)
      assert result.email == "z@example.com"

  def test_get_current_user_missing_user(db_session):
      req = MagicMock(); req.session = {"user_id": 99999}
      result = get_current_user(req, db_session)
      assert isinstance(result, RedirectResponse)

  def test_get_current_user_inactive(db_session):
      user = User(email="inactive@example.com", hashed_password="x", is_active=False)
      db_session.add(user); db_session.commit(); db_session.refresh(user)
      req = MagicMock(); req.session = {"user_id": user.id}
      result = get_current_user(req, db_session)
      assert isinstance(result, RedirectResponse)
  ```

- [ ] **Run tests — expect FAIL:**
  ```bash
  pytest tests/consumer/test_auth.py -v
  ```

- [ ] **Write `consumer/auth.py`:**
  ```python
  """
  Auth helpers for the consumer app.
  Uses raw bcrypt — passlib is broken on bcrypt 5.x + Python 3.14.
  get_current_user returns User or RedirectResponse (does not raise).
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
  ```

- [ ] **Run tests — expect PASS:**
  ```bash
  pytest tests/consumer/test_auth.py -v
  ```
  Expected: 8 passed

- [ ] **Commit:**
  ```bash
  git add consumer/auth.py tests/consumer/test_auth.py
  git commit -m "feat(consumer): add bcrypt password helpers and session auth dependency"
  ```

---

### Task 4: Login/logout routes

**Files:** Create `consumer/routes/auth.py`

- [ ] **Write `consumer/routes/auth.py`:**
  ```python
  import os
  from fastapi import APIRouter, Depends, Form, Request
  from fastapi.responses import HTMLResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates
  from sqlalchemy.orm import Session
  from consumer.auth import verify_password
  from consumer.database import get_consumer_db
  from consumer.models import User

  router = APIRouter()
  _TMPL = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))

  @router.get("/login", response_class=HTMLResponse)
  def get_login(request: Request):
      if request.session.get("user_id"):
          return RedirectResponse(url="/", status_code=302)
      return _TMPL.TemplateResponse("login.html", {"request": request, "error": None})

  @router.post("/login", response_class=HTMLResponse)
  def post_login(
      request: Request,
      email: str = Form(...),
      password: str = Form(...),
      db: Session = Depends(get_consumer_db),
  ):
      user = db.query(User).filter(User.email == email, User.is_active == True).first()
      if user is None or not verify_password(password, user.hashed_password):
          return _TMPL.TemplateResponse(
              "login.html",
              {"request": request, "error": "Invalid email or password.", "email_value": email},
              status_code=200,
          )
      request.session["user_id"] = user.id
      return RedirectResponse(url="/", status_code=303)

  @router.post("/logout")
  def post_logout(request: Request):
      request.session.clear()
      return RedirectResponse(url="/login", status_code=303)
  ```

---

## Chunk 3: Shell + Account + Verification

### Task 5: CSS design system

**Files:** Create `consumer/static/consumer.css`

- [ ] **Write `consumer/static/consumer.css`:**
  ```css
  :root {
    --bg: #080808; --bg2: #0c0c0c; --bg3: #111;
    --border: #1a1a1a; --text: #e8e8e8; --muted: #666;
    --accent: #d97706; --green: #4ade80; --red: #ef4444;
    --font: 'Geist', system-ui, -apple-system, sans-serif;
    --sidebar: 52px;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font); min-height: 100vh; display: flex; }

  /* Sidebar */
  .sidebar { position: fixed; top: 0; left: 0; width: var(--sidebar); height: 100vh; background: var(--bg2); border-right: 1px solid var(--border); display: flex; flex-direction: column; align-items: center; padding: 14px 0; gap: 4px; z-index: 200; }
  .sidebar-logo { width: 28px; height: 28px; background: var(--accent); border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 800; color: #000; margin-bottom: 10px; flex-shrink: 0; }
  .nav-icon { width: 36px; height: 36px; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: var(--muted); text-decoration: none; transition: background .12s, color .12s; position: relative; }
  .nav-icon:hover { background: var(--bg3); color: var(--text); }
  .nav-icon.active { background: #1a1a1a; color: var(--accent); }
  .nav-icon svg { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }
  .nav-icon::after { content: attr(data-label); position: absolute; left: calc(var(--sidebar) - 4px); top: 50%; transform: translateY(-50%); background: var(--bg3); border: 1px solid var(--border); color: var(--text); font-size: 12px; font-weight: 500; padding: 4px 10px; border-radius: 6px; white-space: nowrap; pointer-events: none; opacity: 0; transition: opacity .1s; z-index: 999; }
  .nav-icon:hover::after { opacity: 1; }
  .sidebar-spacer { flex: 1; }

  /* Page shell */
  .page-wrap { margin-left: var(--sidebar); padding: 32px 36px; min-height: 100vh; width: 100%; }
  h1 { font-size: 22px; font-weight: 600; margin: 0 0 6px; color: #fff; }
  h2 { font-size: 16px; font-weight: 600; margin: 0 0 16px; }
  p  { color: var(--muted); font-size: 14px; }
  .page-header { margin-bottom: 32px; }
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }

  /* Buttons */
  .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; font-size: 13px; font-weight: 600; border-radius: 6px; cursor: pointer; border: none; font-family: var(--font); text-decoration: none; transition: background .15s; }
  .btn-primary { background: var(--accent); color: #000; }
  .btn-primary:hover { background: #b45309; color: #000; }
  .btn-secondary { background: transparent; color: var(--text); border: 1px solid var(--border); }
  .btn-secondary:hover { border-color: #333; background: var(--bg3); color: var(--text); }

  /* Forms */
  .form-group { display: flex; flex-direction: column; gap: 6px; }
  .form-group label { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; }
  .form-input { background: var(--bg3); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 6px; font-size: 14px; font-family: var(--font); outline: none; transition: border-color .15s; width: 100%; }
  .form-input:focus { border-color: var(--accent); }
  .form-input::placeholder { color: var(--muted); }
  .error-msg { background: rgba(239,68,68,.08); border: 1px solid rgba(239,68,68,.25); color: var(--red); padding: 10px 14px; border-radius: 6px; font-size: 13px; }

  /* Login */
  .login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); }
  .login-card { width: 360px; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 36px 32px; }
  .login-logo { display: flex; align-items: center; gap: 10px; margin-bottom: 28px; }
  .login-logo-mark { width: 32px; height: 32px; background: var(--accent); border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 800; color: #000; }
  .login-logo-text { font-size: 16px; font-weight: 700; color: #fff; letter-spacing: .04em; }
  .login-logo-sub { font-size: 11px; color: var(--muted); margin-top: 1px; }
  .login-fields { display: flex; flex-direction: column; gap: 16px; }

  /* Stubs */
  .stub-container { display: flex; align-items: center; justify-content: center; height: 60vh; flex-direction: column; gap: 10px; color: var(--muted); }
  .stub-container h2 { color: var(--muted); font-weight: 400; }

  /* Account */
  .account-field { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); font-size: 14px; }
  .account-field:last-child { border-bottom: none; }
  .account-field-label { color: var(--muted); min-width: 120px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
  .tier-badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; background: rgba(217,119,6,.15); color: var(--accent); }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 5px; } ::-webkit-scrollbar-track { background: var(--bg); } ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ```

---

### Task 6: Templates

**Files:** Create all templates in `consumer/templates/`

- [ ] **Write `consumer/templates/base.html`:**
  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Vetted{% endblock %}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/consumer/static/consumer.css">
    {% block head %}{% endblock %}
  </head>
  <body>
    <nav class="sidebar">
      <div class="sidebar-logo">V</div>
      <a href="/"        class="nav-icon {% if active_page=='home' %}active{% endif %}"     data-label="Home">
        <svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg></a>
      <a href="/stocks"   class="nav-icon {% if active_page=='stocks' %}active{% endif %}"   data-label="Stocks">
        <svg viewBox="0 0 24 24"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg></a>
      <a href="/channels" class="nav-icon {% if active_page=='channels' %}active{% endif %}" data-label="Channels">
        <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg></a>
      <a href="/leads"    class="nav-icon {% if active_page=='leads' %}active{% endif %}"    data-label="Leads">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></a>
      <a href="/stats"    class="nav-icon {% if active_page=='stats' %}active{% endif %}"    data-label="Stats">
        <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></a>
      <a href="/exports"  class="nav-icon {% if active_page=='exports' %}active{% endif %}"  data-label="Exports">
        <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></a>
      <div class="sidebar-spacer"></div>
      <a href="/account"  class="nav-icon {% if active_page=='account' %}active{% endif %}"  data-label="Account">
        <svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></a>
    </nav>
    <div class="page-wrap">{% block content %}{% endblock %}</div>
    {% block scripts %}{% endblock %}
  </body>
  </html>
  ```

- [ ] **Write `consumer/templates/login.html`:**
  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vetted — Sign In</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/consumer/static/consumer.css">
  </head>
  <body>
    <div class="login-wrap">
      <div class="login-card">
        <div class="login-logo">
          <div class="login-logo-mark">V</div>
          <div><div class="login-logo-text">VETTED</div><div class="login-logo-sub">Intelligence Platform</div></div>
        </div>
        {% if error %}<div class="error-msg" style="margin-bottom:16px">{{ error }}</div>{% endif %}
        <form method="POST" action="/login" class="login-fields">
          <div class="form-group">
            <label for="email">Email</label>
            <input id="email" name="email" type="email" class="form-input" placeholder="you@firm.com" value="{{ email_value|default('') }}" required autocomplete="username">
          </div>
          <div class="form-group">
            <label for="password">Password</label>
            <input id="password" name="password" type="password" class="form-input" placeholder="••••••••" required autocomplete="current-password">
          </div>
          <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center;margin-top:8px">Sign in</button>
        </form>
      </div>
    </div>
  </body>
  </html>
  ```

- [ ] **Write stub templates** (repeat pattern for home, stocks, channels, leads, stats, exports — change `active_page`, title, icon):

  `consumer/templates/home.html`:
  ```html
  {% extends "base.html" %}
  {% block title %}Vetted — Home{% endblock %}
  {% block content %}
  <div class="page-header"><h1>Home</h1><p>Activity feed and recent coverage — coming in Plan 2.</p></div>
  <div class="stub-container"><h2>Data pages in Plan 2</h2></div>
  {% endblock %}
  ```
  Repeat for `stocks.html`, `channels.html`, `leads.html`, `stats.html`, `exports.html` — changing only the title and page header text.

  **Do NOT use `{% set active_page = ... %}` inside child templates.** The `active_page` variable is passed via route context from `_page()` in `pages.py` and is available to `base.html` at render time. Setting it inside a child block would not propagate to the parent scope in Jinja2.

- [ ] **Write `consumer/templates/account.html`:**
  ```html
  {% extends "base.html" %}
  {% block title %}Vetted — Account{% endblock %}
  {% block content %}
  <div class="page-header"><h1>Account</h1><p>Your subscription and access settings.</p></div>

  <div class="card" style="max-width:480px;margin-bottom:16px">
    <h2>Profile</h2>
    <div class="account-field">
      <span class="account-field-label">Email</span>
      <span>{{ user.email }}</span>
    </div>
    <div class="account-field">
      <span class="account-field-label">Status</span>
      <span style="color:var(--green)">Active</span>
    </div>
    <div class="account-field">
      <span class="account-field-label">Member since</span>
      <span>{{ user.created_at.strftime('%b %d, %Y') }}</span>
    </div>
  </div>

  {% if user.subscription %}
  <div class="card" style="max-width:480px;margin-bottom:24px">
    <h2>Subscription</h2>
    <div class="account-field">
      <span class="account-field-label">Plan</span>
      <span class="tier-badge">{{ user.subscription.tier }}</span>
    </div>
    <div class="account-field">
      <span class="account-field-label">History</span>
      <span>{% if user.subscription.history_days == 0 %}All-time{% else %}{{ user.subscription.history_days }} days{% endif %}</span>
    </div>
    <div class="account-field">
      <span class="account-field-label">Exports</span>
      <span>{{ user.subscription.export_schedule or '—' }}</span>
    </div>
    <div class="account-field">
      <span class="account-field-label">Seats</span>
      <span>{{ user.subscription.seats }}</span>
    </div>
  </div>
  {% endif %}

  <form method="POST" action="/logout">
    <button type="submit" class="btn btn-secondary">Sign out</button>
  </form>
  {% endblock %}
  ```

---

### Task 7: Page routes + main.py

**Files:** Create `consumer/routes/pages.py`, `consumer/main.py`

- [ ] **Write `consumer/routes/pages.py`:**
  ```python
  import os
  from fastapi import APIRouter, Depends, Request
  from fastapi.responses import HTMLResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates
  from sqlalchemy.orm import Session
  from consumer.auth import get_current_user
  from consumer.database import get_consumer_db

  router = APIRouter()
  _TMPL = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))

  def _auth(request, db):
      user = get_current_user(request, db)
      if isinstance(user, RedirectResponse):
          return None, user
      return user, None

  def _page(tmpl, request, db, active_page: str, **ctx):
      """
      active_page must be passed explicitly — Jinja2 {% set %} inside a child block
      does NOT propagate to the parent template scope, so base.html sidebar would
      never see it. Passing it in the template context is the correct approach.
      """
      user, redir = _auth(request, db)
      if redir:
          return redir
      return _TMPL.TemplateResponse(tmpl, {"request": request, "user": user, "active_page": active_page, **ctx})

  @router.get("/", response_class=HTMLResponse)
  def home(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("home.html", request, db, active_page="home")

  @router.get("/stocks", response_class=HTMLResponse)
  def stocks(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("stocks.html", request, db, active_page="stocks")

  @router.get("/channels", response_class=HTMLResponse)
  def channels(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("channels.html", request, db, active_page="channels")

  @router.get("/leads", response_class=HTMLResponse)
  def leads(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("leads.html", request, db, active_page="leads")

  @router.get("/stats", response_class=HTMLResponse)
  def stats(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("stats.html", request, db, active_page="stats")

  @router.get("/exports", response_class=HTMLResponse)
  def exports(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("exports.html", request, db, active_page="exports")

  @router.get("/account", response_class=HTMLResponse)
  def account(request: Request, db: Session = Depends(get_consumer_db)):
      return _page("account.html", request, db, active_page="account")
  ```

- [ ] **Write `consumer/main.py`:**
  ```python
  """
  Consumer FastAPI app — port 8001.
  Does NOT import from main.py or db_manager.py.
  vetted.db is accessed READ-ONLY only.
  """
  import os
  from contextlib import asynccontextmanager
  from fastapi import FastAPI
  from fastapi.staticfiles import StaticFiles
  from starlette.middleware.sessions import SessionMiddleware
  from consumer.database import engine
  from consumer.models import Base
  from consumer.routes.auth import router as auth_router
  from consumer.routes.pages import router as pages_router

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      Base.metadata.create_all(bind=engine)
      yield

  app = FastAPI(title="Vetted Consumer", lifespan=lifespan)

  _secret = os.getenv("CONSUMER_SECRET_KEY", "dev-insecure-change-in-production")
  app.add_middleware(SessionMiddleware, secret_key=_secret, https_only=False)

  _static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
  app.mount("/consumer/static", StaticFiles(directory=_static), name="consumer-static")

  app.include_router(auth_router)
  app.include_router(pages_router)
  ```

---

### Task 8: Route tests + seed script

**Files:** Create `tests/consumer/test_routes.py`, `scripts/seed_consumer.py`

- [ ] **Write `tests/consumer/test_routes.py`:**
  ```python
  import pytest
  from fastapi.testclient import TestClient
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker
  from consumer.main import app
  from consumer.database import get_consumer_db
  from consumer.models import Base, User, Subscription
  from consumer.auth import hash_password

  @pytest.fixture(scope="function")
  def override_db():
      eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
      Base.metadata.create_all(eng)
      Sess = sessionmaker(bind=eng)
      def _override():
          db = Sess()
          try: yield db
          finally: db.close()
      app.dependency_overrides[get_consumer_db] = _override
      db = Sess()
      yield db
      db.close()
      app.dependency_overrides.clear()
      Base.metadata.drop_all(eng)

  @pytest.fixture
  def client(override_db):
      return TestClient(app, raise_server_exceptions=True)

  @pytest.fixture
  def seeded_user(override_db):
      user = User(email="test@example.com", hashed_password=hash_password("password123"), is_active=True)
      override_db.add(user); override_db.commit(); override_db.refresh(user)
      sub = Subscription(user_id=user.id, tier="starter", history_days=30, seats=1)
      override_db.add(sub); override_db.commit()
      return user

  def _login(client):
      client.post("/login", data={"email": "test@example.com", "password": "password123"}, follow_redirects=True)

  def test_login_page(client):
      assert client.get("/login").status_code == 200

  def test_login_success(client, seeded_user):
      r = client.post("/login", data={"email": "test@example.com", "password": "password123"}, follow_redirects=False)
      assert r.status_code in (302, 303)

  def test_login_bad_password(client, seeded_user):
      r = client.post("/login", data={"email": "test@example.com", "password": "wrong"}, follow_redirects=False)
      assert r.status_code == 200
      assert b"invalid" in r.content.lower()

  def test_logout(client, seeded_user):
      _login(client)
      r = client.post("/logout", follow_redirects=False)
      assert r.status_code in (302, 303)

  @pytest.mark.parametrize("route", ["/", "/stocks", "/channels", "/leads", "/stats", "/exports", "/account"])
  def test_unauthenticated_redirects(client, route):
      r = client.get(route, follow_redirects=False)
      assert r.status_code in (302, 303)
      assert "login" in r.headers["location"]

  @pytest.mark.parametrize("route", ["/", "/stocks", "/channels", "/leads", "/stats", "/exports", "/account"])
  def test_authenticated_200(client, seeded_user, route):
      _login(client)
      assert client.get(route).status_code == 200

  def test_account_shows_email(client, seeded_user):
      _login(client)
      assert b"test@example.com" in client.get("/account").content

  def test_account_shows_tier(client, seeded_user):
      _login(client)
      assert b"starter" in client.get("/account").content.lower()
  ```

- [ ] **Run all consumer tests — expect all PASS:**
  ```bash
  pytest tests/consumer/ -v
  ```
  Expected: ~28 passed

- [ ] **Write `scripts/seed_consumer.py`:**
  ```python
  #!/usr/bin/env python3
  """Seed consumer.db with an initial user. Run from project root."""
  import sys, os
  sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
  from sqlalchemy.orm import sessionmaker
  from consumer.database import engine
  from consumer.models import Base, User, Subscription
  from consumer.auth import hash_password

  EMAIL, PASSWORD, TIER = "owner@vetted.co", "changeme123", "enterprise"

  Base.metadata.create_all(bind=engine)
  Session = sessionmaker(bind=engine)
  db = Session()
  if db.query(User).filter(User.email == EMAIL).first():
      print(f"{EMAIL} already exists — skipping.")
  else:
      u = User(email=EMAIL, hashed_password=hash_password(PASSWORD), is_active=True)
      db.add(u); db.flush()
      db.add(Subscription(user_id=u.id, tier=TIER, history_days=0, seats=5, export_schedule="on-demand"))
      db.commit()
      print(f"Created: {EMAIL} / {PASSWORD}  (tier: {TIER})")
      print("IMPORTANT: Change this password before any real use.")
  db.close()
  ```

- [ ] **Commit everything:**
  ```bash
  git add consumer/ tests/consumer/ scripts/
  git commit -m "feat(consumer): complete foundation — shell, auth, account page, 28 tests green"
  ```

---

## Verification

### 1. Run full test suite (must not break existing tests)
```bash
pytest tests/ -v
```
Expected: all existing `test_scorer.py` + `test_evals_store.py` + all new `tests/consumer/` pass.

### 2. Add CONSUMER_SECRET_KEY to .env
```
CONSUMER_SECRET_KEY=replace-with-64-random-chars
```

### 3. Seed a user
```bash
python scripts/seed_consumer.py
```

### 4. Run both servers simultaneously
```bash
# Terminal 1 — owner dashboard (unchanged)
uvicorn main:app --port 8000 --reload

# Terminal 2 — consumer app
uvicorn consumer.main:app --port 8001 --reload
```

### 5. Manual checklist
- [ ] `http://localhost:8000/` — owner dashboard, HTTP Basic Auth prompt, no consumer routes
- [ ] `http://localhost:8001/` — redirects to `/login`
- [ ] Login with `owner@vetted.co` / `changeme123` — lands on Home stub
- [ ] All 7 nav icons navigate without errors
- [ ] `/account` shows email, `enterprise` tier badge, member since date
- [ ] Sign out returns to `/login`
- [ ] `consumer.db` created in `consumer/consumer.db`
- [ ] `vetted.db` mtime unchanged (no writes from consumer app)

---

## Next Plans

- **Plan 2: Core Data Pages** — Home (activity feed from vetted.db), Channels leaderboard, Channel detail, Stocks, Stock detail
- **Plan 3: Power Features** — Leads, Stats, Exports (CSV/JSON downloads + scheduled exports)
