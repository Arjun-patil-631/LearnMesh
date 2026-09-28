import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, AgentDB, MemoryReferenceDB
from backend.services.agent_pipeline_service import AgentPipelineService
from backend.services.memory_promotion_service import MemoryPromotionService
from backend.services.hindsight_service import RetainedMemoryResult, RecalledMemoryItem
from backend.schemas.memory_schemas import ValidationRequest

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    billing_agent = AgentDB(
        id="agent-billing",
        name="Billing Agent",
        role="Billing Specialist",
        capabilities=["billing", "refunds"]
    )
    support_agent = AgentDB(
        id="agent-support",
        name="Support Agent",
        role="Customer Support",
        capabilities=["support", "refunds"]
    )
    warehouse_agent = AgentDB(
        id="agent-warehouse",
        name="Warehouse Operations",
        role="Warehouse Logistics",
        capabilities=["shipping"]
    )
    db.add_all([billing_agent, support_agent, warehouse_agent])
    db.commit()

    yield db
    db.close()

def test_cross_agent_learning_loop(test_db):
    """
    Core non-negotiable test:
    1. Billing Agent starts without shared lesson -> promises immediate refund
    2. Human corrects Billing Agent
    3. Lesson is promoted into Hindsight
    4. Support Agent encounters identical enterprise refund situation
    5. Support Agent recalls Hindsight memory and changes behavior (requires approval)
    """
    mock_hindsight = MagicMock()
    mock_hindsight.retain.return_value = RetainedMemoryResult(
        success=True,
        bank_id="learnmesh-fleet",
        memory_id="HM-0021"
    )

    agent_service = AgentPipelineService(hindsight=mock_hindsight)
    promo_service = MemoryPromotionService(hindsight=mock_hindsight)

    # Step 1: Billing Agent produces initial recommendation (without shared memory)
    # mock recall returns empty initially
    mock_hindsight.recall.return_value = []

    int1, output1, memories1 = agent_service.run_interaction(
        db=test_db,
        agent_id="agent-billing",
        user_prompt="I need an immediate refund for invoice 9021.",
        task_type="refund",
        customer_tier="enterprise"
    )

    assert output1.action_type == "refund_approved"
    assert output1.requires_approval is False
    assert len(output1.used_memory_ids) == 0

    # Step 2: Human corrects Billing Agent
    correction, candidate = promo_service.capture_correction(
        db=test_db,
        interaction_id=int1.id,
        correction_text="Do not promise a refund for enterprise-contract customers. Approval is required.",
        original_action=output1.response_text,
        human_reason="Enterprise policy requirement"
    )

    # Step 3: Validate and promote lesson to Hindsight
    val_req = ValidationRequest(
        candidate_id=candidate.id,
        confirmed_lesson="Enterprise refund requests require approval before making that commitment.",
        target_scope=["Billing Agent", "Support Agent", "Account Management Agent"]
    )
    promo_result = promo_service.validate_and_promote_lesson(db=test_db, validation=val_req)
    assert promo_result.memory_id == "HM-0021"
    assert "Support Agent" in promo_result.eligible_agents
    assert "Warehouse Operations" in promo_result.ineligible_agents

    # Step 4: Configure mock Hindsight to return the newly retained memory
    mock_hindsight.recall.return_value = [
        RecalledMemoryItem(
            learnmesh_memory_id="HM-0021",
            hindsight_document_id="HM-0021",
            text="Enterprise refund requests require approval before making that commitment.",
            context="Customer tier: enterprise",
            tags=["learnmesh", "shared_lesson", "refund", "agent:support_agent"],
            metadata={"source_agent": "Billing Agent"}
        )
    ]

    # Step 5: Support Agent encounters enterprise refund request
    int2, output2, memories2 = agent_service.run_interaction(
        db=test_db,
        agent_id="agent-support",
        user_prompt="Customer from Acme Corp wants an immediate refund on their enterprise account.",
        task_type="refund",
        customer_tier="enterprise"
    )

    # Step 6: Verify Support Agent changed behavior!
    assert output2.action_type == "approval_required"
    assert output2.requires_approval is True
    assert "HM-0021" in output2.used_memory_ids
    assert "approval" in output2.response_text.lower()
    assert "enterprise" in output2.response_text.lower()

def test_contradiction_pipeline_flow(test_db):
    """
    Verify that when conflicting policies are recalled simultaneously,
    the pipeline flags the conflict and safely escalates rather than taking unsafe action.
    """
    mock_hindsight = MagicMock()
    mock_hindsight.recall.return_value = [
        RecalledMemoryItem(
            learnmesh_memory_id="HM-001",
            hindsight_document_id="HM-001",
            text="Require approval prior to refund commitment.",
            context="Enterprise policy",
            tags=["refund"],
            metadata={}
        ),
        RecalledMemoryItem(
            learnmesh_memory_id="HM-002",
            hindsight_document_id="HM-002",
            text="Refund customer immediately without waiting for approval.",
            context="Fast resolution policy",
            tags=["refund"],
            metadata={}
        )
    ]

    agent_service = AgentPipelineService(hindsight=mock_hindsight)

    int_result, output, memories = agent_service.run_interaction(
        db=test_db,
        agent_id="agent-support",
        user_prompt="I need a refund immediately.",
        task_type="refund",
        customer_tier="enterprise"
    )

    assert output.action_type == "escalation_required"
    assert output.requires_approval is True
    assert "conflicting" in output.response_text.lower()
    assert set(output.used_memory_ids) == {"HM-001", "HM-002"}

