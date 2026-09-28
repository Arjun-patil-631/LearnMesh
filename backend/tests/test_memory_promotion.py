import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, InteractionDB, AgentDB
from backend.services.memory_promotion_service import MemoryPromotionService
from backend.schemas.memory_schemas import ValidationRequest, ConfidenceLevel
from backend.services.hindsight_service import RetainedMemoryResult

@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    agent = AgentDB(id="agent-billing", name="Billing Agent", role="Billing Specialist")
    db.add(agent)
    
    interaction = InteractionDB(
        id="int-101",
        agent_id="agent-billing",
        customer_tier="enterprise",
        task_type="refund",
        user_prompt="I need a refund for Invoice #9021 immediately.",
        agent_response="Refund customer immediately."
    )
    db.add(interaction)
    db.commit()
    
    yield db
    db.close()

def test_capture_correction_and_promotion_flow(in_memory_db):
    mock_hindsight = MagicMock()
    mock_hindsight.retain.return_value = RetainedMemoryResult(
        success=True,
        bank_id="test-fleet",
        memory_id="HM-TEST123"
    )

    promo_service = MemoryPromotionService(hindsight=mock_hindsight)

    # 1. Capture correction
    correction, candidate = promo_service.capture_correction(
        db=in_memory_db,
        interaction_id="int-101",
        correction_text="Do not promise a refund for enterprise-contract customers. Approval is required.",
        original_action="Refund customer immediately.",
        human_reason="Enterprise policy requirement"
    )

    assert candidate.id.startswith("cand-")
    assert candidate.is_validated is False
    assert candidate.is_promoted is False
    assert "Approval is required" in candidate.extracted_lesson

    # 2. Validate and promote to Hindsight
    val_req = ValidationRequest(
        candidate_id=candidate.id,
        confirmed_lesson="Enterprise refund requests require approval prior to customer commitment.",
        target_scope=["Billing Agent", "Support Agent", "Account Management Agent"]
    )
    result = promo_service.validate_and_promote_lesson(db=in_memory_db, validation=val_req)

    assert result.memory_id == "HM-TEST123"
    assert result.confidence_level == ConfidenceLevel.LIMITED
    assert "Support Agent" in result.eligible_agents
    assert "Warehouse Operations" in result.ineligible_agents
    mock_hindsight.retain.assert_called_once()
