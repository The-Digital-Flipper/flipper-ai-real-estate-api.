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
    "run-census-enrichment-daily": {
        "task": "app.workers.tasks.run_census_enrichment_task",
        "schedule": 86400.0,   # once per day — Census data changes slowly
    },
    "run-fred-weekly": {
        "task": "app.workers.tasks.run_fred_task",
        "schedule": 604800.0,  # once per week — FRED publishes weekly/monthly
    },
    "run-walk-score-daily": {
        "task": "app.workers.tasks.run_walk_score_task",
        "schedule": 86400.0,   # once per day — score newly ingested properties
    },
    "run-deal-analysis-every-4-hours": {
        "task": "app.workers.tasks.run_deal_analysis_task",
        "schedule": 14400.0,   # after scrapers run, enrich matches with full analysis
    },
    "run-saved-search-matcher-every-4-hours": {
        "task": "app.workers.tasks.run_saved_search_matcher_task",
        "schedule": 14400.0,   # alert users of matches to their saved searches
    },
    "run-notifications-every-hour": {
        "task": "app.workers.tasks.run_notifications_task",
        "schedule": 3600.0,    # deliver unread alerts via email every hour
    },
}
celery_app.conf.timezone = "UTC"
