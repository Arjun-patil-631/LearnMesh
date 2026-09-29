import time
import uuid
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    AgentDB,
    InteractionDB,
    MemoryReferenceDB,
    SystemEventDB,
    AgentActionAuditDB
)
from backend.schemas.enterprise_schemas import (
    PipelineStepTrace,
    ExecutionTrace,
    ActionExecutionRequest,
    ActionExecutionResult
)
from backend.schemas.reasoning_schemas import AgentReasoningInput, AgentReasoningOutput
from backend.services.hindsight_service import hindsight_service, HindsightService
from backend.services.llm_service import llm_service, LLMService
from backend.services.applicability_service import ContradictionDetector, ApplicabilityEngine
from backend.services.scope_evaluator import ScopeEvaluator
from backend.services.contradiction_service import ContradictionService
from backend.services.security_service import SecurityService
from backend.services.confidence_service import ConfidenceService

class AgentPipelineService:
    """
    Executes the full agent reasoning pipeline with both standard and
    7-step decomposed enterprise reasoning modes.
    """

    def __init__(
        self,
        hindsight: Optional[HindsightService] = None,
        llm: Optional[LLMService] = None
    ):
        self.hindsight = hindsight or hindsight_service
        self.llm = llm or llm_service

    def run_interaction(
        self,
        db: Session,
        agent_id: str,
        user_prompt: str,
        task_type: str = "refund",
        customer_tier: str = "enterprise",
        customer_id: Optional[str] = None
    ) -> Tuple[InteractionDB, AgentReasoningOutput, List[Dict[str, Any]]]:
        # Support lookup by agent_id or agent_name
        agent = db.query(AgentDB).filter(AgentDB.id == agent_id).first()
        if not agent:
            agent = db.query(AgentDB).filter(AgentDB.name == agent_id).first()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found.")

        # 1. Recall relevant memories from Hindsight
        query = f"{task_type} {customer_tier} {user_prompt}"
        recalled_items = []
        try:
            recalled_items = self.hindsight.recall(query=query)
        except Exception:
            recalled_items = []

        # If Hindsight returns empty or is offline, fall back to local database memory references
        if not recalled_items:
            local_refs = db.query(MemoryReferenceDB).filter(
                MemoryReferenceDB.task_type == task_type,
                MemoryReferenceDB.is_deleted == False
            ).all()
            for ref in local_refs:
                recalled_items.append({
                    "id": ref.memory_id,
                    "learnmesh_memory_id": ref.memory_id,
                    "hindsight_document_id": ref.memory_id,
                    "text": ref.lesson,
                    "scope": ref.scope,
                    "confidence_score": ref.confidence_score
                })

        # 2. Filter memories based on ApplicabilityEngine (scope, capability, task)
        agent_name = agent.name
        agent_caps = agent.capabilities or []
        applicable_memories = []

        for item in recalled_items:
            mem_id = getattr(item, "learnmesh_memory_id", None) or getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)
            mem_ref = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.memory_id == mem_id).first()

            if mem_ref:
                scope = mem_ref.scope or []
                eval_result = ApplicabilityEngine.evaluate(
                    agent_name=agent_name,
                    agent_capabilities=agent_caps,
                    memory_scope=scope,
                    task_type=task_type
                )
                if eval_result["is_eligible"]:
                    applicable_memories.append({
                        "id": mem_ref.memory_id,
                        "text": mem_ref.lesson,
                        "source_agent": mem_ref.source_agent,
                        "confidence_level": mem_ref.confidence_level,
                        "confidence_score": mem_ref.confidence_score,
                        "scope": mem_ref.scope,
                        "applicability_reasons": eval_result.get("reasons", [])
                    })
            else:
                scope_val = getattr(item, "scope", None) or (item.get("scope") if isinstance(item, dict) else None)
                target_scope = scope_val if isinstance(scope_val, list) else []
                eval_result = ApplicabilityEngine.evaluate(
                    agent_name=agent_name,
                    agent_capabilities=agent_caps,
                    memory_scope=target_scope,
                    task_type=task_type
                )
                if eval_result["is_eligible"]:
                    item_text = getattr(item, "text", "") if not isinstance(item, dict) else item.get("text", "")
                    applicable_memories.append({
                        "id": mem_id,
                        "text": item_text,
                        "source_agent": "Shared Fleet",
                        "confidence_level": "Limited",
                        "confidence_score": 0.40,
                        "scope": [agent_name],
                        "applicability_reasons": eval_result.get("reasons", [])
                    })

        # 3. Detect conflicting historical memories
        conflicts = ContradictionDetector.detect_conflicts(applicable_memories)
        has_conflicts = len(conflicts) > 0

        # 4. Build reasoning input
        reasoning_input = AgentReasoningInput(
            agent_id=agent.id,
            agent_name=agent.name,
            task_type=task_type,
            customer_tier=customer_tier,
            user_prompt=user_prompt,
            relevant_memories=applicable_memories,
            has_conflicts=has_conflicts,
            conflicting_memories=conflicts
        )

        # 5. Generate structured response
        agent_output = self.llm.generate_agent_response(reasoning_input)

        # 6. Save interaction
        interaction_id = f"int-{uuid.uuid4().hex[:8]}"
        interaction = InteractionDB(
            id=interaction_id,
            agent_id=agent.id,
            customer_id=customer_id or "cust-enterprise-01",
            customer_tier=customer_tier,
            task_type=task_type,
            user_prompt=user_prompt,
            agent_response=agent_output.response_text,
            recalled_memory_ids=agent_output.used_memory_ids,
            tool_calls=[{"action_type": agent_output.action_type, "requires_approval": agent_output.requires_approval}] if agent_output.action_type else [],
            status="completed"
        )
        db.add(interaction)

        # Record system event
        event = SystemEventDB(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="interaction_created",
            agent_id=agent.id,
            memory_id=agent_output.used_memory_ids[0] if agent_output.used_memory_ids else None,
            details={
                "interaction_id": interaction_id,
                "used_memories": agent_output.used_memory_ids,
                "action_type": agent_output.action_type
            }
        )
        db.add(event)
        db.commit()

        return interaction, agent_output, applicable_memories

    @staticmethod
    def execute_7step_pipeline(
        db: Session,
        agent_name: str,
        user_prompt: str,
        task_type: str,
        customer_tier: str,
        customer_id: Optional[str] = "cust-enterprise-01",
        tenant_id: str = "default",
        hindsight_service: Optional[HindsightService] = None,
        llm_service: Optional[LLMService] = None
    ) -> Tuple[Dict[str, Any], ExecutionTrace]:
        """
        Executes the 7-Step Decomposed Reasoning Pipeline:
        Step 1: Understand (entity, intent, amount extraction, PII & injection check)
        Step 2: Recall (fetch candidate memories from Hindsight & local DB)
        Step 3: Applicability Filter (hierarchical scope, condition predicates, expiration)
        Step 4: Conflict Check (contradiction detection & precedence arbitration)
        Step 5: Draft (generate preliminary action and response)
        Step 6: Self-Critique (verify compliance against negative guardrails)
        Step 7: Final Answer (assemble response, tool calls, trace & cost metrics)
        """
        start_total = time.perf_counter()
        steps: List[PipelineStepTrace] = []
        interaction_id = f"inter-{uuid.uuid4().hex[:12]}"
        trace_id = f"trc-{uuid.uuid4().hex[:12]}"

        accepted_memory_ids: List[str] = []
        rejected_memory_ids: List[str] = []
        rejection_reasons: Dict[str, str] = {}

        # ----------------------------------------------------
        # STEP 1: UNDERSTAND
        # ----------------------------------------------------
        s1_start = time.perf_counter()
        is_injection, injection_reason = SecurityService.detect_prompt_injection(user_prompt)
        redacted_prompt, pii_counts = SecurityService.redact_pii(user_prompt)

        extracted_amount = None
        amounts = re.findall(r"\$(\d+(?:\.\d{2})?)", user_prompt)
        if amounts:
            extracted_amount = float(amounts[0])

        order_ids = re.findall(r"\b(?:ORD|order)[-_]?([a-zA-Z0-9]+)\b", user_prompt, re.IGNORECASE)
        extracted_order = order_ids[0] if order_ids else None

        steps.append(PipelineStepTrace(
            step_name="understand",
            status="WARNING" if is_injection else "SUCCESS",
            duration_ms=round((time.perf_counter() - s1_start) * 1000, 2),
            details={
                "extracted_amount": extracted_amount,
                "extracted_order": extracted_order,
                "is_injection": is_injection,
                "pii_redacted": pii_counts
            }
        ))

        # ----------------------------------------------------
        # STEP 2: RECALL
        # ----------------------------------------------------
        s2_start = time.perf_counter()
        recalled_raw: List[Dict[str, Any]] = []

        if hindsight_service:
            try:
                hs_results = hindsight_service.recall(
                    query=f"{task_type} {customer_tier} {user_prompt}"
                )
                recalled_raw.extend(hs_results)
            except Exception:
                pass

        local_db_memories = db.query(MemoryReferenceDB).filter(
            MemoryReferenceDB.state.in_(["active", "verified"]),
            MemoryReferenceDB.is_deleted == False
        ).all()

        local_dict_map = {m.memory_id: m for m in local_db_memories}

        seen_ids = set()
        candidates: List[MemoryReferenceDB] = []
        for r in recalled_raw:
            m_id = getattr(r, "id", None) or (r.get("id") if isinstance(r, dict) else None)
            if m_id and m_id in local_dict_map and m_id not in seen_ids:
                candidates.append(local_dict_map[m_id])
                seen_ids.add(m_id)

        for m in local_db_memories:
            if m.memory_id not in seen_ids:
                candidates.append(m)
                seen_ids.add(m.memory_id)

        steps.append(PipelineStepTrace(
            step_name="recall",
            status="SUCCESS",
            duration_ms=round((time.perf_counter() - s2_start) * 1000, 2),
            details={"candidates_found": len(candidates)}
        ))

        # ----------------------------------------------------
        # STEP 3: APPLICABILITY FILTER
        # ----------------------------------------------------
        s3_start = time.perf_counter()
        applicable_memories: List[MemoryReferenceDB] = []
        context_payload = {"amount": extracted_amount, "order_id": extracted_order}

        for mem in candidates:
            is_app, reason = ScopeEvaluator.is_memory_applicable(
                memory=mem,
                agent_name=agent_name,
                task_type=task_type,
                customer_tier=customer_tier,
                tenant_id=tenant_id,
                context_payload=context_payload
            )
            if is_app:
                applicable_memories.append(mem)
            else:
                rejected_memory_ids.append(mem.memory_id)
                rejection_reasons[mem.memory_id] = reason

        steps.append(PipelineStepTrace(
            step_name="applicability_filter",
            status="SUCCESS",
            duration_ms=round((time.perf_counter() - s3_start) * 1000, 2),
            details={
                "accepted_count": len(applicable_memories),
                "rejected_count": len(rejected_memory_ids)
            }
        ))

        # ----------------------------------------------------
        # STEP 4: CONFLICT CHECK
        # ----------------------------------------------------
        s4_start = time.perf_counter()
        conflict_free_memories: List[MemoryReferenceDB] = []
        sorted_memories = sorted(
            applicable_memories,
            key=lambda m: (m.priority or 40, m.confidence_score or 0.5),
            reverse=True
        )

        for mem in sorted_memories:
            conflicts = ContradictionService.detect_contradictions(
                candidate_lesson=mem.lesson or "",
                existing_memories=conflict_free_memories
            )
            if conflicts:
                rejected_memory_ids.append(mem.memory_id)
                rejection_reasons[mem.memory_id] = f"Overridden by higher-precedence conflicting policy: {conflicts[0]['evidence']}"
            else:
                conflict_free_memories.append(mem)
                accepted_memory_ids.append(mem.memory_id)

        steps.append(PipelineStepTrace(
            step_name="conflict_check",
            status="SUCCESS",
            duration_ms=round((time.perf_counter() - s4_start) * 1000, 2),
            details={
                "conflict_free_count": len(conflict_free_memories),
                "conflicts_eliminated": len(applicable_memories) - len(conflict_free_memories)
            }
        ))

        # ----------------------------------------------------
        # STEP 5: DRAFT
        # ----------------------------------------------------
        s5_start = time.perf_counter()
        active_lessons = [m.lesson for m in conflict_free_memories if m.lesson]

        if llm_service and not is_injection:
            agent_input = AgentReasoningInput(
                agent_id=agent_name,
                agent_name=agent_name,
                task_type=task_type,
                customer_tier=customer_tier,
                user_prompt=redacted_prompt,
                relevant_memories=[
                    {"id": m.memory_id, "text": m.lesson, "confidence_level": m.confidence_level}
                    for m in conflict_free_memories
                ]
            )
            llm_result = llm_service.generate_agent_response(agent_input)
            draft_response = llm_result.response_text
            action_type = llm_result.action_type
            requires_approval = llm_result.requires_approval
            reasoning_mode = llm_result.reasoning_mode
        else:
            reasoning_mode = "DETERMINISTIC_FALLBACK"
            if is_injection:
                draft_response = "I cannot process this request because it violates system safety boundaries."
                action_type = "REJECTED_SECURITY"
                requires_approval = True
            elif active_lessons:
                draft_response = (
                    f"Applying validated policy: {active_lessons[0]} "
                    f"For customer ({customer_tier} tier), our verified guidelines state: {active_lessons[0]}"
                )
                action_type = "POLICY_APPLIED"
                requires_approval = False
            else:
                draft_response = f"[{agent_name}] Proceeding with standard {task_type} policy handling for {customer_tier} tier customer."
                action_type = "STANDARD_EXECUTION"
                requires_approval = False

        steps.append(PipelineStepTrace(
            step_name="draft",
            status="SUCCESS",
            duration_ms=round((time.perf_counter() - s5_start) * 1000, 2),
            details={"action_type": action_type, "requires_approval": requires_approval}
        ))

        # ----------------------------------------------------
        # STEP 6: SELF-CRITIQUE (NEGATIVE GUARDRAILS VERIFICATION)
        # ----------------------------------------------------
        s6_start = time.perf_counter()
        critique_passed = True
        critique_notes = []

        for m in conflict_free_memories:
            if m.is_negative_guardrail:
                g_lower = m.lesson.lower()
                if "never refund without return label" in g_lower or "requires return label" in g_lower or "return label" in g_lower:
                    critique_passed = False
                    critique_notes.append("Negative guardrail enforced: Mandatory return shipping label required.")
                    draft_response = (
                        f"Per organizational policy: {m.lesson} "
                        f"We cannot issue a refund without a verified return shipping label. "
                        f"Please provide your return tracking number to proceed."
                    )
                    requires_approval = True
                    action_type = "GUARDRAIL_ENFORCED"

        steps.append(PipelineStepTrace(
            step_name="self_critique",
            status="SUCCESS" if critique_passed else "WARNING",
            duration_ms=round((time.perf_counter() - s6_start) * 1000, 2),
            details={
                "critique_passed": critique_passed,
                "notes": critique_notes
            }
        ))

        # ----------------------------------------------------
        # STEP 7: FINAL ANSWER & TOOL DISPATCH
        # ----------------------------------------------------
        s7_start = time.perf_counter()
        tool_calls = []

        if extracted_amount and "refund" in task_type.lower():
            tool_calls.append({
                "tool_name": "process_refund",
                "parameters": {"amount": extracted_amount, "customer_id": customer_id},
                "requires_confirmation": extracted_amount > 100.0,
                "status": "REQUIRES_CONFIRMATION" if extracted_amount > 100.0 else "PENDING_EXECUTION"
            })

        if extracted_order:
            tool_calls.append({
                "tool_name": "lookup_order",
                "parameters": {"order_id": extracted_order},
                "status": "PENDING_EXECUTION"
            })

        steps.append(PipelineStepTrace(
            step_name="final_answer",
            status="SUCCESS",
            duration_ms=round((time.perf_counter() - s7_start) * 1000, 2),
            details={"tool_calls_count": len(tool_calls)}
        ))

        total_duration = round((time.perf_counter() - start_total) * 1000, 2)
        tokens_est = len(user_prompt.split()) + len(draft_response.split()) + sum(len(m.lesson.split()) for m in conflict_free_memories)
        cost_est = round(tokens_est * 0.000002, 6)

        trace = ExecutionTrace(
            trace_id=trace_id,
            agent_id=agent_name,
            interaction_id=interaction_id,
            total_duration_ms=total_duration,
            steps=steps,
            accepted_memory_ids=accepted_memory_ids,
            rejected_memory_ids=rejected_memory_ids,
            rejection_reasons=rejection_reasons,
            tokens_used=tokens_est,
            estimated_cost_usd=cost_est
        )

        response_dict = {
            "interaction_id": interaction_id,
            "agent_id": agent_name,
            "agent_name": agent_name,
            "status": "COMPLETED",
            "user_prompt": user_prompt,
            "agent_response": draft_response,
            "action_type": action_type,
            "requires_approval": requires_approval,
            "used_memory_ids": accepted_memory_ids,
            "recalled_memories": [
                {
                    "memory_id": m.memory_id,
                    "lesson": m.lesson,
                    "confidence_level": m.confidence_level,
                    "priority": m.priority,
                    "is_negative_guardrail": m.is_negative_guardrail
                }
                for m in conflict_free_memories
            ],
            "rejected_memory_ids": rejected_memory_ids,
            "rejection_reasons": rejection_reasons,
            "tool_calls": tool_calls,
            "reasoning_mode": reasoning_mode,
            "execution_trace": trace.model_dump(),
            "cost_usd": cost_est,
            "tokens_used": tokens_est,
            "created_at": datetime.now(timezone.utc)
        }

        return response_dict, trace

    @staticmethod
    def execute_tool_action(
        db: Session,
        action_req: ActionExecutionRequest
    ) -> ActionExecutionResult:
        action_id = f"act-{uuid.uuid4().hex[:12]}"
        action_name = action_req.action_name
        params = action_req.parameters
        mode = action_req.mode.lower()
        amount = params.get("amount", 0.0)
        try:
            amount = float(amount)
        except (ValueError, TypeError):
            amount = 0.0

        threshold_amount = 100.0
        exceeds_threshold = amount > threshold_amount

        if exceeds_threshold and not action_req.user_confirmed and mode == "execute":
            status = "REQUIRES_CONFIRMATION"
            result_details = {
                "message": f"Action {action_name} of ${amount:.2f} exceeds threshold (${threshold_amount:.2f}). Explicit confirmation required.",
                "threshold_amount": threshold_amount,
                "amount": amount
            }
        elif mode == "dry_run":
            status = "SIMULATED"
            result_details = {
                "message": f"[DRY-RUN SIMULATION] Action {action_name} simulated successfully with params: {params}",
                "simulated_outcome": "SUCCESS",
                "affected_entities": params
            }
        else:
            status = "EXECUTED"
            result_details = {
                "message": f"Action {action_name} executed successfully.",
                "confirmation_id": f"CONF-{uuid.uuid4().hex[:8].upper()}",
                "executed_at": datetime.now(timezone.utc).isoformat()
            }

        audit = AgentActionAuditDB(
            id=action_id,
            interaction_id=action_req.interaction_id or "direct_tool_invocation",
            agent_id=action_req.agent_id,
            action_name=action_name,
            parameters=params,
            execution_mode=mode,
            exceeds_confirmation_threshold=exceeds_threshold,
            threshold_amount=threshold_amount,
            status=status,
            result=result_details,
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit)
        db.commit()

        return ActionExecutionResult(
            action_id=action_id,
            action_name=action_name,
            mode=mode,
            status=status,
            threshold_exceeded=exceeds_threshold,
            threshold_amount=threshold_amount,
            details=result_details,
            timestamp=datetime.now(timezone.utc)
        )

agent_pipeline_service = AgentPipelineService()
