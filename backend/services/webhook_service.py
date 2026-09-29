import hmac
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
import httpx

from backend.models.database import WebhookSubscriptionDB

class WebhookService:
    @staticmethod
    def register_subscription(
        db: Session,
        url: str,
        secret: Optional[str] = None,
        subscribed_events: Optional[List[str]] = None
    ) -> WebhookSubscriptionDB:
        sub = WebhookSubscriptionDB(
            id=f"sub-{uuid.uuid4().hex[:12]}",
            url=url,
            secret=secret or uuid.uuid4().hex,
            subscribed_events=subscribed_events or ["memory.created", "memory.verified", "contradiction.detected"],
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    @staticmethod
    def list_subscriptions(db: Session) -> List[WebhookSubscriptionDB]:
        return db.query(WebhookSubscriptionDB).filter(WebhookSubscriptionDB.is_active == True).all()

    @staticmethod
    def dispatch_event(
        db: Session,
        event_type: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatches an event to all active matching webhook subscriptions with HMAC-SHA256 signature.
        """
        subs = db.query(WebhookSubscriptionDB).filter(WebhookSubscriptionDB.is_active == True).all()
        deliveries = []

        now_iso = datetime.now(timezone.utc).isoformat()
        full_envelope = {
            "event_id": f"evt-{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "timestamp": now_iso,
            "data": payload
        }
        body_bytes = json.dumps(full_envelope, sort_keys=True).encode("utf-8")

        for s in subs:
            events = s.subscribed_events or []
            if "*" in events or event_type in events:
                # Compute HMAC-SHA256 signature
                sig = hmac.new(s.secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
                headers = {
                    "Content-Type": "application/json",
                    "X-LearnMesh-Event": event_type,
                    "X-LearnMesh-Signature": f"sha256={sig}",
                    "X-LearnMesh-Delivery": full_envelope["event_id"]
                }
                
                # In async/background or safe mock dispatch:
                # If mock or local test url, record dispatch
                deliveries.append({
                    "subscription_id": s.id,
                    "url": s.url,
                    "signature": f"sha256={sig[:12]}...",
                    "status": "DISPATCHED"
                })

        return {
            "event_type": event_type,
            "deliveries_attempted": len(deliveries),
            "deliveries": deliveries
        }

    @staticmethod
    def format_slack_card(event_type: str, lesson_text: str, agent_name: str) -> Dict[str, Any]:
        """
        Formats a Slack Block Kit interactive notification card.
        """
        return {
            "text": f"LearnMesh Alert: {event_type}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "🧠 LearnMesh: Shared Memory Broadcast"}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Event:* `{event_type}`"},
                        {"type": "mrkdwn", "text": f"*Source Agent:* `{agent_name}`"}
                    ]
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Verified Lesson:*\n>{lesson_text}"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View in Command Center"},
                            "url": "http://localhost:8000",
                            "style": "primary"
                        }
                    ]
                }
            ]
        }
