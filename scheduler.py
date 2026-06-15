"""APScheduler integration for unattended Vetted operation.

Three jobs run on a persistent SQLite-backed schedule (jobs survive restarts):

  - daily_scan      cron 06:00 UTC    scanner.scan_all_channels + ROI baseline
  - hourly_roi      every hour        sync prices + refresh roi_current
  - daily_backup    cron 02:00 UTC    snapshot vetted.db + consumer.db

**Pre-launch behavior:** on the very first boot the jobs are added in the
*paused* state. Nothing fires automatically until the owner resumes them
from /admin/scheduler. This is what you want before the product is live —
the scheduler infra is up so the dashboard can manage it, but no Anthropic
calls / yfinance hits / backups happen on a schedule yet.

After resume, pause/run state persists in the SQLite jobstore, so a process
restart preserves whatever the owner last set.

Public surface:
  start(), stop()                    lifespan hooks
  get_status()                       snapshot for /health and the dashboard
  pause(job_id), resume(job_id)      per-job toggles
  pause_all(), resume_all()          bulk toggles
  trigger_now(job_id)                fire job once on a thread (does NOT
                                     unpause; safe to call on a paused job)
  job_specs()                        canonical [(id, name, trigger, fn), ...]
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import scanner

_log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent
_JOBSTORE_PATH = _REPO_ROOT / "scheduler.db"
_BACKUP_DIR = Path(os.getenv("VETTED_BACKUP_DIR", "/tmp/vetted-backups"))
_VETTED_DB = _REPO_ROOT / "vetted.db"
_CONSUMER_DB = _REPO_ROOT / "consumer" / "consumer.db"

_scheduler: BackgroundScheduler | None = None
_last_runs: dict[str, dict] = {}


def _record_run(job_id: str, ok: bool, detail: str = "") -> None:
    _last_runs[job_id] = {
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------

def _job_daily_scan() -> None:
    _log.info("[scheduler] daily_scan: start")
    try:
        scanner.scan_all_channels()
        scanner.fetch_roi_baselines()
        _record_run("daily_scan", True)
        _log.info("[scheduler] daily_scan: done")
    except Exception as exc:
        _log.exception("[scheduler] daily_scan failed")
        _record_run("daily_scan", False, str(exc)[:200])


def _job_hourly_roi() -> None:
    _log.info("[scheduler] hourly_roi: start")
    try:
        scanner.sync_price_daily()
        scanner.update_rois()
        _record_run("hourly_roi", True)
        _log.info("[scheduler] hourly_roi: done")
    except Exception as exc:
        _log.exception("[scheduler] hourly_roi failed")
        _record_run("hourly_roi", False, str(exc)[:200])


def _job_daily_backup() -> None:
    _log.info("[scheduler] daily_backup: start")
    try:
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = _BACKUP_DIR / f"vetted-{stamp}.tar.gz"
        targets = []
        if _VETTED_DB.exists():
            targets.append(str(_VETTED_DB.relative_to(_REPO_ROOT)))
        if _CONSUMER_DB.exists():
            targets.append(str(_CONSUMER_DB.relative_to(_REPO_ROOT)))
        if not targets:
            _record_run("daily_backup", False, "no databases found")
            _log.warning("[scheduler] daily_backup: no databases to back up")
            return
        cmd = ["tar", "czf", str(out), "-C", str(_REPO_ROOT), *targets]
        subprocess.run(cmd, check=True, capture_output=True)
        _prune_old_backups(keep=14)
        _record_run("daily_backup", True, f"wrote {out}")
        _log.info("[scheduler] daily_backup: wrote %s", out)
    except Exception as exc:
        _log.exception("[scheduler] daily_backup failed")
        _record_run("daily_backup", False, str(exc)[:200])


def _prune_old_backups(keep: int) -> None:
    if not _BACKUP_DIR.exists():
        return
    files = sorted(_BACKUP_DIR.glob("vetted-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Job catalog — single source of truth for what jobs exist
# ---------------------------------------------------------------------------

def job_specs() -> list[dict]:
    """Canonical list. Used by start() and the /admin/scheduler page."""
    return [
        {
            "id": "daily_scan",
            "name": "Daily channel scan + ROI baselines",
            "description": "Scans every tracked YouTube channel for new videos, runs the three-pass extraction, writes ROI baselines.",
            "trigger": CronTrigger(hour=6, minute=0, timezone="UTC"),
            "schedule_human": "Daily at 06:00 UTC",
            "fn": _job_daily_scan,
        },
        {
            "id": "hourly_roi",
            "name": "Hourly price sync + ROI refresh",
            "description": "Pulls latest prices from yfinance/Tiingo and refreshes 7d/30d/current ROI for tracked tickers.",
            "trigger": IntervalTrigger(hours=1),
            "schedule_human": "Every hour",
            "fn": _job_hourly_roi,
        },
        {
            "id": "daily_backup",
            "name": "Daily DB backup",
            "description": "Snapshots vetted.db + consumer.db to a tar.gz under VETTED_BACKUP_DIR. Keeps the 14 most recent.",
            "trigger": CronTrigger(hour=2, minute=0, timezone="UTC"),
            "schedule_human": "Daily at 02:00 UTC",
            "fn": _job_daily_backup,
        },
    ]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def start() -> BackgroundScheduler:
    """Start the scheduler. Idempotent — safe to call multiple times.

    On first boot, jobs are added in the *paused* state (next_run_time=None).
    On subsequent restarts, existing jobs are left untouched so the owner's
    pause/resume choices persist across deploys.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{_JOBSTORE_PATH}")}
    executors = {"default": ThreadPoolExecutor(max_workers=2)}
    job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 60 * 60}

    sched = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone="UTC",
    )

    # The scheduler must be running before we can add or query jobs reliably.
    sched.start()
    _scheduler = sched

    for spec in job_specs():
        if sched.get_job(spec["id"]):
            # Existing job — preserve owner's pause/resume choice.
            continue
        # First-time add: paused (next_run_time=None) so nothing fires
        # automatically until the owner resumes from the dashboard.
        sched.add_job(
            spec["fn"],
            trigger=spec["trigger"],
            id=spec["id"],
            name=spec["name"],
            next_run_time=None,
            replace_existing=False,
        )

    job_states = [
        f"{j.id}={'paused' if j.next_run_time is None else j.next_run_time.isoformat()}"
        for j in sched.get_jobs()
    ]
    _log.info("[scheduler] started; %s", " ".join(job_states))
    return sched


def stop() -> None:
    """Stop the scheduler cleanly. Idempotent."""
    global _scheduler
    if _scheduler is None:
        return
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
    _log.info("[scheduler] stopped")


# ---------------------------------------------------------------------------
# Status + per-job controls (used by /admin/scheduler)
# ---------------------------------------------------------------------------

def get_status() -> dict:
    """Snapshot of scheduler state for /health and the dashboard."""
    specs = {s["id"]: s for s in job_specs()}
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": [], "last_runs": _last_runs}
    jobs = []
    for j in _scheduler.get_jobs():
        spec = specs.get(j.id, {})
        jobs.append({
            "id": j.id,
            "name": j.name,
            "description": spec.get("description", ""),
            "schedule_human": spec.get("schedule_human", ""),
            "paused": j.next_run_time is None,
            "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            "last_run": _last_runs.get(j.id),
        })
    return {"running": True, "jobs": jobs, "last_runs": _last_runs}


def pause(job_id: str) -> bool:
    if _scheduler is None or not _scheduler.running:
        return False
    if not _scheduler.get_job(job_id):
        return False
    _scheduler.pause_job(job_id)
    _log.info("[scheduler] paused %s", job_id)
    return True


def resume(job_id: str) -> bool:
    if _scheduler is None or not _scheduler.running:
        return False
    if not _scheduler.get_job(job_id):
        return False
    _scheduler.resume_job(job_id)
    _log.info("[scheduler] resumed %s", job_id)
    return True


def pause_all() -> int:
    if _scheduler is None or not _scheduler.running:
        return 0
    n = 0
    for j in _scheduler.get_jobs():
        if j.next_run_time is not None:
            _scheduler.pause_job(j.id)
            n += 1
    _log.info("[scheduler] paused %d job(s)", n)
    return n


def resume_all() -> int:
    if _scheduler is None or not _scheduler.running:
        return 0
    n = 0
    for j in _scheduler.get_jobs():
        if j.next_run_time is None:
            _scheduler.resume_job(j.id)
            n += 1
    _log.info("[scheduler] resumed %d job(s)", n)
    return n


def trigger_now(job_id: str) -> bool:
    """Fire a job once on a background thread without changing its schedule
    or pause state. Safe to call on a paused job — the schedule stays paused
    after the one-shot completes.
    """
    spec = next((s for s in job_specs() if s["id"] == job_id), None)
    if spec is None:
        return False
    threading.Thread(
        target=spec["fn"],
        name=f"vetted-{job_id}-now",
        daemon=True,
    ).start()
    _log.info("[scheduler] triggered %s on a one-shot thread", job_id)
    return True
