import asyncio
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.run_ingestion_task")
def run_ingestion_task():
    from app.database import AsyncSessionLocal
    from app.services.ingestion_service import detect_price_drops

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await detect_price_drops(db)
            return count

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_matching_task")
def run_matching_task():
    from app.database import AsyncSessionLocal
    from app.services.matching_engine import run_matching

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await run_matching(db)
            return count

    return asyncio.run(_run())
