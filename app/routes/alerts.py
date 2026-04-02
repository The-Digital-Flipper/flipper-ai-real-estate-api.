from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.schemas.alert import AlertRead
from app.services.auth_service import get_current_user
from app.services.alert_service import get_user_alerts, mark_alert_read

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=dict)
async def get_alerts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip = (page - 1) * per_page
    alerts = await get_user_alerts(current_user.id, db, skip=skip, limit=per_page)
    return {
        "page": page,
        "per_page": per_page,
        "items": [AlertRead.model_validate(a) for a in alerts],
    }


@router.put("/{alert_id}/read", response_model=AlertRead)
async def read_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    alert = await mark_alert_read(alert_id, current_user.id, db)
    return alert
