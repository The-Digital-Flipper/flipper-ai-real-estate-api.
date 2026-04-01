from celery import Celery
from app.config import settings

celery_app = Celery(
    "flipper_ai",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)
celery_app.conf.beat_schedule = {
    "run-ingestion-every-hour": {
        "task": "app.workers.tasks.run_ingestion_task",
        "schedule": 3600.0,
    },
    "run-matching-every-30-min": {
        "task": "app.workers.tasks.run_matching_task",
        "schedule": 1800.0,
    },
    "run-scraper-every-4-hours": {
        "task": "app.workers.tasks.run_scraper_task",
        "schedule": 14400.0,
    },
}
celery_app.conf.timezone = "UTC"
