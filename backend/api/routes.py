import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.repositories.db_session import get_db
from backend.models.database import (
    AgentDB, InteractionDB, CorrectionDB, LessonCandidateDB,
    MemoryReferenceDB, OutcomeDB, SystemEventDB
)
from backend.schemas.api_schemas import (
    CreateInteractionRequest, InteractionResponseModel,
    CorrectionRequest, CorrectionResponse,
    OutcomeRecordRequest, OutcomeRecordResponse
)
from backend.schemas.memory_schemas import ValidationRequest, PromotionResult
from backend.services.agent_pipeline_service import agent_pipeline_service
from backend.services.memory_promotion_service import memory_promotion_service
from backend.services.applicability_service import ConfidenceCalculator, ContradictionDetector
from backend.services.hindsight_service import hindsight_service
from backend.services.llm_service import llm_service

router = APIRouter(prefix="/api", tags=["LearnMesh Core API"])

# 1. System Status
@router.get("/system/status")
def get_system_status(db: Session = Depends(get_db)):
    hindsight_ping = hindsight_service.ping()
    groq_available = llm_service.is_available()

    total_agents = db.query(AgentDB).count()
    total_memories = db.query(MemoryReferenceDB).count()
    total_interactions = db.query(InteractionDB).count()

    return {
        "status": "operational",
        "hindsight": hindsight_ping,
        "groq": {
            "connected": groq_available,
            "model": llm_service.model
        },
        "database": {
            "status": "connected",
            "agents_count": total_agents,
            "memories_count": total_memories,
            "interactions_count": total_interactions
        }
    }

# 2. Agents
@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(AgentDB).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "capabilities": a.capabilities,
            "description": a.description
        }
        for a in agents
    ]

@router.get("/agents/{agent_id}")
def get_agent_details(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(AgentDB).filter(AgentDB.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

# 3. Interactions Pipeline
@router.post("/interactions", response_model=InteractionResponseModel)
def create_and_run_interaction(req: CreateInteractionRequest, db: Session = Depends(get_db)):
    try:
        interaction, agent_output, applicable_memories = agent_pipeline_service.run_interaction(
            db=db,
            agent_id=req.agent_id,
            user_prompt=req.user_prompt,
            task_type=req.task_type,
            customer_tier=req.customer_tier,
            customer_id=req.customer_id
        )
        agent = db.query(AgentDB).filter(AgentDB.id == req.agent_id).first()
        return InteractionResponseModel(
            interaction_id=interaction.id,
            agent_id=interaction.agent_id,
            agent_name=agent.name if agent else "Unknown Agent",
            status=interaction.status,
            user_prompt=interaction.user_prompt,
            agent_response=interaction.agent_response or "",
            action_type=agent_output.action_type,
            requires_approval=agent_output.requires_approval,
            used_memory_ids=agent_output.used_memory_ids,
            recalled_memories=applicable_memories,
            reasoning_mode=agent_output.reasoning_mode,
            created_at=interaction.created_at
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/interactions/{interaction_id}")
def get_interaction(interaction_id: str, db: Session = Depends(get_db)):
    interaction = db.query(InteractionDB).filter(InteractionDB.id == interaction_id).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return interaction

# 4. Human Correction & Lesson Promotion
@router.post("/interactions/{interaction_id}/correction", response_model=CorrectionResponse)
def capture_human_correction(
    interaction_id: str,
    req: CorrectionRequest,
    db: Session = Depends(get_db)
):
    interaction = db.query(InteractionDB).filter(InteractionDB.id == interaction_id).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")

    correction, candidate = memory_promotion_service.capture_correction(
        db=db,
        interaction_id=interaction_id,
        correction_text=req.correction_text,
        original_action=interaction.agent_response or "Unknown action",
        human_reason=req.human_reason
    )

    return CorrectionResponse(
        correction_id=correction.id,
        candidate_id=candidate.id,
        interaction_id=candidate.interaction_id,
        original_action=candidate.original_action,
        correction_text=candidate.correction_text,
        extracted_lesson=candidate.extracted_lesson,
        recommended_scope=candidate.recommended_scope
    )

@router.post("/lessons/{candidate_id}/validate")
def validate_and_promote(
    candidate_id: str,
    validation: ValidationRequest,
    db: Session = Depends(get_db)
):
    try:
        validation.candidate_id = candidate_id
        res = memory_promotion_service.validate_and_promote_lesson(db=db, validation=validation)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# 5. Outcome Recording & Reinforcement
@router.post("/interactions/{interaction_id}/outcome", response_model=OutcomeRecordResponse)
def record_outcome(
    interaction_id: str,
    req: OutcomeRecordRequest,
    db: Session = Depends(get_db)
):
    interaction = db.query(InteractionDB).filter(InteractionDB.id == interaction_id).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")

    mem_ref = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.memory_id == req.memory_id).first()
    if not mem_ref:
        raise HTTPException(status_code=404, detail="Memory reference not found")

    # Record outcome
    out_id = f"out-{uuid.uuid4().hex[:8]}"
    outcome = OutcomeDB(
        id=out_id,
        interaction_id=interaction_id,
        memory_id=req.memory_id,
        outcome_type=req.outcome_type,
        notes=req.notes
    )
    db.add(outcome)

    if req.outcome_type == "CONFIRMED_SUCCESS":
        mem_ref.supporting_outcomes_count += 1
    elif req.outcome_type == "CONTRADICTION":
        mem_ref.contradicting_outcomes_count += 1

    # Recalculate deterministic confidence
    new_score, new_level = ConfidenceCalculator.calculate(
        supporting_count=mem_ref.supporting_outcomes_count,
        contradiction_count=mem_ref.contradicting_outcomes_count
    )
    mem_ref.confidence_score = new_score
    mem_ref.confidence_level = new_level.value
    mem_ref.last_reinforced_at = datetime.now(timezone.utc)

    # Record system event
    event = SystemEventDB(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        event_type="memory_reinforced" if req.outcome_type == "CONFIRMED_SUCCESS" else "memory_contradicted",
        memory_id=mem_ref.memory_id,
        details={
            "interaction_id": interaction_id,
            "new_confidence_score": new_score,
            "new_confidence_level": new_level.value
        }
    )
    db.add(event)
    db.commit()

    return OutcomeRecordResponse(
        outcome_id=outcome.id,
        interaction_id=interaction_id,
        memory_id=mem_ref.memory_id,
        new_confidence_level=mem_ref.confidence_level,
        new_confidence_score=mem_ref.confidence_score,
        supporting_count=mem_ref.supporting_outcomes_count,
        contradiction_count=mem_ref.contradicting_outcomes_count
    )

# 6. Memory Explorer & Lineage
@router.get("/memories")
def list_memories(db: Session = Depends(get_db)):
    memories = db.query(MemoryReferenceDB).all()
    return memories

@router.get("/memory/{memory_id}")
def get_memory_detail(memory_id: str, db: Session = Depends(get_db)):
    mem = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.memory_id == memory_id).first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return mem

@router.get("/memory/{memory_id}/lineage")
def get_memory_lineage(memory_id: str, db: Session = Depends(get_db)):
    mem = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.memory_id == memory_id).first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    candidate = db.query(LessonCandidateDB).filter(LessonCandidateDB.id == mem.candidate_id).first()
    outcomes = db.query(OutcomeDB).filter(OutcomeDB.memory_id == memory_id).all()
    events = db.query(SystemEventDB).filter(SystemEventDB.memory_id == memory_id).all()

    return {
        "memory_id": mem.memory_id,
        "hindsight_id": mem.hindsight_id,
        "source_agent": mem.source_agent,
        "lesson": mem.lesson,
        "confidence_level": mem.confidence_level,
        "confidence_score": mem.confidence_score,
        "scope": mem.scope,
        "original_candidate": candidate,
        "outcomes": outcomes,
        "trace_events": events
    }

# 7. Fleet Learning & Lineage Summary
@router.get("/fleet/learning")
def get_fleet_learning_summary(db: Session = Depends(get_db)):
    memories = db.query(MemoryReferenceDB).all()
    recent_events = db.query(SystemEventDB).order_by(SystemEventDB.created_at.desc()).limit(15).all()

    total_confirmations = sum(m.supporting_outcomes_count for m in memories)
    total_contradictions = sum(m.contradicting_outcomes_count for m in memories)

    return {
        "shared_lessons_count": len(memories),
        "total_confirmations": total_confirmations,
        "total_contradictions": total_contradictions,
        "active_fleet_memories": memories,
        "recent_system_events": recent_events
    }

# 8. Repeatable Demo Reset Endpoint
@router.post("/demo/reset")
def reset_demo_state(db: Session = Depends(get_db)):
    # Clear interactions, corrections, lesson candidates, outcomes, system events, memory references
    db.query(OutcomeDB).delete()
    db.query(CorrectionDB).delete()
    db.query(LessonCandidateDB).delete()
    db.query(MemoryReferenceDB).delete()
    db.query(InteractionDB).delete()
    db.query(SystemEventDB).delete()
    db.commit()

    return {
        "status": "reset_successful",
        "message": "Demo state reset to clean baseline. Agents ready for live demonstration."
    }
