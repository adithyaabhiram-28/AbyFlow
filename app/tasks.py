import os
import logging
from celery import Celery
from app.services import user_service

logger = logging.getLogger(__name__)

# Initialize Celery app
redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
celery = Celery(
    'abyflow',
    broker=redis_url,
    backend=None
)

# Celery configurations
celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Lazy app instance cache
_flask_app = None


def get_flask_app():
    global _flask_app
    if _flask_app is None:
        from app import create_app
        _flask_app = create_app()
    return _flask_app


@celery.task(bind=True, max_retries=3, default_retry_delay=5)
def process_payment_task(self, user_email: str):
    """
    Background worker task to upgrade a user's subscription tier after successful payment.
    Implements automatic exponential backoff retries for transient failures.
    """
    logger.info("Celery worker processing payment for user: %s (task_id=%s)", user_email, self.request.id)
    app = get_flask_app()

    with app.app_context():
        try:
            success, message = user_service.upgrade_user_to_pro(user_email)
            if not success and "Database error" in message:
                logger.warning("Database error in payment task, scheduling retry (%d/3)...", self.request.retries + 1)
                raise self.retry(exc=Exception(message), countdown=5 * (2 ** self.request.retries))

            logger.info("Celery payment task completed for %s: %s", user_email, message)
            return {"status": "success" if success else "failed", "message": message}

        except self.MaxRetriesExceededError:
            logger.error("Max retries exceeded for payment task %s (user: %s)", self.request.id, user_email)
            return {"status": "failed", "message": "Max retries exceeded"}
        except Exception as exc:
            if not isinstance(exc, self.Retry):
                logger.error("Unexpected error in process_payment_task: %s", str(exc))
                raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))
            raise exc
