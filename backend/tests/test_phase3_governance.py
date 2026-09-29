import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, MemoryReferenceDB, ApprovalRequestDB, AuditLogEntryDB, ContradictionReviewDB
from backend.services.contradiction_service import ContradictionService
from backend.services.governance_service import GovernanceService
from backend.utils.exceptions import ValidationError, ResourceNotFoundError

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_contradiction_detection_layers(db_session):
    existing = [
        MemoryReferenceDB(
            memory_id="MEM-EXIST-01",
            lesson="Allow customer courtesy refund up to $50 without return label.",
            priority=60,
            state="active"
        )
    ]

    # Layer 1: Heuristic opposing directives (allow vs never allow)
    cand1 = "Never allow customer courtesy refund without return label."
    conflicts1 = ContradictionService.detect_contradictions(cand1, existing)
    assert len(conflicts1) >= 1
    assert conflicts1[0]["conflict_type"] == "DIRECT_OPPOSITION"
    assert conflicts1[0]["severity"] == "HIGH"

    # Layer 2: Value range divergence ($50 vs $20)
    cand2 = "Allow customer courtesy refund up to $20 without return label."
    conflicts2 = ContradictionService.detect_contradictions(cand2, existing)
    assert len(conflicts2) >= 1
    assert conflicts2[0]["conflict_type"] == "VALUE_RANGE_CONFLICT"

def test_policy_precedence_arbitration():
    # Legal/Compliance (100) vs Agent Tip (40)
    winning, can_auto = ContradictionService.evaluate_precedence(100, 40)
    assert winning == "A"
    assert can_auto is True

    # Equal priority (60 vs 60) requires human governance
    winning_tie, can_auto_tie = ContradictionService.evaluate_precedence(60, 60)
    assert winning_tie == "TIE"
    assert can_auto_tie is False

def test_two_person_approval_workflow(db_session):
    # 1. Create approval request
    req = GovernanceService.create_approval_request(
        db=db_session,
        candidate_id="CAND-RISK-01",
        risk_category="CRITICAL",
        risk_score=0.85,
        requested_by="operator_01"
    )
    assert req.status == "pending"

    # 2. First approver signs off
    req, fully_appr = GovernanceService.process_approval_action(
        db=db_session,
        request_id=req.id,
        approver_id="verifier_lead_a",
        action="approve",
        note="First approval granted"
    )
    assert fully_appr is False
    assert req.status == "pending"
    assert req.approver_1 == "verifier_lead_a"

    # 3. Same approver attempts to sign second approval -> MUST be rejected by two-person rule
    with pytest.raises(ValidationError):
        GovernanceService.process_approval_action(
            db=db_session,
            request_id=req.id,
            approver_id="verifier_lead_a",
            action="approve",
            note="Trying to sign again"
        )

    # 4. Second independent approver signs off -> fully approved
    req_final, fully_appr_final = GovernanceService.process_approval_action(
        db=db_session,
        request_id=req.id,
        approver_id="compliance_director_b",
        action="approve",
        note="Second approval granted"
    )
    assert fully_appr_final is True
    assert req_final.status == "approved"
    assert req_final.approver_2 == "compliance_director_b"

def test_cryptographic_audit_log_hash_chain(db_session):
    # 1. Add entries to audit log
    e1 = GovernanceService.record_audit_entry(
        db=db_session,
        action="TEST_ACTION_1",
        actor_id="admin",
        tenant_id="default",
        target_entity="Policy",
        target_id="POL-01",
        payload={"change": "Initial policy"}
    )
    e2 = GovernanceService.record_audit_entry(
        db=db_session,
        action="TEST_ACTION_2",
        actor_id="verifier",
        tenant_id="default",
        target_entity="Policy",
        target_id="POL-01",
        payload={"change": "Updated policy"}
    )

    assert e1.sequence_number == 1
    assert e2.sequence_number == 2
    assert e2.previous_hash == e1.entry_hash

    # 2. Verify audit integrity
    verification = GovernanceService.verify_audit_integrity(db_session)
    assert verification["is_valid"] is True
    assert verification["total_entries"] == 2
    assert verification["status"] == "VERIFIED_UNBROKEN"

    # 3. Deliberately tamper with an entry in the database
    e1.entry_hash = "tampered_fake_hash_1234567890abcdef"
    db_session.commit()

    # 4. Run verification again -> MUST catch tampering and identify broken sequence
    tampered_verification = GovernanceService.verify_audit_integrity(db_session)
    assert tampered_verification["is_valid"] is False
    assert tampered_verification["broken_at_sequence"] == 1
