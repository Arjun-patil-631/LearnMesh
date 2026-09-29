import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc

from backend.repositories.db_session import get_db
from backend.models.database import (
    AgentDB, InteractionDB, CorrectionDB, LessonCandidateDB,
    MemoryReferenceDB, OutcomeDB, SystemEventDB, JobDB
)
from backend.schemas.api_schemas import (
    CreateInteractionRequest, InteractionResponseModel,
    CorrectionRequest, CorrectionResponse,
    OutcomeRecordRequest, OutcomeRecordResponse
)
from backend.schemas.common_schemas import PaginatedResponse
from backend.schemas.memory_schemas import ValidationRequest, PromotionResult
from backend.services.agent_pipeline_service import agent_pipeline_service
from backend.services.memory_promotion_service import memory_promotion_service
from backend.services.applicability_service import ConfidenceCalculator, ContradictionDetector
from backend.services.hindsight_service import hindsight_service
from backend.services.llm_service import llm_service
from backend.utils.resilience import hindsight_circuit_breaker, groq_circuit_breaker
from backend.utils.task_queue import task_queue
from backend.utils.idempotency import check_idempotency, store_idempotency_record
from backend.utils.pagination import apply_pagination_and_sorting
from backend.utils.metrics import (
    HTTP_REQUESTS_TOTAL, MEMORIES_RETAINED_TOTAL, MEMORIES_RECALLED_TOTAL
)

router = APIRouter(tags=["LearnMesh Core API"])

# ------------------------------------------------------------------------------
# 1. System Health, Diagnostics & Connectivity
# ------------------------------------------------------------------------------
@router.get("/system/status")
def get_system_status(db: Session = Depends(get_db)):
    hindsight_ping = hindsight_service.ping()
    groq_available = llm_service.is_available()

    total_agents = db.query(AgentDB).filter(AgentDB.is_deleted == False).count()
    total_memories = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.is_deleted == False).count()
    total_interactions = db.query(InteractionDB).filter(InteractionDB.is_deleted == False).count()

    # Determine overall health
    is_degraded = (
        hindsight_ping.get("status") != "connected"
        or hindsight_service.circuit_breaker.state.value == "OPEN"
    )

    return {
        "status": "operational",
        "degraded": is_degraded,
        "mode": "degraded_local_fallback" if is_degraded else "fully_connected",
        "hindsight": hindsight_ping,
        "groq": {
            "connected": groq_available,
            "model": llm_service.model,
            "circuit_breaker": groq_circuit_breaker.get_status()
        },
        "database": {
            "status": "connected",
            "agents_count": total_agents,
            "memories_count": total_memories,
            "interactions_count": total_interactions
        },
        "resilience": {
            "hindsight_circuit": hindsight_circuit_breaker.state.value,
            "groq_circuit": groq_circuit_breaker.state.value,
            "pending_offline_retains": len(hindsight_service.pending_retains_queue)
        }
    }

# ------------------------------------------------------------------------------
# 2. Fleet Agents
# ------------------------------------------------------------------------------
@router.get("/agents")
def list_agents(
    db: Session = Depends(get_db),
    capability: Optional[str] = Query(None, description="Filter by capability")
):
    query = db.query(AgentDB).filter(AgentDB.is_deleted == False)
    agents = query.all()
    if capability:
        agents = [a for a in agents if capability in (a.capabilities or [])]

    return [
        {
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "capabilities": a.capabilities,
            "description": a.description,
            "created_at": a.created_at.isoformat() if a.created_at else None
        }
        for a in agents
    ]

@router.get("/agents/{agent_id}")
def get_agent_details(agent_id: str, db: Session = Depends(get_db)):
    agent = db.query(AgentDB).filter(AgentDB.id == agent_id, AgentDB.is_deleted == False).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

# ------------------------------------------------------------------------------
# 3. Interactions Pipeline
# ------------------------------------------------------------------------------
@router.post("/interactions", response_model=InteractionResponseModel)
def create_and_run_interaction(
    req: CreateInteractionRequest,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    # Idempotency check
    cached = check_idempotency(db, idempotency_key, "/interactions", req.model_dump())
    if cached:
        return cached

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
        response_model = InteractionResponseModel(
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

        MEMORIES_RECALLED_TOTAL.inc(len(applicable_memories))

        # Store idempotency record if requested
        if idempotency_key:
            store_idempotency_record(
                db=db,
                idempotency_key=idempotency_key,
                request_path="/interactions",
                body_dict=req.model_dump(),
                status_code=200,
                response_body=response_model
            )

        return response_model
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/interactions/{interaction_id}")
def get_interaction(interaction_id: str, db: Session = Depends(get_db)):
    interaction = db.query(InteractionDB).filter(
        InteractionDB.id == interaction_id,
        InteractionDB.is_deleted == False
    ).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return interaction

@router.get("/interactions")
def list_interactions(
    db: Session = Depends(get_db),
    agent_id: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    customer_tier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100)
):
    query = db.query(InteractionDB).filter(InteractionDB.is_deleted == False)
    if agent_id:
        query = query.filter(InteractionDB.agent_id == agent_id)
    if task_type:
        query = query.filter(InteractionDB.task_type == task_type)
    if customer_tier:
        query = query.filter(InteractionDB.customer_tier == customer_tier)
    if status:
        query = query.filter(InteractionDB.status == status)

    if page_size is not None:
        items, total = apply_pagination_and_sorting(query, InteractionDB, page=page, page_size=page_size)
        return PaginatedResponse.create(items=jsonable_encoder(items), total=total, page=page, page_size=page_size)

    return query.order_by(desc(InteractionDB.created_at)).all()

# ------------------------------------------------------------------------------
# 4. Human Correction & Lesson Promotion
# ------------------------------------------------------------------------------
@router.post("/interactions/{interaction_id}/correction", response_model=CorrectionResponse)
def capture_human_correction(
    interaction_id: str,
    req: CorrectionRequest,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    cached = check_idempotency(db, idempotency_key, f"/interactions/{interaction_id}/correction", req.model_dump())
    if cached:
        return cached

    interaction = db.query(InteractionDB).filter(
        InteractionDB.id == interaction_id,
        InteractionDB.is_deleted == False
    ).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")

    correction, candidate = memory_promotion_service.capture_correction(
        db=db,
        interaction_id=interaction_id,
        correction_text=req.correction_text,
        original_action=interaction.agent_response or "Unknown action",
        human_reason=req.human_reason
    )

    resp = CorrectionResponse(
        correction_id=correction.id,
        candidate_id=candidate.id,
        interaction_id=candidate.interaction_id,
        original_action=candidate.original_action,
        correction_text=candidate.correction_text,
        extracted_lesson=candidate.extracted_lesson,
        recommended_scope=candidate.recommended_scope
    )

    if idempotency_key:
        store_idempotency_record(
            db=db,
            idempotency_key=idempotency_key,
            request_path=f"/interactions/{interaction_id}/correction",
            body_dict=req.model_dump(),
            status_code=200,
            response_body=resp
        )

    return resp

@router.post("/lessons/{candidate_id}/validate")
def validate_and_promote(
    candidate_id: str,
    validation: ValidationRequest,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    cached = check_idempotency(db, idempotency_key, f"/lessons/{candidate_id}/validate", validation.model_dump())
    if cached:
        return cached

    try:
        validation.candidate_id = candidate_id
        res = memory_promotion_service.validate_and_promote_lesson(db=db, validation=validation)
        MEMORIES_RETAINED_TOTAL.labels(mode="live").inc()

        if idempotency_key:
            store_idempotency_record(
                db=db,
                idempotency_key=idempotency_key,
                request_path=f"/lessons/{candidate_id}/validate",
                body_dict=validation.model_dump(),
                status_code=200,
                response_body=res
            )

        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ------------------------------------------------------------------------------
# 5. Outcome Recording & Reinforcement
# ------------------------------------------------------------------------------
@router.post("/interactions/{interaction_id}/outcome", response_model=OutcomeRecordResponse)
def record_outcome(
    interaction_id: str,
    req: OutcomeRecordRequest,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    cached = check_idempotency(db, idempotency_key, f"/interactions/{interaction_id}/outcome", req.model_dump())
    if cached:
        return cached

    interaction = db.query(InteractionDB).filter(
        InteractionDB.id == interaction_id,
        InteractionDB.is_deleted == False
    ).first()
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")

    mem_ref = db.query(MemoryReferenceDB).filter(
        MemoryReferenceDB.memory_id == req.memory_id,
        MemoryReferenceDB.is_deleted == False
    ).first()
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

    resp = OutcomeRecordResponse(
        outcome_id=outcome.id,
        interaction_id=interaction_id,
        memory_id=mem_ref.memory_id,
        new_confidence_level=mem_ref.confidence_level,
        new_confidence_score=mem_ref.confidence_score,
        supporting_count=mem_ref.supporting_outcomes_count,
        contradiction_count=mem_ref.contradicting_outcomes_count
    )

    if idempotency_key:
        store_idempotency_record(
            db=db,
            idempotency_key=idempotency_key,
            request_path=f"/interactions/{interaction_id}/outcome",
            body_dict=req.model_dump(),
            status_code=200,
            response_body=resp
        )

    return resp

# ------------------------------------------------------------------------------
# 6. Memory Explorer, Pagination & Lineage
# ------------------------------------------------------------------------------
@router.get("/memories")
def list_memories(
    db: Session = Depends(get_db),
    task_type: Optional[str] = Query(None),
    confidence_level: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=100)
):
    query = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.is_deleted == False)

    if task_type:
        query = query.filter(MemoryReferenceDB.task_type == task_type)
    if confidence_level:
        query = query.filter(MemoryReferenceDB.confidence_level == confidence_level)
    if search:
        query = query.filter(
            (MemoryReferenceDB.lesson.ilike(f"%{search}%")) |
            (MemoryReferenceDB.context.ilike(f"%{search}%"))
        )

    # Return paginated format if page_size is specified
    if page_size is not None:
        items, total = apply_pagination_and_sorting(
            query=query,
            model=MemoryReferenceDB,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            order=order
        )
        return PaginatedResponse.create(items=jsonable_encoder(items), total=total, page=page, page_size=page_size)

    # Default to raw list for backward compatibility with frontend and tests
    if sort_by and hasattr(MemoryReferenceDB, sort_by):
        col = getattr(MemoryReferenceDB, sort_by)
        query = query.order_by(desc(col) if order.lower() == "desc" else asc(col))
    else:
        query = query.order_by(desc(MemoryReferenceDB.created_at))

    return query.all()

@router.get("/memory/{memory_id}")
def get_memory_detail(memory_id: str, db: Session = Depends(get_db)):
    mem = db.query(MemoryReferenceDB).filter(
        MemoryReferenceDB.memory_id == memory_id,
        MemoryReferenceDB.is_deleted == False
    ).first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return mem

@router.get("/memory/{memory_id}/lineage")
def get_memory_lineage(memory_id: str, db: Session = Depends(get_db)):
    mem = db.query(MemoryReferenceDB).filter(
        MemoryReferenceDB.memory_id == memory_id,
        MemoryReferenceDB.is_deleted == False
    ).first()
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

# ------------------------------------------------------------------------------
# 7. Fleet Learning & Lineage Summary
# ------------------------------------------------------------------------------
@router.get("/fleet/learning")
def get_fleet_learning_summary(db: Session = Depends(get_db)):
    memories = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.is_deleted == False).all()
    recent_events = (
        db.query(SystemEventDB)
        .order_by(SystemEventDB.created_at.desc())
        .limit(15)
        .all()
    )

    total_confirmations = sum(m.supporting_outcomes_count for m in memories)
    total_contradictions = sum(m.contradicting_outcomes_count for m in memories)

    return {
        "shared_lessons_count": len(memories),
        "total_confirmations": total_confirmations,
        "total_contradictions": total_contradictions,
        "active_fleet_memories": memories,
        "recent_system_events": recent_events
    }

# ------------------------------------------------------------------------------
# 8. Background Jobs & Degraded Queue Management
# ------------------------------------------------------------------------------
@router.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    status = task_queue.get_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return status

@router.post("/jobs/drain-degraded-queue")
def trigger_drain_degraded_queue():
    result = hindsight_service.replay_pending_queue()
    return {
        "status": "success",
        "result": result,
        "message": f"Replayed {result['replayed']} buffered memories into Hindsight."
    }

# ------------------------------------------------------------------------------
# 9. Repeatable Demo Reset Endpoint
# ------------------------------------------------------------------------------
@router.post("/demo/reset")
def reset_demo_state(db: Session = Depends(get_db)):
    # 1. Clear transient records
    db.query(OutcomeDB).delete()
    db.query(CorrectionDB).delete()
    db.query(LessonCandidateDB).delete()
    db.query(MemoryReferenceDB).delete()
    db.query(InteractionDB).delete()
    db.query(SystemEventDB).delete()
    db.query(JobDB).delete()
    db.commit()

    # 2. Reset Hindsight demo session namespace
    new_session_id = hindsight_service.reset_demo_session()
    hindsight_service.pending_retains_queue.clear()

    return {
        "status": "reset_successful",
        "demo_session_id": new_session_id,
        "message": "Demo state reset to clean baseline. Hindsight demo session isolated for fresh run."
    }
