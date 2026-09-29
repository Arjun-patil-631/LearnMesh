import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, MemoryReferenceDB, AgentDB
from backend.services.agent_pipeline_service import AgentPipelineService
from backend.schemas.enterprise_schemas import ActionExecutionRequest

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_7step_pipeline_execution(db_session):
    # Populate negative guardrail memory in database
    mem = MemoryReferenceDB(
        memory_id="HM-GUARD-01",
        hindsight_id="HM-GUARD-01",
        memory_type="SHARED_LESSON",
        source_agent="Billing Agent",
        task_type="refund",
        context="Refund edge cases",
        lesson="Never refund without return label under any circumstances.",
        scope=["Billing Agent", "Support Agent"],
        state="active",
        priority=90,
        is_negative_guardrail=True,
        confidence_level="Strong",
        confidence_score=0.88,
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(mem)
    db_session.commit()

    # Execute 7-step pipeline on Support Agent
    resp, trace = AgentPipelineService.execute_7step_pipeline(
        db=db_session,
        agent_name="Support Agent",
        user_prompt="Customer wants an immediate $500 refund without sending any return label.",
        task_type="refund",
        customer_tier="enterprise"
    )

    # 1. Verify trace structure
    assert trace.total_duration_ms > 0
    assert len(trace.steps) == 7
    step_names = [s.step_name for s in trace.steps]
    assert step_names == [
        "understand", "recall", "applicability_filter", "conflict_check",
        "draft", "self_critique", "final_answer"
    ]

    # 2. Verify negative guardrail was recalled and enforced
    assert "HM-GUARD-01" in resp["used_memory_ids"]
    assert resp["requires_approval"] is True
    assert "cannot issue a refund without a verified return shipping label" in resp["agent_response"]

    # 3. Verify tool call generated with confirmation threshold requirement
    assert len(resp["tool_calls"]) >= 1
    refund_tool = resp["tool_calls"][0]
    assert refund_tool["tool_name"] == "process_refund"
    assert refund_tool["requires_confirmation"] is True

def test_tool_guardrail_thresholds(db_session):
    # 1. Action below $100 -> executes immediately
    req_small = ActionExecutionRequest(
        action_name="process_refund",
        parameters={"amount": 45.0, "reason": "damaged item"},
        mode="execute",
        agent_id="Billing Agent",
        user_confirmed=False
    )
    res_small = AgentPipelineService.execute_tool_action(db_session, req_small)
    assert res_small.status == "EXECUTED"
    assert res_small.threshold_exceeded is False

    # 2. Action above $100 without confirmation -> BLOCKED with REQUIRES_CONFIRMATION
    req_large = ActionExecutionRequest(
        action_name="process_refund",
        parameters={"amount": 450.0, "reason": "executive concession"},
        mode="execute",
        agent_id="Billing Agent",
        user_confirmed=False
    )
    res_large = AgentPipelineService.execute_tool_action(db_session, req_large)
    assert res_large.status == "REQUIRES_CONFIRMATION"
    assert res_large.threshold_exceeded is True

    # 3. Action above $100 with confirmation -> EXECUTED
    req_confirmed = ActionExecutionRequest(
        action_name="process_refund",
        parameters={"amount": 450.0, "reason": "executive concession"},
        mode="execute",
        agent_id="Billing Agent",
        user_confirmed=True
    )
    res_confirmed = AgentPipelineService.execute_tool_action(db_session, req_confirmed)
    assert res_confirmed.status == "EXECUTED"

    # 4. Simulation mode (dry_run)
    req_dry = ActionExecutionRequest(
        action_name="process_refund",
        parameters={"amount": 450.0},
        mode="dry_run",
        agent_id="Billing Agent"
    )
    res_dry = AgentPipelineService.execute_tool_action(db_session, req_dry)
    assert res_dry.status == "SIMULATED"
