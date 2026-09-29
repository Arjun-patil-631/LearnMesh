import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, MemoryReferenceDB, MemoryVersionDB, MemoryTransitionAuditDB
from backend.schemas.enterprise_schemas import MemoryState
from backend.services.confidence_service import ConfidenceService
from backend.services.lifecycle_service import LifecycleService
from backend.services.generalization_service import GeneralizationService
from backend.services.scope_evaluator import ScopeEvaluator
from backend.utils.exceptions import ValidationError, ResourceNotFoundError

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_confidence_2_formulation():
    # 1. Admin verification with 0 outcomes
    score_admin, level_admin, b_admin = ConfidenceService.calculate_confidence(
        verifier_role="admin", supporting_count=0, contradiction_count=0, base_score=0.50
    )
    assert score_admin >= 0.50
    assert b_admin.verifier_weight == 1.0

    # 2. Junior verification (lower weight)
    score_jr, level_jr, b_jr = ConfidenceService.calculate_confidence(
        verifier_role="junior_verifier", supporting_count=0, contradiction_count=0, base_score=0.50
    )
    assert score_jr < score_admin
    assert b_jr.verifier_weight == 0.50

    # 3. Logarithmic supporting outcomes bonus
    score_s1, _, b_s1 = ConfidenceService.calculate_confidence(
        verifier_role="verifier", supporting_count=1, contradiction_count=0, base_score=0.40
    )
    score_s10, _, b_s10 = ConfidenceService.calculate_confidence(
        verifier_role="verifier", supporting_count=10, contradiction_count=0, base_score=0.40
    )
    assert score_s10 > score_s1
    assert b_s10.supporting_bonus <= 0.35

    # 4. Contradiction penalty
    score_c, _, b_c = ConfidenceService.calculate_confidence(
        verifier_role="verifier", supporting_count=5, contradiction_count=2, base_score=0.40
    )
    assert b_c.contradiction_penalty == 0.40
    assert score_c < score_s10

    # 5. Human-readable explanation verification
    assert "Confidence" in b_admin.explanation
    assert "scaled by" in b_admin.explanation

def test_lifecycle_state_machine_and_versioning(db_session):
    mem = MemoryReferenceDB(
        memory_id="MEM-LIFE-01",
        hindsight_id="HM-01",
        memory_type="SHARED_LESSON",
        source_agent="Billing Agent",
        task_type="refund",
        context="Enterprise refund edge case",
        lesson="Refunds require manager sign-off.",
        scope=["Billing Agent", "Support Agent"],
        state=MemoryState.CANDIDATE.value,
        version=1,
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(mem)
    db_session.commit()

    # 1. Valid transition: candidate -> verified
    LifecycleService.transition_state(db_session, mem.memory_id, MemoryState.VERIFIED)
    assert mem.state == MemoryState.VERIFIED.value

    # 2. Valid transition: verified -> active
    LifecycleService.transition_state(db_session, mem.memory_id, MemoryState.ACTIVE)
    assert mem.state == MemoryState.ACTIVE.value

    # 3. Invalid transition: active -> candidate should raise ValidationError
    with pytest.raises(ValidationError):
        LifecycleService.transition_state(db_session, mem.memory_id, MemoryState.CANDIDATE)

    # 4. Check transition audit entry
    audits = db_session.query(MemoryTransitionAuditDB).filter(
        MemoryTransitionAuditDB.memory_id == mem.memory_id
    ).all()
    assert len(audits) >= 2

    # 5. Version snapshot & diff creation
    ver2 = LifecycleService.create_version_snapshot(
        db=db_session,
        memory=mem,
        new_lesson="Refunds strictly require return label and manager sign-off.",
        changed_by="compliance_lead",
        change_reason="Policy hardening"
    )
    assert ver2.version_number == 2
    assert mem.version == 2
    assert "return label" in ver2.diff_summary

    # 6. Rollback to version 1
    # Create snapshot for version 1 first
    LifecycleService.create_version_snapshot(
        db=db_session,
        memory=mem,
        new_lesson="Refunds require manager sign-off.",
        changed_by="admin",
        change_reason="Test rollback",
        is_initial=True
    )
    ver_v1 = db_session.query(MemoryVersionDB).filter(
        MemoryVersionDB.memory_id == mem.memory_id,
        MemoryVersionDB.version_number == 1
    ).first()
    assert ver_v1 is not None

    rolled_back = LifecycleService.rollback_memory(db_session, mem.memory_id, 1, actor_id="admin")
    assert rolled_back.lesson == "Refunds require manager sign-off."

def test_staleness_checker(db_session):
    # Stale memory (older than 90 days)
    old_time = datetime.now(timezone.utc) - timedelta(days=120)
    mem = MemoryReferenceDB(
        memory_id="MEM-STALE-01",
        hindsight_id="HM-STALE-01",
        memory_type="SHARED_LESSON",
        source_agent="Support Agent",
        task_type="support",
        context="Support procedure documentation",
        lesson="Old support procedure",
        state=MemoryState.ACTIVE.value,
        created_at=old_time,
        last_reinforced_at=old_time
    )
    db_session.add(mem)
    db_session.commit()

    flagged = LifecycleService.check_and_flag_stale_memories(db_session, inactivity_days=90)
    assert len(flagged) >= 1
    assert flagged[0].memory_id == "MEM-STALE-01"
    assert flagged[0].is_stale is True
    assert flagged[0].state == MemoryState.UNDER_REVIEW.value

def test_generalization_service():
    correction = "Never refund enterprise customers without an active return label and manager approval."
    analysis = GeneralizationService.analyze_correction(
        correction_text=correction,
        context="Acme Corp refund request",
        task_type="refund",
        original_action="refund_approved"
    )
    assert analysis["is_negative_guardrail"] is True
    assert analysis["risk_level"] in ["HIGH", "CRITICAL"]
    assert analysis["requires_two_person_approval"] is True
    assert "Billing Agent" in analysis["suggested_scope"]
    assert "Support Agent" in analysis["suggested_scope"]
    assert "All customer financial concessions" in analysis["generalized_rule"]

def test_scope_evaluator():
    mem = MemoryReferenceDB(
        memory_id="MEM-SCOPE-01",
        state="active",
        tenant_id="default",
        department="Finance",
        scope=["Billing Agent", "Support Agent"],
        customer_segments=["enterprise", "premium"],
        conditions={"max_amount": 100.0},
        priority=80
    )

    # 1. Matching scenario
    app, reason = ScopeEvaluator.is_memory_applicable(
        memory=mem,
        agent_name="Billing Agent",
        customer_tier="enterprise",
        tenant_id="default",
        department="Finance",
        context_payload={"amount": 50.0}
    )
    assert app is True
    assert "priority: 80" in reason

    # 2. Scope mismatch (Warehouse Operations not in scope)
    app2, reason2 = ScopeEvaluator.is_memory_applicable(
        memory=mem,
        agent_name="Warehouse Operations",
        customer_tier="enterprise",
        tenant_id="default"
    )
    assert app2 is False
    assert "not in memory scope" in reason2

    # 3. Customer tier mismatch (standard tier vs enterprise)
    app3, reason3 = ScopeEvaluator.is_memory_applicable(
        memory=mem,
        agent_name="Billing Agent",
        customer_tier="standard",
        tenant_id="default"
    )
    assert app3 is False
    assert "not in allowed segments" in reason3

    # 4. Amount threshold exceeded
    app4, reason4 = ScopeEvaluator.is_memory_applicable(
        memory=mem,
        agent_name="Billing Agent",
        customer_tier="enterprise",
        tenant_id="default",
        department="Finance",
        context_payload={"amount": 250.0}
    )
    assert app4 is False
    assert "exceeds maximum threshold" in reason4
