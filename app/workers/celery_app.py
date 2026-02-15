from celery import Celery

from app.config import settings

celery_app = Celery(
    "hypertrade",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.analysis",
        "app.workers.tasks.trading",
        "app.workers.tasks.notifications",
        "app.workers.tasks.maintenance",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=300,
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

celery_app.conf.beat_schedule = {
    "analyze-markets-every-2-hours": {
        "task": "app.workers.tasks.analysis.analyze_all_markets",
        "schedule": 7200.0,
    },
    "sync-positions-every-minute": {
        "task": "app.workers.tasks.trading.sync_all_positions",
        "schedule": 60.0,
    },
    "check-subscriptions-daily": {
        "task": "app.workers.tasks.maintenance.check_subscriptions",
        "schedule": 86400.0,
    },
    "expire-signals-hourly": {
        "task": "app.workers.tasks.maintenance.expire_old_signals",
        "schedule": 3600.0,
    },
    "send-renewal-reminders-daily": {
        "task": "notifications.send_renewal_reminders",
        "schedule": 86400.0,
    },
    "send-expired-subscription-emails-daily": {
        "task": "notifications.send_expired_subscription_emails",
        "schedule": 86400.0,
    },
    "cleanup-old-notifications-weekly": {
        "task": "notifications.cleanup_old_notifications",
        "schedule": 604800.0,  # 7 days
    },
}
