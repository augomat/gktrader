from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from gktrader.config.settings import get_settings

settings = get_settings()

celery_app = Celery("gktrader", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    timezone=settings.europe_timezone,
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_retry_delay=30,
    task_routes={"gktrader.tasks.jobs.*": {"queue": "gktrader"}},
    beat_schedule={
        "poll-sources": {
            "task": "gktrader.tasks.jobs.poll_sources",
            "schedule": settings.source_poll_interval_seconds,
        },
        "deliver-pending-alerts": {
            "task": "gktrader.tasks.jobs.deliver_pending_alerts",
            "schedule": 15.0,
        },
        "weekly-review": {
            "task": "gktrader.tasks.jobs.generate_weekly_review",
            "schedule": crontab(hour=14, minute=0, day_of_week=0),
        },
        "deliver-weekly-review": {
            "task": "gktrader.tasks.jobs.deliver_weekly_review",
            "schedule": 120.0,
        },
        "deliver-snooze-reminders": {
            "task": "gktrader.tasks.jobs.deliver_snooze_reminders",
            "schedule": 30.0,
        },
        "compute-performance-horizons": {
            "task": "gktrader.tasks.jobs.compute_performance_horizons",
            "schedule": 1800.0,  # every 30 minutes
        },
        "refresh-ticker-master": {
            "task": "gktrader.tasks.jobs.refresh_ticker_master",
            "schedule": crontab(hour=3, minute=0),  # daily 03:00 (spec §9)
        },
    },
)
