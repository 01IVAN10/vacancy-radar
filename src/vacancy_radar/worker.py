"""Celery application for background workers and the Beat scheduler.

Collection logic stays independent of Celery: tasks here are thin wrappers
around plain functions. Real tasks and the Beat schedule arrive in stage 4.
"""

from celery import Celery

from vacancy_radar.config import get_settings


def create_celery() -> Celery:
    """Build the Celery app with Redis as both broker and result backend."""
    redis_url = str(get_settings().redis_url)
    app = Celery("vacancy_radar", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        beat_schedule={},
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()


@celery_app.task
def ping() -> str:
    """Health probe: proves a worker can pick up and run a task."""
    return "pong"
