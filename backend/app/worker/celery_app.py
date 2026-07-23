import os

from celery import Celery  # type: ignore[import-untyped]

broker_url = os.environ.get("REDIS_URL", "redis://localhost:6379/15")
soft_limit = int(os.environ.get("MEDIA_TASK_SOFT_LIMIT_SECONDS", "1200"))
hard_limit = int(os.environ.get("MEDIA_TASK_HARD_LIMIT_SECONDS", "1500"))

celery_app = Celery("ecommerce-media", broker=broker_url, backend=broker_url)
celery_app.conf.update(
    task_default_queue="media",
    task_routes={"media.process": {"queue": "media"}},
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=soft_limit,
    task_time_limit=hard_limit,
    worker_max_tasks_per_child=5,
    worker_max_memory_per_child=8_388_608,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
)

celery_app.autodiscover_tasks(["app.worker"])
