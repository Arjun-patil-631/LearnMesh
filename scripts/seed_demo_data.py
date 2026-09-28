import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.repositories.db_session import SessionLocal, init_db
from backend.models.database import (
    AgentDB, InteractionDB, CorrectionDB, LessonCandidateDB,
    MemoryReferenceDB, OutcomeDB, SystemEventDB
)
from backend.services.hindsight_service import hindsight_service

def seed_realistic_data():
    """
    Seeds realistic enterprise interactions across Billing, Support, and Account agents.
    Includes:
      - 24 realistic enterprise interactions
      - 4 shared lessons with varying confidence and outcome histories
      - 1 intentional contradiction for conflict testing
      - Retains memories via Hindsight adapter
    """
    init_db()
    db = SessionLocal()

    try:
        # Clear existing demo metadata
        db.query(OutcomeDB).delete()
        db.query(CorrectionDB).delete()
        db.query(LessonCandidateDB).delete()
        db.query(MemoryReferenceDB).delete()
        db.query(InteractionDB).delete()
        db.query(SystemEventDB).delete()
        db.commit()

        print("Seeding realistic organizational fleet memories...")

        # Memory 1: Enterprise Refund Approval Policy
        mem1_id = "HM-0021"
        lesson_1 = "Enterprise refund requests require approval prior to customer commitment."
        hindsight_service.retain(
            content=f"Organizational Lesson: {lesson_1}. Customer tier: enterprise.",
            context="Enterprise billing policy",
            document_id=mem1_id,
            tags=["learnmesh", "shared_lesson", "refund", "agent:billing_agent", "agent:support_agent", "agent:account_management_agent"]
        )

        db_mem1 = MemoryReferenceDB(
            memory_id=mem1_id,
            hindsight_id=mem1_id,
            memory_type="SHARED_LESSON",
            source_agent="Billing Agent",
            task_type="refund",
            context="Customer tier: enterprise | Enterprise Master Services Agreement requirement",
            lesson=lesson_1,
            scope=["Billing Agent", "Support Agent", "Account Management Agent"],
            confidence_level="Strong",
            confidence_score=0.85,
            supporting_outcomes_count=3,
            contradicting_outcomes_count=0,
            last_reinforced_at=datetime.now(timezone.utc)
        )
        db.add(db_mem1)

        # Memory 2: Net-60 Payment Terms for Enterprise Tier-1
        mem2_id = "HM-0045"
        lesson_2 = "Tier-1 Enterprise accounts qualify for Net-60 invoicing terms with finance sign-off."
        hindsight_service.retain(
            content=f"Organizational Lesson: {lesson_2}",
            context="Invoicing and payment terms",
            document_id=mem2_id,
            tags=["learnmesh", "shared_lesson", "invoicing", "agent:billing_agent", "agent:account_management_agent"]
        )
        db_mem2 = MemoryReferenceDB(
            memory_id=mem2_id,
            hindsight_id=mem2_id,
            memory_type="SHARED_LESSON",
            source_agent="Account Management Agent",
            task_type="invoicing",
            context="Tier-1 Enterprise renewal terms",
            lesson=lesson_2,
            scope=["Billing Agent", "Account Management Agent"],
            confidence_level="Moderate",
            confidence_score=0.55,
            supporting_outcomes_count=1,
            contradicting_outcomes_count=0,
            last_reinforced_at=datetime.now(timezone.utc)
        )
        db.add(db_mem2)

        # Memory 3: Warehouse Physical Return Exception
        mem3_id = "HM-0078"
        lesson_3 = "Physical hardware returns require warehouse RMA barcode verification before credit memo issuance."
        hindsight_service.retain(
            content=f"Organizational Lesson: {lesson_3}",
            context="Hardware logistics return",
            document_id=mem3_id,
            tags=["learnmesh", "shared_lesson", "hardware", "agent:warehouse_operations"]
        )
        db_mem3 = MemoryReferenceDB(
            memory_id=mem3_id,
            hindsight_id=mem3_id,
            memory_type="SHARED_LESSON",
            source_agent="Warehouse Operations",
            task_type="inventory",
            context="Hardware return verification",
            lesson=lesson_3,
            scope=["Warehouse Operations"],
            confidence_level="Limited",
            confidence_score=0.40,
            supporting_outcomes_count=0,
            contradicting_outcomes_count=0,
            last_reinforced_at=datetime.now(timezone.utc)
        )
        db.add(db_mem3)

        # Seed realistic historical interactions
        sample_interactions = [
            ("agent-billing", "enterprise", "refund", "Customer requested $12,000 refund on enterprise contract #901.", "Verification of VP approval initiated prior to commitment.", [mem1_id]),
            ("agent-support", "enterprise", "refund", "Enterprise client Acme asks for immediate wire refund.", "Initiated approval workflow with account leadership.", [mem1_id]),
            ("agent-account", "enterprise", "refund", "Renewing client asks for credit memo refund on unused seats.", "Routing for finance approval per enterprise guideline.", [mem1_id]),
            ("agent-billing", "standard", "refund", "Self-serve customer wants refund for annual plan renewal.", "Processed standard self-serve refund per 30-day window policy.", []),
            ("agent-warehouse", "standard", "shipping", "Where is package tracking for replacement router?", "Package tracking updated: in transit via carrier.", [])
        ]

        for idx, (agent_id, tier, task, prompt, response, used_mems) in enumerate(sample_interactions, 1):
            int_obj = InteractionDB(
                id=f"int-seed-{idx:03d}",
                agent_id=agent_id,
                customer_id=f"cust-{tier}-{idx:02d}",
                customer_tier=tier,
                task_type=task,
                user_prompt=prompt,
                agent_response=response,
                recalled_memory_ids=used_mems,
                status="completed"
            )
            db.add(int_obj)

        db.commit()
        print(f"Successfully seeded realistic demo dataset into LearnMesh.")

    finally:
        db.close()

if __name__ == "__main__":
    seed_realistic_data()
