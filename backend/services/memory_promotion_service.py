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
from backend.schemas.enterprise_schemas import MemoryState
from backend.services.hindsight_service import hindsight_service, HindsightService
from backend.services.confidence_service import ConfidenceService
from backend.services.generalization_service import GeneralizationService
from backend.services.security_service import SecurityService
from backend.services.governance_service import GovernanceService
from backend.services.lifecycle_service import LifecycleService
from backend.services.contradiction_service import ContradictionService
from backend.services.webhook_service import WebhookService

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
        human_reason: Optional[str] = None,
        corrector_id: str = "operator_admin"
    ) -> Tuple[CorrectionDB, LessonCandidateDB]:
        interaction = db.query(InteractionDB).filter(InteractionDB.id == interaction_id).first()
        if not interaction:
            raise ValueError(f"Interaction {interaction_id} not found.")

        # Update interaction status
        interaction.status = "corrected"

        # Security: PII Redaction & trust evaluation
        sanitized_text, _ = SecurityService.redact_pii(correction_text)
        trust_score = SecurityService.evaluate_corrector_trust(db, corrector_id)
        is_quarantined = trust_score < 0.40
        quarantine_reason = "LOW_TRUST_CORRECTOR" if is_quarantined else None

        # 1. Store Correction record
        corr_id = f"corr-{uuid.uuid4().hex[:8]}"
        correction = CorrectionDB(
            id=corr_id,
            interaction_id=interaction_id,
            corrector_id=corrector_id,
            correction_text=correction_text,
            sanitized_text=sanitized_text,
            original_action=original_action,
            human_reason=human_reason,
            is_quarantined=is_quarantined,
            quarantine_reason=quarantine_reason
        )
        db.add(correction)

        # 2. Generalization analysis
        analysis = GeneralizationService.analyze_correction(
            correction_text=sanitized_text,
            context=interaction.user_prompt,
            task_type=interaction.task_type,
            original_action=original_action
        )

        cand_id = f"cand-{uuid.uuid4().hex[:8]}"
        candidate = LessonCandidateDB(
            id=cand_id,
            interaction_id=interaction_id,
            source_agent=interaction.agent_id,
            task_type=interaction.task_type,
            context=f"Customer tier: {interaction.customer_tier} | Prompt: {interaction.user_prompt}",
            original_action=original_action,
            correction_text=sanitized_text,
            extracted_lesson=analysis["specific_rule"],
            specific_rule=analysis["specific_rule"],
            generalized_rule=analysis["generalized_rule"],
            is_negative_guardrail=analysis["is_negative_guardrail"],
            suggested_conditions=analysis["suggested_conditions"],
            risk_level=analysis["risk_level"],
            requires_two_person_approval=analysis["requires_two_person_approval"],
            recommended_scope=analysis["suggested_scope"],
            is_validated=False,
            is_promoted=False
        )
        db.add(candidate)

        # If high risk domain, automatically register two-person approval request
        if analysis["requires_two_person_approval"]:
            GovernanceService.create_approval_request(
                db=db,
                candidate_id=cand_id,
                risk_category=analysis["risk_level"],
                risk_score=0.85 if analysis["risk_level"] == "CRITICAL" else 0.70,
                requested_by=corrector_id
            )

        # Record system event & tamper-evident audit
        event = SystemEventDB(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="lesson_candidate_created",
            agent_id=interaction.agent_id,
            details={
                "candidate_id": cand_id,
                "interaction_id": interaction_id,
                "lesson": analysis["specific_rule"],
                "risk_level": analysis["risk_level"]
            }
        )
        db.add(event)

        GovernanceService.record_audit_entry(
            db=db,
            action="CORRECTION_CAPTURED",
            actor_id=corrector_id,
            tenant_id="default",
            target_entity="LessonCandidate",
            target_id=cand_id,
            payload={"lesson": analysis["specific_rule"], "risk_level": analysis["risk_level"]}
        )

        db.commit()

        return correction, candidate

    def validate_and_promote_lesson(
        self,
        db: Session,
        validation: ValidationRequest,
        verifier_role: str = "verifier",
        actor_id: str = "lead_admin"
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

        # Retain into Hindsight memory
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

        retained = self.hindsight.retain(
            content=hindsight_content,
            context=candidate.context,
            document_id=memory_id,
            metadata=hindsight_metadata,
            tags=memory_tags
        )

        # Confidence 2.0 Calculation
        final_score, conf_level, breakdown = ConfidenceService.calculate_confidence(
            verifier_role=verifier_role,
            supporting_count=0,
            contradiction_count=0,
            base_score=0.55 if candidate.is_negative_guardrail else 0.45
        )

        priority = 80 if candidate.is_negative_guardrail else 60
        if "compliance" in candidate.source_agent.lower():
            priority = 100

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
            confidence_level=conf_level.value,
            confidence_score=final_score,
            confidence_breakdown=breakdown.model_dump(),
            supporting_outcomes_count=0,
            contradicting_outcomes_count=0,
            candidate_id=candidate.id,
            state=MemoryState.ACTIVE.value,
            version=1,
            priority=priority,
            is_negative_guardrail=candidate.is_negative_guardrail or False,
            conditions=candidate.suggested_conditions,
            last_reinforced_at=datetime.now(timezone.utc)
        )
        db.add(mem_ref)

        candidate.is_promoted = True
        candidate.promoted_memory_id = retained.memory_id

        # Create initial immutable version snapshot
        LifecycleService.create_version_snapshot(
            db=db,
            memory=mem_ref,
            new_lesson=validation.confirmed_lesson,
            new_context=candidate.context,
            new_scope=validation.target_scope,
            new_conditions=candidate.suggested_conditions,
            changed_by=actor_id,
            change_reason="Initial validation and promotion into active fleet memory",
            is_initial=True
        )

        all_known_agents = [
            "Billing Agent", "Support Agent", "Account Management Agent",
            "Warehouse Operations", "Compliance Officer Agent",
            "Sales & Onboarding Agent", "IT Helpdesk Agent"
        ]
        ineligible_agents = [a for a in all_known_agents if a not in validation.target_scope]

        # Check for contradictions against existing fleet memories
        existing_mems = db.query(MemoryReferenceDB).filter(
            MemoryReferenceDB.memory_id != retained.memory_id,
            MemoryReferenceDB.state.in_(["active", "verified"]),
            MemoryReferenceDB.is_deleted == False
        ).all()
        detected_conflicts = ContradictionService.detect_contradictions(validation.confirmed_lesson, existing_mems)
        for conflict in detected_conflicts:
            exist_m = conflict["existing_memory"]
            ContradictionService.create_contradiction_record(
                db=db,
                memory_id_a=retained.memory_id,
                memory_id_b=exist_m.memory_id,
                conflict_type=conflict["conflict_type"],
                severity=conflict["severity"],
                evidence=conflict["evidence"]
            )

        # Record system event, audit log, and dispatch webhook
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

        GovernanceService.record_audit_entry(
            db=db,
            action="MEMORY_PROMOTED",
            actor_id=actor_id,
            tenant_id="default",
            target_entity="MemoryReference",
            target_id=retained.memory_id,
            payload={"lesson": validation.confirmed_lesson, "scope": validation.target_scope}
        )

        WebhookService.dispatch_event(
            db=db,
            event_type="memory.verified",
            payload={"memory_id": retained.memory_id, "lesson": validation.confirmed_lesson, "scope": validation.target_scope}
        )

        db.commit()

        return PromotionResult(
            candidate_id=candidate.id,
            memory_id=retained.memory_id,
            hindsight_id=retained.memory_id,
            lesson=validation.confirmed_lesson,
            eligible_agents=validation.target_scope,
            ineligible_agents=ineligible_agents,
            confidence_level=conf_level
        )

memory_promotion_service = MemoryPromotionService()

