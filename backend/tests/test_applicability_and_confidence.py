import pytest
from backend.services.applicability_service import (
    ConfidenceCalculator, ApplicabilityEngine, ContradictionDetector
)
from backend.schemas.memory_schemas import ConfidenceLevel

def test_confidence_evolution():
    # 1. New verified lesson
    score1, level1 = ConfidenceCalculator.calculate(supporting_count=0, contradiction_count=0)
    assert score1 == 0.40
    assert level1 == ConfidenceLevel.MODERATE or level1 == ConfidenceLevel.LIMITED

    # 2. Confirmed twice
    score2, level2 = ConfidenceCalculator.calculate(supporting_count=2, contradiction_count=0)
    assert score2 == 0.70
    assert level2 == ConfidenceLevel.STRONG

    # 3. Repeated confirmations (3x)
    score3, level3 = ConfidenceCalculator.calculate(supporting_count=3, contradiction_count=0)
    assert score3 == 0.85
    assert level3 == ConfidenceLevel.STRONG

    # 4. Contradiction occurs
    score4, level4 = ConfidenceCalculator.calculate(supporting_count=2, contradiction_count=1)
    assert score4 == 0.40  # 0.40 + 0.30 - 0.30
    assert level4 == ConfidenceLevel.MODERATE

def test_applicability_routing():
    scope = ["Billing Agent", "Support Agent", "Account Management Agent"]
    
    # Support agent should be eligible
    eval_support = ApplicabilityEngine.evaluate(
        agent_name="Support Agent",
        agent_capabilities=["support", "refunds"],
        memory_scope=scope,
        task_type="refund"
    )
    assert eval_support["is_eligible"] is True

    # Warehouse Operations should NOT be eligible
    eval_warehouse = ApplicabilityEngine.evaluate(
        agent_name="Warehouse Operations",
        agent_capabilities=["shipping", "logistics"],
        memory_scope=scope,
        task_type="refund"
    )
    assert eval_warehouse["is_eligible"] is False

def test_contradiction_detection():
    memories = [
        {"id": "HM-001", "text": "Require approval prior to refund commitment."},
        {"id": "HM-002", "text": "Refund customer immediately without waiting for approval."}
    ]
    conflicts = ContradictionDetector.detect_conflicts(memories)
    assert len(conflicts) == 2

    consistent_memories = [
        {"id": "HM-001", "text": "Require approval prior to refund commitment."},
        {"id": "HM-003", "text": "Verify VP approval for enterprise accounts."}
    ]
    no_conflicts = ContradictionDetector.detect_conflicts(consistent_memories)
    assert len(no_conflicts) == 0
