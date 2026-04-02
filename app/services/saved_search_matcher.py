"""
Saved Search Matcher — matches newly scored deals against users' saved searches
and creates personalised ``SAVED_SEARCH_MATCH`` alerts.

How it works
------------
1. Load all ``SavedSearch`` rows that have an owning ``User``.
2. For each saved search, query ``PropertyMatch`` rows whose associated
   ``ActiveListing`` and ``DistressedProperty`` satisfy the saved search
   filters (city, state, zip_code, min_price, max_price, property_type,
   foreclosure_stage, min_deal_score).
3. Skip matches that have already produced an alert for this user + match pair.
4. Create an ``Alert(user_id=..., alert_type="SAVED_SEARCH_MATCH", ...)``
   for each qualifying match.
5. Optionally send email via ``NotificationService``.

Filter keys understood inside ``SavedSearch.filters`` (all optional)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    city                str
    state               str   (2-letter code, case-insensitive)
    zip_code            str
    min_price           float (applies to ActiveListing.list_price)
    max_price           float
    property_type       str
    foreclosure_stage   str
    min_deal_score      float (default 50)

Run cadence
-----------
Called by the ``run_saved_search_matcher_task`` Celery task after every
scraper + matching cycle (i.e., every 4 hours).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.listing import ActiveListing
from app.models.match import PropertyMatch
from app.models.property import DistressedProperty
from app.models.saved_search import SavedSearch
from app.models.user import User

logger = logging.getLogger(__name__)

# Default minimum deal score — only surfaces real opportunities
_DEFAULT_MIN_DEAL_SCORE = 50.0


def _matches_filters(
    match: PropertyMatch,
    prop: DistressedProperty,
    listing: ActiveListing,
    filters: Dict[str, Any],
) -> bool:
    """Return True if the match / listing / property satisfy all saved search filters."""
    city = filters.get("city")
    if city and city.lower() not in (listing.city or "").lower():
        return False

    state = filters.get("state")
    if state and (listing.state or "").upper() != state.upper():
        return False

    zip_code = filters.get("zip_code")
    if zip_code and listing.zip_code != zip_code:
        return False

    min_price = filters.get("min_price")
    if min_price is not None:
        price = float(prop.list_price or listing.list_price or 0)
        if price < min_price:
            return False

    max_price = filters.get("max_price")
    if max_price is not None:
        price = float(prop.list_price or listing.list_price or 0)
        if price > max_price:
            return False

    property_type = filters.get("property_type")
    if property_type and (listing.property_type or "").upper() != property_type.upper():
        return False

    foreclosure_stage = filters.get("foreclosure_stage")
    if foreclosure_stage and (prop.foreclosure_stage or "").upper() != foreclosure_stage.upper():
        return False

    min_deal_score = float(filters.get("min_deal_score", _DEFAULT_MIN_DEAL_SCORE))
    deal_score = float(match.deal_score) if match.deal_score is not None else 0.0
    if deal_score < min_deal_score:
        return False

    return True


async def run_saved_search_matching(
    db: AsyncSession,
    notify: bool = True,
) -> int:
    """
    Match recent high-score deals against all saved searches and create
    user-specific ``SAVED_SEARCH_MATCH`` alerts.

    Parameters
    ----------
    db:
        Async database session.
    notify:
        If True, attempt email delivery for each new alert via the
        configured ``NotificationService``.

    Returns
    -------
    int
        Number of new alerts created.
    """
    # Build notification service lazily (import here to avoid circular imports)
    from app.services.notification_service import build_notification_service
    from app.services.deal_analyzer import analyze_deal

    notifier = build_notification_service()

    # Load all saved searches with their users
    ss_result = await db.execute(
        select(SavedSearch).join(User, SavedSearch.user_id == User.id).where(User.is_active.is_(True))
    )
    saved_searches: List[SavedSearch] = ss_result.scalars().all()

    if not saved_searches:
        return 0

    # Load recent matches that have a deal score (exclude ones without any score)
    match_result = await db.execute(
        select(PropertyMatch)
        .where(PropertyMatch.deal_score.is_not(None))
        .order_by(PropertyMatch.matched_at.desc())
        .limit(500)
    )
    matches: List[PropertyMatch] = match_result.scalars().all()

    if not matches:
        return 0

    # Load all needed properties and listings in bulk
    prop_ids = list({str(m.distressed_property_id) for m in matches})
    listing_ids = list({str(m.active_listing_id) for m in matches})

    props_result = await db.execute(
        select(DistressedProperty).where(DistressedProperty.id.in_(prop_ids))
    )
    props_by_id: Dict[str, DistressedProperty] = {
        str(p.id): p for p in props_result.scalars().all()
    }

    listings_result = await db.execute(
        select(ActiveListing).where(ActiveListing.id.in_(listing_ids))
    )
    listings_by_id: Dict[str, ActiveListing] = {
        str(l.id): l for l in listings_result.scalars().all()
    }

    # Load users for saved searches
    user_ids = list({str(ss.user_id) for ss in saved_searches})
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id: Dict[str, User] = {str(u.id): u for u in users_result.scalars().all()}

    # Load already-created SAVED_SEARCH_MATCH alerts to avoid duplicates
    existing_result = await db.execute(
        select(Alert.user_id, Alert.match_id).where(Alert.alert_type == "SAVED_SEARCH_MATCH")
    )
    existing_pairs = {(str(r[0]), str(r[1])) for r in existing_result.fetchall() if r[0] and r[1]}

    total_new = 0
    for saved_search in saved_searches:
        filters: Dict[str, Any] = saved_search.filters or {}
        user = users_by_id.get(str(saved_search.user_id))
        if not user:
            continue

        for match in matches:
            pair = (str(user.id), str(match.id))
            if pair in existing_pairs:
                continue

            prop = props_by_id.get(str(match.distressed_property_id))
            listing = listings_by_id.get(str(match.active_listing_id))
            if not prop or not listing:
                continue

            if not _matches_filters(match, prop, listing, filters):
                continue

            # Create the alert
            score = float(match.deal_score) if match.deal_score else 0.0
            alert = Alert(
                user_id=user.id,
                match_id=match.id,
                alert_type="SAVED_SEARCH_MATCH",
                message=(
                    f"New deal matching '{saved_search.name}': "
                    f"{prop.address} — deal score {score:.0f}"
                ),
                details={
                    "saved_search_id": str(saved_search.id),
                    "saved_search_name": saved_search.name,
                    "match_id": str(match.id),
                    "deal_score": score,
                    "address": prop.address,
                    "city": listing.city,
                    "state": listing.state,
                    "zip_code": listing.zip_code,
                },
                created_at=datetime.utcnow(),
            )
            db.add(alert)
            existing_pairs.add(pair)
            total_new += 1
            logger.debug(
                "[SAVED_SEARCH] Alert for user %s, search '%s', match %s (score %.0f)",
                user.email,
                saved_search.name,
                match.id,
                score,
            )

    await db.commit()

    # Email delivery (best-effort — don't let failures raise)
    if notify and notifier.enabled and total_new > 0:
        await _deliver_saved_search_emails(db, users_by_id, notifier, analyze_deal)

    logger.info("[SAVED_SEARCH] Created %d new saved-search-match alerts", total_new)
    return total_new


async def _deliver_saved_search_emails(
    db: AsyncSession,
    users_by_id: Dict[str, User],
    notifier: Any,
    analyze_deal_fn: Any,
) -> None:
    """Send un-emailed SAVED_SEARCH_MATCH alerts to users who have email alerts enabled."""
    from app.services.notification_service import NotificationService

    for user in users_by_id.values():
        if not user.email_alerts_enabled:
            continue

        result = await db.execute(
            select(Alert)
            .where(
                Alert.user_id == user.id,
                Alert.alert_type == "SAVED_SEARCH_MATCH",
                Alert.is_read.is_(False),
            )
            .order_by(Alert.created_at.desc())
            .limit(10)
        )
        unread: List[Alert] = result.scalars().all()

        for alert in unread:
            analysis: Optional[Dict[str, Any]] = None
            if alert.match_id:
                match_result = await db.execute(
                    select(PropertyMatch).where(PropertyMatch.id == alert.match_id)
                )
                m = match_result.scalar_one_or_none()
                if m:
                    try:
                        analysis = await analyze_deal_fn(m, db)
                    except Exception:
                        pass

            await notifier.send_alert(user, alert, analysis)
