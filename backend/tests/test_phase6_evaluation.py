import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, MemoryReferenceDB
from backend.services.evaluation_service import EvaluationService, BENCHMARK_SCENARIOS

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_evaluation_benchmark_suite(db_session):
    # Add negative guardrail memory for refund return labels
    mem = MemoryReferenceDB(
        memory_id="HM-BENCH-01",
        hindsight_id="HM-BENCH-01",
        memory_type="SHARED_LESSON",
        source_agent="Billing Agent",
        task_type="refund",
        context="Refund edge cases",
        lesson="Never refund without return label under any circumstances.",
        scope=["Billing Agent", "Support Agent", "Warehouse Operations"],
        state="active",
        priority=90,
        is_negative_guardrail=True,
        confidence_level="Strong",
        confidence_score=0.88,
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(mem)
    db_session.commit()

    suite_resp = EvaluationService.run_benchmark_suite(
        db=db_session,
        mode="AFTER_LEARNING"
    )

    # 1. Verify all 20 benchmark scenarios were evaluated
    assert suite_resp.total_scenarios == len(BENCHMARK_SCENARIOS)
    assert suite_resp.total_scenarios >= 20
    assert len(suite_resp.scenario_results) == suite_resp.total_scenarios

    # 2. Check compliance metrics
    assert suite_resp.compliance_rate >= 70.0
    assert suite_resp.metrics.repeat_mistake_reduction_rate >= 50.0
    assert suite_resp.metrics.estimated_hours_saved > 0
    assert suite_resp.metrics.cost_avoided_usd > 0

    # 3. Check individual scenario result
    billing_001 = next(r for r in suite_resp.scenario_results if r.scenario_id == "BILLING-001")
    assert billing_001.policy_compliant is True
    assert billing_001.negative_guardrail_passed is True

def test_regression_test_generator(db_session):
    mem = MemoryReferenceDB(
        memory_id="HM-REG-01",
        hindsight_id="HM-REG-01",
        memory_type="SHARED_LESSON",
        source_agent="Billing Agent",
        task_type="refund",
        context="Refund procedures",
        lesson="All refunds require verified return label.",
        scope=["Support Agent"],
        state="active",
        is_negative_guardrail=True
    )
    db_session.add(mem)
    db_session.commit()

    reg_test = EvaluationService.generate_regression_test_from_memory(mem)
    assert reg_test["test_id"] == "REG-HM-REG-01"
    assert reg_test["agent_target"] == "Support Agent"
    assert "return label" in reg_test["expected_assertions"]["must_include"]
