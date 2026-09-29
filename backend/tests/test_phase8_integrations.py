import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, WebhookSubscriptionDB, MemoryReferenceDB, LessonCandidateDB
from backend.services.webhook_service import WebhookService
from backend.services.importer_service import ImporterService
from learnmesh_sdk import LearnMeshClient

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_webhook_lifecycle_and_signatures(db_session):
    # 1. Register subscription
    sub = WebhookService.register_subscription(
        db=db_session,
        url="https://webhook.site/test-endpoint",
        secret="test_secret_key_12345",
        subscribed_events=["memory.verified", "contradiction.detected"]
    )
    assert sub.id.startswith("sub-")
    assert sub.is_active is True

    # 2. List subscriptions
    active_subs = WebhookService.list_subscriptions(db_session)
    assert len(active_subs) == 1
    assert active_subs[0].url == "https://webhook.site/test-endpoint"

    # 3. Dispatch event with HMAC signature
    dispatch_res = WebhookService.dispatch_event(
        db=db_session,
        event_type="memory.verified",
        payload={"memory_id": "HM-TEST-01", "lesson": "Test lesson"}
    )
    assert dispatch_res["deliveries_attempted"] == 1
    delivery = dispatch_res["deliveries"][0]
    assert delivery["status"] == "DISPATCHED"
    assert delivery["signature"].startswith("sha256=")

    # 4. Slack card formatter
    slack_card = WebhookService.format_slack_card("memory.verified", "Refund rule", "Billing Agent")
    assert "LearnMesh Alert" in slack_card["text"]
    assert len(slack_card["blocks"]) >= 3

def test_sop_importer(db_session):
    markdown_sop = """
# Enterprise Billing Standard Operating Procedure
## Section 1: Customer Concessions
- All concessions exceeding $100 require managerial authorization.
- Never refund software licenses after 30 days without return label.
- Customer must provide order confirmation number.
    """
    # 1. Parse markdown
    rules = ImporterService.parse_markdown_sop(markdown_sop, department="Finance")
    assert len(rules) >= 3
    guardrail_rule = next(r for r in rules if "Never refund" in r["lesson"])
    assert guardrail_rule["is_negative_guardrail"] is True

    # 2. Bulk import into database as candidates
    import_res = ImporterService.bulk_import_sop(
        db=db_session,
        content=markdown_sop,
        format_type="markdown",
        department="Finance",
        auto_verify=False
    )
    assert import_res["status"] == "IMPORT_SUCCESS"
    assert import_res["candidates_created"] >= 3

    # Verify candidates saved in DB
    cands = db_session.query(LessonCandidateDB).all()
    assert len(cands) >= 3

def test_learnmesh_client_sdk():
    # Test client instantiation and interface
    client = LearnMeshClient(base_url="http://localhost:8000", api_key="test-key", tenant_id="tenant-acme")
    assert client.base_url == "http://localhost:8000"
    assert client.tenant_id == "tenant-acme"
    client.close()
