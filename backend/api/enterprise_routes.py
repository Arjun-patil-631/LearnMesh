import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header, status
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.repositories.db_session import get_db
from backend.models.database import (
    MemoryReferenceDB,
    MemoryVersionDB,
    MemoryTransitionAuditDB,
    ContradictionReviewDB,
    ApprovalRequestDB,
    AuditLogEntryDB,
    AgentActionAuditDB,
    InteractionDB,
    CorrectionDB,
    OutcomeDB,
    AgentDB
)
from backend.schemas.enterprise_schemas import (
    MemoryState,
    ContradictionResolutionRequest,
    ApprovalActionRequest,
    ActionExecutionRequest,
    ActionExecutionResult,
    WebhookSubscriptionCreate,
    SOPImportRequest,
    EvaluationSuiteResponse
)
from backend.services.confidence_service import ConfidenceService
from backend.services.lifecycle_service import LifecycleService
from backend.services.governance_service import GovernanceService
from backend.services.contradiction_service import ContradictionService
from backend.services.security_service import SecurityService
from backend.services.agent_pipeline_service import AgentPipelineService
from backend.services.evaluation_service import EvaluationService
from backend.services.webhook_service import WebhookService
from backend.services.importer_service import ImporterService
from backend.services.hindsight_service import hindsight_service
from backend.services.llm_service import llm_service
from backend.utils.exceptions import ValidationError, ResourceNotFoundError

enterprise_router = APIRouter()

# ============================================================================
# 1. MEMORY LIFECYCLE & VERSIONING
# ============================================================================

@enterprise_router.get("/memories/{memory_id}/versions")
def get_memory_versions(memory_id: str, db: Session = Depends(get_db)):
    """Retrieves immutable version history for a given memory reference."""
    versions = db.query(MemoryVersionDB).filter(
        MemoryVersionDB.memory_id == memory_id
    ).order_by(MemoryVersionDB.version_number.desc()).all()
    return versions

@enterprise_router.post("/memories/{memory_id}/rollback")
def rollback_memory_version(
    memory_id: str,
    target_version: int = Query(..., description="Target version number to rollback to"),
    reason: Optional[str] = Query(None, description="Reason for rollback"),
    actor_id: str = Header("admin", alias="X-Actor-ID"),
    db: Session = Depends(get_db)
):
    """Rolls back memory to an earlier version, creating a new immutable head version."""
    mem = LifecycleService.rollback_memory(
        db=db,
        memory_id=memory_id,
        target_version_number=target_version,
        actor_id=actor_id,
        reason=reason
    )
    GovernanceService.record_audit_entry(
        db=db,
        action="MEMORY_ROLLED_BACK",
        actor_id=actor_id,
        tenant_id=mem.tenant_id or "default",
        target_entity="MemoryReference",
        target_id=mem.memory_id,
        payload={"target_version": target_version, "new_version": mem.version, "reason": reason}
    )
    return {
        "memory_id": mem.memory_id,
        "current_version": mem.version,
        "lesson": mem.lesson,
        "message": f"Successfully rolled back to version {target_version}"
    }

@enterprise_router.post("/memories/{memory_id}/state")
def transition_memory_state(
    memory_id: str,
    target_state: MemoryState = Query(..., description="Target lifecycle state"),
    reason: Optional[str] = Query(None),
    actor_id: str = Header("admin", alias="X-Actor-ID"),
    db: Session = Depends(get_db)
):
    """Transitions a memory through the validated lifecycle state machine."""
    mem = LifecycleService.transition_state(
        db=db,
        memory_id=memory_id,
        to_state=target_state,
        actor_id=actor_id,
        reason=reason
    )
    GovernanceService.record_audit_entry(
        db=db,
        action="MEMORY_STATE_TRANSITIONED",
        actor_id=actor_id,
        tenant_id=mem.tenant_id or "default",
        target_entity="MemoryReference",
        target_id=mem.memory_id,
        payload={"new_state": mem.state, "reason": reason}
    )
    return {
        "memory_id": mem.memory_id,
        "state": mem.state,
        "updated_at": mem.updated_at
    }

@enterprise_router.get("/memories/stale/check")
def check_stale_memories(
    inactivity_days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db)
):
    """Scans fleet memories and flags stale or overdue memories into under_review state."""
    flagged = LifecycleService.check_and_flag_stale_memories(db=db, inactivity_days=inactivity_days)
    return {
        "scanned_inactivity_threshold_days": inactivity_days,
        "flagged_stale_count": len(flagged),
        "flagged_memory_ids": [m.memory_id for m in flagged]
    }

@enterprise_router.get("/memories/{memory_id}/confidence-breakdown")
def get_confidence_breakdown(memory_id: str, db: Session = Depends(get_db)):
    """Returns detailed Confidence 2.0 mathematical score breakdown and explanation."""
    mem = db.query(MemoryReferenceDB).filter(
        MemoryReferenceDB.memory_id == memory_id,
        MemoryReferenceDB.is_deleted == False
    ).first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    score, level, breakdown = ConfidenceService.calculate_confidence(
        verifier_role=mem.verifier_role or "verifier",
        supporting_count=mem.supporting_outcomes_count or 0,
        contradiction_count=mem.contradicting_outcomes_count or 0,
        created_at=mem.created_at,
        last_reinforced_at=mem.last_reinforced_at,
        base_score=0.55 if mem.is_negative_guardrail else 0.45
    )
    return breakdown

# ============================================================================
# 2. CONTRADICTIONS & GOVERNANCE
# ============================================================================

@enterprise_router.get("/governance/contradictions")
def list_contradiction_reviews(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Lists contradiction review queue with SLA timers and severity."""
    q = db.query(ContradictionReviewDB)
    if status:
        q = q.filter(ContradictionReviewDB.status == status)
    return q.order_by(desc(ContradictionReviewDB.created_at)).all()

@enterprise_router.post("/governance/contradictions/{review_id}/resolve")
def resolve_contradiction(
    review_id: str,
    req: ContradictionResolutionRequest,
    db: Session = Depends(get_db)
):
    """Resolves a detected contradiction and transitions affected memory states."""
    review = db.query(ContradictionReviewDB).filter(ContradictionReviewDB.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Contradiction review not found")

    now = datetime.now(timezone.utc)
    review.status = "RESOLVED"
    review.resolution = req.resolution
    review.justification = req.justification
    review.assigned_to = req.actor_id
    review.resolved_at = now

    # Apply resolution logic
    if req.resolution == "SUPERSEDE_A":
        LifecycleService.transition_state(db, review.memory_id_a, MemoryState.SUPERSEDED, actor_id=req.actor_id, reason=f"Superseded by {review.memory_id_b}")
    elif req.resolution == "SUPERSEDE_B":
        LifecycleService.transition_state(db, review.memory_id_b, MemoryState.SUPERSEDED, actor_id=req.actor_id, reason=f"Superseded by {review.memory_id_a}")
    elif req.resolution == "EXCEPTION_RULE":
        LifecycleService.transition_state(db, review.memory_id_a, MemoryState.ACTIVE, actor_id=req.actor_id, reason="Marked as domain exception")

    GovernanceService.record_audit_entry(
        db=db,
        action="CONTRADICTION_RESOLVED",
        actor_id=req.actor_id,
        tenant_id="default",
        target_entity="ContradictionReview",
        target_id=review_id,
        payload={"resolution": req.resolution, "justification": req.justification}
    )
    db.commit()
    return {"status": "RESOLVED", "review_id": review_id, "resolution": req.resolution}

@enterprise_router.get("/governance/approvals")
def list_approval_requests(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Lists two-person approval requests."""
    q = db.query(ApprovalRequestDB)
    if status:
        q = q.filter(ApprovalRequestDB.status == status)
    return q.order_by(desc(ApprovalRequestDB.created_at)).all()

@enterprise_router.post("/governance/approvals/{request_id}/action")
def act_on_approval_request(
    request_id: str,
    action_req: ApprovalActionRequest,
    db: Session = Depends(get_db)
):
    """Processes step in two-person approval workflow."""
    req, fully_approved = GovernanceService.process_approval_action(
        db=db,
        request_id=request_id,
        approver_id=action_req.approver_id,
        action=action_req.action,
        note=action_req.note
    )
    return {
        "request_id": req.id,
        "status": req.status,
        "approver_1": req.approver_1,
        "approver_2": req.approver_2,
        "fully_approved": fully_approved
    }

@enterprise_router.get("/governance/audit-log")
def list_audit_log(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """Lists entries from the tamper-evident SHA-256 hash chained audit log."""
    return db.query(AuditLogEntryDB).order_by(desc(AuditLogEntryDB.sequence_number)).limit(limit).all()

@enterprise_router.get("/governance/audit-log/verify")
def verify_audit_log_integrity(db: Session = Depends(get_db)):
    """Cryptographically verifies every link in the audit log hash chain."""
    return GovernanceService.verify_audit_integrity(db)

# ============================================================================
# 3. SECURITY & COMPLIANCE
# ============================================================================

@enterprise_router.delete("/compliance/forget-customer/{customer_id}")
def forget_customer_gdpr(
    customer_id: str,
    actor_id: str = Header("dpo_admin", alias="X-Actor-ID"),
    db: Session = Depends(get_db)
):
    """GDPR Article 17 / CCPA Right to be Forgotten customer data purge."""
    return SecurityService.forget_customer(db=db, customer_id=customer_id, actor_id=actor_id)

@enterprise_router.post("/security/scan-prompt")
def scan_prompt_security(payload: Dict[str, str]):
    """Scans and redacts PII and flags prompt injection attempts."""
    text = payload.get("prompt", "")
    redacted, counts = SecurityService.redact_pii(text)
    is_injection, reason = SecurityService.detect_prompt_injection(text)
    return {
        "original_length": len(text),
        "redacted_prompt": redacted,
        "pii_redacted_counts": counts,
        "prompt_injection_detected": is_injection,
        "injection_reason": reason
    }

# ============================================================================
# 4. AGENT INTELLIGENCE & REASONING PIPELINE
# ============================================================================

@enterprise_router.post("/agents/pipeline/execute")
def execute_agent_pipeline(
    payload: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """Runs the 7-step decomposed reasoning pipeline with full step traces and guardrails."""
    agent_id = payload.get("agent_id", "Billing Agent")
    user_prompt = payload.get("user_prompt", "")
    task_type = payload.get("task_type", "refund")
    customer_tier = payload.get("customer_tier", "enterprise")
    customer_id = payload.get("customer_id", "cust-enterprise-01")

    response_dict, trace = AgentPipelineService.execute_7step_pipeline(
        db=db,
        agent_name=agent_id,
        user_prompt=user_prompt,
        task_type=task_type,
        customer_tier=customer_tier,
        customer_id=customer_id,
        hindsight_service=hindsight_service,
        llm_service=llm_service
    )

    # Persist interaction record (only columns that exist on InteractionDB;
    # action metadata is folded into execution_trace JSON to avoid schema drift)
    trace_dump = trace.model_dump()
    trace_dump["action_type"] = response_dict["action_type"]
    trace_dump["requires_approval"] = response_dict["requires_approval"]
    interaction = InteractionDB(
        id=response_dict["interaction_id"],
        agent_id=agent_id,
        user_prompt=user_prompt,
        agent_response=response_dict["agent_response"],
        task_type=task_type,
        customer_tier=customer_tier,
        customer_id=customer_id,
        recalled_memory_ids=response_dict["used_memory_ids"],
        rejected_memory_ids=response_dict["rejected_memory_ids"],
        execution_trace=trace_dump,
        tool_calls=response_dict["tool_calls"],
        reasoning_mode=response_dict["reasoning_mode"],
        tokens_used=response_dict["tokens_used"],
        cost_usd=response_dict["cost_usd"],
        status="completed",
        created_at=datetime.now(timezone.utc)
    )
    db.add(interaction)
    db.commit()

    return response_dict

@enterprise_router.post("/agents/actions/execute")
def execute_agent_action(
    req: ActionExecutionRequest,
    db: Session = Depends(get_db)
):
    """Executes or simulates agent tool actions with financial threshold confirmation guardrails."""
    return AgentPipelineService.execute_tool_action(db=db, action_req=req)

@enterprise_router.get("/agents/actions/audit")
def list_agent_action_audits(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Lists logged actions, execution modes, and threshold enforcement records."""
    return db.query(AgentActionAuditDB).order_by(desc(AgentActionAuditDB.created_at)).limit(limit).all()

# ============================================================================
# 5. EVALUATION & PROOF OF IMPACT
# ============================================================================

@enterprise_router.post("/evaluation/run-benchmarks", response_model=EvaluationSuiteResponse)
def run_evaluation_benchmarks(
    mode: str = Query("AFTER_LEARNING", description="Evaluation execution mode"),
    fast: bool = Query(False, description="Deterministic fast mode: skips live LLM calls, runs full real pipeline logic (recall, guardrails, traces). Safe for serverless timeouts."),
    db: Session = Depends(get_db)
):
    """Runs the 20-scenario golden benchmark test suite and calculates quantifiable ROI metrics."""
    return EvaluationService.run_benchmark_suite(
        db=db,
        hindsight_service=hindsight_service,
        llm_service=None if fast else llm_service,
        mode=mode
    )

@enterprise_router.get("/evaluation/regression-test/{memory_id}")
def generate_regression_test(memory_id: str, db: Session = Depends(get_db)):
    """Synthesizes an automated regression test assertion from any verified memory."""
    mem = db.query(MemoryReferenceDB).filter(
        MemoryReferenceDB.memory_id == memory_id,
        MemoryReferenceDB.is_deleted == False
    ).first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return EvaluationService.generate_regression_test_from_memory(mem)

@enterprise_router.get("/evaluation/metrics")
def get_platform_impact_metrics(db: Session = Depends(get_db)):
    """Returns live aggregated business ROI metrics (measured from real DB state)."""
    return EvaluationService.compute_live_impact_metrics(db)

# ============================================================================
# 6. INTEGRATIONS, WEBHOOKS & SOPS
# ============================================================================

@enterprise_router.post("/webhooks/subscriptions")
def register_webhook(
    req: WebhookSubscriptionCreate,
    db: Session = Depends(get_db)
):
    """Registers a new webhook URL with optional secret and subscribed events."""
    sub = WebhookService.register_subscription(
        db=db,
        url=req.url,
        secret=req.secret,
        subscribed_events=req.subscribed_events
    )
    return {
        "id": sub.id,
        "url": sub.url,
        "subscribed_events": sub.subscribed_events,
        "secret": sub.secret,
        "created_at": sub.created_at
    }

@enterprise_router.get("/webhooks/subscriptions")
def list_webhooks(db: Session = Depends(get_db)):
    """Lists all active webhook subscriptions."""
    return WebhookService.list_subscriptions(db)

@enterprise_router.post("/webhooks/test-dispatch")
def test_webhook_dispatch(
    event_type: str = Query("memory.verified"),
    db: Session = Depends(get_db)
):
    """Dispatches a test signed event across registered webhooks."""
    payload = {"test": True, "message": "Test event dispatch from LearnMesh"}
    return WebhookService.dispatch_event(db=db, event_type=event_type, payload=payload)

@enterprise_router.post("/sop/import")
def import_sop_knowledge(
    req: SOPImportRequest,
    db: Session = Depends(get_db)
):
    """Imports Standard Operating Procedures (Markdown or JSON) into memories or candidates."""
    return ImporterService.bulk_import_sop(
        db=db,
        content=req.content,
        format_type=req.format,
        department=req.department,
        target_agents=req.target_agents,
        auto_verify=req.auto_verify,
        verifier_id=req.verifier_id
    )

# ============================================================================
# 7. LINEAGE & KNOWLEDGE GRAPH
# ============================================================================

@enterprise_router.get("/analytics/lineage-graph")
def get_knowledge_lineage_graph(db: Session = Depends(get_db)):
    """
    Returns nodes and directed links for interactive graph visualization:
    Interaction -> Correction -> Lesson Candidate -> Memory -> Target Agents -> Outcomes.
    """
    nodes = []
    links = []

    # 1. Agents
    agents = db.query(AgentDB).all()
    for ag in agents:
        nodes.append({
            "id": f"agent:{ag.name}",
            "label": ag.name,
            "type": "AGENT",
            "group": "agent",
            "details": {"role": ag.role, "department": ag.department}
        })

    # 2. Memories
    memories = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.is_deleted == False).all()
    for mem in memories:
        nodes.append({
            "id": f"memory:{mem.memory_id}",
            "label": f"Memory {mem.memory_id}",
            "type": "MEMORY",
            "group": "memory",
            "details": {
                "lesson": mem.lesson,
                "confidence": mem.confidence_score,
                "state": mem.state,
                "is_negative_guardrail": mem.is_negative_guardrail
            }
        })
        # Link memory to eligible agents
        for target in (mem.scope or []):
            links.append({
                "source": f"memory:{mem.memory_id}",
                "target": f"agent:{target}",
                "relation": "EQUIPS"
            })

    # 3. Corrections & Candidates
    corrections = db.query(CorrectionDB).limit(20).all()
    for c in corrections:
        nodes.append({
            "id": f"corr:{c.id}",
            "label": f"Correction {c.id}",
            "type": "CORRECTION",
            "group": "correction",
            "details": {"text": c.sanitized_text or c.correction_text}
        })
        links.append({
            "source": f"inter:{c.interaction_id}",
            "target": f"corr:{c.id}",
            "relation": "CORRECTED_IN"
        })

    # 4. Interactions
    interactions = db.query(InteractionDB).filter(InteractionDB.is_deleted == False).limit(20).all()
    for i in interactions:
        nodes.append({
            "id": f"inter:{i.id}",
            "label": f"Interaction {i.id}",
            "type": "INTERACTION",
            "group": "interaction",
            "details": {"prompt": i.user_prompt[:50], "status": i.status}
        })
        links.append({
            "source": f"agent:{i.agent_id}",
            "target": f"inter:{i.id}",
            "relation": "EXECUTED"
        })

    return {"nodes": nodes, "links": links}
