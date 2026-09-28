import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    LessonCandidateDB, MemoryReferenceDB, CorrectionDB, InteractionDB, SystemEventDB
)
from backend.schemas.memory_schemas import (
    ConfidenceLevel, LessonCandidate, MemoryReference, MemoryType, PromotionResult, ValidationRequest
)
from backend.services.hindsight_service import hindsight_service, HindsightService

class MemoryPromotionService:
    """
    Orchestrates the Memory Promotion Pipeline:
      Raw Interaction -> Capture Correction -> Extract Lesson Candidate
      -> Human Validation Gate -> Hindsight Retain -> Memory Reference & Fleet Scope
    """

    def __init__(self, hindsight: Optional[HindsightService] = None):
        self.hindsight = hindsight or hindsight_service

    def capture_correction(
        self,
        db: Session,
        interaction_id: str,
        correction_text: str,
        original_action: str,
        human_reason: Optional[str] = None
    ) -> Tuple[CorrectionDB, LessonCandidateDB]:
        interaction = db.query(InteractionDB).filter(InteractionDB.id == interaction_id).first()
        if not interaction:
            raise ValueError(f"Interaction {interaction_id} not found.")

        # Update interaction status
        interaction.status = "corrected"

        # 1. Store Correction record
        corr_id = f"corr-{uuid.uuid4().hex[:8]}"
        correction = CorrectionDB(
            id=corr_id,
            interaction_id=interaction_id,
            correction_text=correction_text,
            original_action=original_action,
            human_reason=human_reason
        )
        db.add(correction)

        # 2. Extract Candidate Lesson
        extracted_lesson = correction_text
        if "No." in correction_text or "Do not" in correction_text:
            extracted_lesson = correction_text.replace("No.", "").strip()

        # Determine recommended fleet scope based on task
        recommended_scope = ["Billing Agent", "Support Agent", "Account Management Agent"]
        if interaction.task_type in ["shipping", "inventory"]:
            recommended_scope = ["Warehouse Operations"]

        cand_id = f"cand-{uuid.uuid4().hex[:8]}"
        candidate = LessonCandidateDB(
            id=cand_id,
            interaction_id=interaction_id,
            source_agent=interaction.agent_id,
            task_type=interaction.task_type,
            context=f"Customer tier: {interaction.customer_tier} | Prompt: {interaction.user_prompt}",
            original_action=original_action,
            correction_text=correction_text,
            extracted_lesson=extracted_lesson,
            recommended_scope=recommended_scope,
            is_validated=False,
            is_promoted=False
        )
        db.add(candidate)

        # Record system event
        event = SystemEventDB(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="lesson_candidate_created",
            agent_id=interaction.agent_id,
            details={
                "candidate_id": cand_id,
                "interaction_id": interaction_id,
                "lesson": extracted_lesson
            }
        )
        db.add(event)
        db.commit()

        return correction, candidate

    def validate_and_promote_lesson(
        self,
        db: Session,
        validation: ValidationRequest
    ) -> PromotionResult:
        candidate = db.query(LessonCandidateDB).filter(
            LessonCandidateDB.id == validation.candidate_id
        ).first()

        if not candidate:
            raise ValueError(f"Candidate {validation.candidate_id} not found.")
        if candidate.is_promoted:
            raise ValueError(f"Candidate {validation.candidate_id} has already been promoted to the fleet.")

        # Update candidate state
        candidate.is_validated = True
        candidate.extracted_lesson = validation.confirmed_lesson
        candidate.recommended_scope = validation.target_scope

        # Retain into genuine Hindsight memory
        # Tags allow routing & filtering within Hindsight
        memory_tags = ["learnmesh", "shared_lesson", candidate.task_type] + [
            f"agent:{agent.lower().replace(' ', '_')}" for agent in validation.target_scope
        ]

        hindsight_content = (
            f"Organizational Lesson: {validation.confirmed_lesson}. "
            f"Context: {candidate.context}. "
            f"Original Action: {candidate.original_action}. "
            f"Correction Reason: {candidate.correction_text}"
        )

        hindsight_metadata = {
            "source_agent": candidate.source_agent,
            "task_type": candidate.task_type,
            "candidate_id": candidate.id,
            "interaction_id": candidate.interaction_id
        }

        memory_id = f"HM-{uuid.uuid4().hex[:6].upper()}"

        # Call real Hindsight service
        retained = self.hindsight.retain(
            content=hindsight_content,
            context=candidate.context,
            document_id=memory_id,
            metadata=hindsight_metadata,
            tags=memory_tags
        )

        # Record memory reference
        mem_ref = MemoryReferenceDB(
            memory_id=retained.memory_id,
            hindsight_id=retained.memory_id,
            memory_type="SHARED_LESSON",
            source_agent=candidate.source_agent,
            task_type=candidate.task_type,
            context=candidate.context,
            lesson=validation.confirmed_lesson,
            scope=validation.target_scope,
            confidence_level="Limited",
            confidence_score=0.40,
            supporting_outcomes_count=0,
            contradicting_outcomes_count=0,
            candidate_id=candidate.id,
            last_reinforced_at=datetime.now(timezone.utc)
        )
        db.add(mem_ref)

        candidate.is_promoted = True
        candidate.promoted_memory_id = retained.memory_id

        all_known_agents = ["Billing Agent", "Support Agent", "Account Management Agent", "Warehouse Operations"]
        ineligible_agents = [a for a in all_known_agents if a not in validation.target_scope]

        # Record system event
        event = SystemEventDB(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="memory_retain_completed",
            agent_id=candidate.source_agent,
            memory_id=retained.memory_id,
            details={
                "candidate_id": candidate.id,
                "lesson": validation.confirmed_lesson,
                "eligible_agents": validation.target_scope,
                "ineligible_agents": ineligible_agents
            }
        )
        db.add(event)
        db.commit()

        return PromotionResult(
            candidate_id=candidate.id,
            memory_id=retained.memory_id,
            hindsight_id=retained.memory_id,
            lesson=validation.confirmed_lesson,
            eligible_agents=validation.target_scope,
            ineligible_agents=ineligible_agents,
            confidence_level=ConfidenceLevel.LIMITED
        )
