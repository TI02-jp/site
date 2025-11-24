"""
Service for sending Web Push Notifications to users.
"""

import os
import json
import logging
from datetime import datetime
from app.models.tables import SAO_PAULO_TZ
from typing import List, Optional
from pywebpush import webpush, WebPushException
from app import db
from app.models.tables import PushSubscription, User

logger = logging.getLogger(__name__)


def send_push_notification(
    user_id: int,
    title: str,
    body: str,
    url: str = "/",
    notification_id: Optional[int] = None,
    icon: str = "/static/images/icon-192x192.png",
    badge: str = "/static/images/icon-192x192.png",
) -> dict:
    """
    Send a web push notification to a specific user.

    Args:
        user_id: ID of the user to send notification to
        title: Notification title
        body: Notification body text
        url: URL to open when notification is clicked
        notification_id: Optional ID of the notification in database
        icon: URL to notification icon
        badge: URL to notification badge

    Returns:
        Dictionary with success status and details
    """
    # Get VAPID keys from environment
    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
    vapid_claims_email = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:suporte@jpcontabil.com.br")

    if not vapid_private_key:
        logger.error("VAPID_PRIVATE_KEY not configured")
        return {"success": False, "error": "VAPID not configured"}

    # Get all push subscriptions for this user
    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()

    if not subscriptions:
        logger.info(f"No push subscriptions found for user {user_id}")
        return {"success": False, "error": "No subscriptions found"}

    # Prepare notification payload
    payload = {
        "title": title,
        "body": body,
        "url": url,
        "icon": icon,
        "badge": badge,
        "timestamp": None,  # Will be set by browser
    }

    if notification_id:
        payload["id"] = notification_id

    payload_json = json.dumps(payload)

    results = {
        "success": True,
        "sent": 0,
        "failed": 0,
        "errors": [],
    }

    # Send push notification to each subscription
    for subscription in subscriptions:
        try:
            subscription_info = {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh_key,
                    "auth": subscription.auth_key,
                }
            }

            # Send push notification
            webpush(
                subscription_info=subscription_info,
                data=payload_json,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_claims_email},
            )

            # Update last_used_at timestamp
            subscription.last_used_at = datetime.now(SAO_PAULO_TZ)
            results["sent"] += 1

            logger.info(f"Push notification sent successfully to user {user_id}")

        except WebPushException as e:
            logger.error(f"Push notification failed for user {user_id}: {e}")
            results["failed"] += 1
            results["errors"].append(str(e))

            # If subscription is no longer valid (410 Gone), remove it
            if e.response and e.response.status_code == 410:
                logger.info(f"Removing invalid subscription for user {user_id}")
                db.session.delete(subscription)

        except Exception as e:
            logger.error(f"Unexpected error sending push to user {user_id}: {e}")
            results["failed"] += 1
            results["errors"].append(str(e))

    # Commit changes (updated timestamps and deleted invalid subscriptions)
    try:
        db.session.commit()
    except Exception as e:
        logger.error(f"Error committing push notification updates: {e}")
        db.session.rollback()

    # Mark as failed if no notifications were sent
    if results["sent"] == 0:
        results["success"] = False

    return results


def send_push_to_multiple_users(
    user_ids: List[int],
    title: str,
    body: str,
    url: str = "/",
    notification_id: Optional[int] = None,
) -> dict:
    """
    Send a web push notification to multiple users.

    Args:
        user_ids: List of user IDs to send notification to
        title: Notification title
        body: Notification body text
        url: URL to open when notification is clicked
        notification_id: Optional ID of the notification in database

    Returns:
        Dictionary with aggregated results
    """
    total_results = {
        "success": True,
        "sent": 0,
        "failed": 0,
        "errors": [],
    }

    for user_id in user_ids:
        result = send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            url=url,
            notification_id=notification_id,
        )

        total_results["sent"] += result.get("sent", 0)
        total_results["failed"] += result.get("failed", 0)
        total_results["errors"].extend(result.get("errors", []))

    if total_results["sent"] == 0:
        total_results["success"] = False

    return total_results


def test_push_notification(user_id: int) -> dict:
    """
    Send a test push notification to verify setup.

    Args:
        user_id: ID of the user to send test notification to

    Returns:
        Dictionary with test results
    """
    return send_push_notification(
        user_id=user_id,
        title="JP Contábil - Teste",
        body="Suas notificações push estão funcionando corretamente!",
        url="/",
    )
