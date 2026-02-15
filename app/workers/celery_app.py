import asyncio
import logging
from datetime import UTC, datetime

from celery import Celery
from celery.signals import task_failure

from app.config import settings

logger = logging.getLogger(__name__)

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


@task_failure.connect
def handle_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **kw,
):
    """Send an email alert to the admin when a Celery task fails."""
    admin_email = settings.admin_alert_email
    if not admin_email:
        return

    # Don't send alert emails for notification task failures to avoid loops
    task_name = sender.name if sender else "unknown"
    if task_name.startswith("notifications."):
        logger.warning(f"Skipping error alert for notification task: {task_name}")
        return

    tb_text = ""
    if einfo:
        tb_text = str(einfo)
    elif traceback:
        tb_text = str(traceback)

    task_args = ""
    if args or kwargs:
        parts = []
        if args:
            parts.append(f"args={args}")
        if kwargs:
            parts.append(f"kwargs={kwargs}")
        task_args = ", ".join(parts)

    timestamp = datetime.now(UTC).strftime("%B %d, %Y at %H:%M:%S UTC")

    try:
        from app.utils.email import EmailTemplates, get_base_email_context, get_email_subject

        context = get_base_email_context(admin_email, name="Admin")
        context.update(
            {
                "task_name": task_name,
                "task_id": task_id or "N/A",
                "exception": str(exception),
                "traceback": tb_text,
                "args": task_args,
                "timestamp": timestamp,
                "retries": getattr(sender.request, "retries", None) if sender else None,
            }
        )

        subject = get_email_subject(EmailTemplates.ERROR_ALERT, task_name=task_name)

        from app.services.email_service import get_email_service

        email_service = get_email_service()
        html = email_service._render_template(EmailTemplates.ERROR_ALERT, context, is_html=True)
        text = email_service._render_template(EmailTemplates.ERROR_ALERT, context, is_html=False)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                email_service.send_email(
                    to_email=admin_email,
                    subject=subject,
                    html_content=html,
                    text_content=text,
                    to_name="Admin",
                )
            )
        finally:
            loop.close()

        logger.info(f"Error alert email sent to {admin_email} for task {task_name}")
    except Exception as e:
        logger.error(f"Failed to send error alert email: {e}")
