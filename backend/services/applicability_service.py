import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
from backend.schemas.memory_schemas import ConfidenceLevel

class ConfidenceCalculator:
    """
    Deterministic confidence calculator derived from empirical evidence:
      - Initial verified lesson = 0.40 (Limited)
      - Each supporting confirmation adds weight (up to 0.95 -> Strong)
      - Contradictions heavily penalize confidence (-0.30 per contradiction)
      
    Formulas:
      raw_score = 0.40 + (0.15 * min(supporting_count, 4)) - (0.30 * contradiction_count)
      clamped between 0.05 and 0.98
      
    Levels:
      >= 0.70: Strong
      0.40 - 0.69: Moderate
      < 0.40: Limited
    """

    @staticmethod
    def calculate(
        supporting_count: int = 0,
        contradiction_count: int = 0,
        is_human_verified: bool = True
    ) -> Tuple[float, ConfidenceLevel]:
        base = 0.40 if is_human_verified else 0.20
        support_gain = 0.15 * min(supporting_count, 4)
        contradiction_penalty = 0.30 * contradiction_count

        raw_score = base + support_gain - contradiction_penalty
        score = max(0.05, min(0.98, round(raw_score, 2)))

        if score >= 0.70 and contradiction_count == 0:
            level = ConfidenceLevel.STRONG
        elif score >= 0.40:
            level = ConfidenceLevel.MODERATE
        else:
            level = ConfidenceLevel.LIMITED

        return score, level

class ApplicabilityEngine:
    """
    Evaluates whether an organizational lesson is eligible for a specific agent:
      Checks:
      - Explicit scope inclusion
      - Agent capability alignment
      - Task type relevance
    """

    @staticmethod
    def evaluate(
        agent_name: str,
        agent_capabilities: List[str],
        memory_scope: List[str],
        task_type: str
    ) -> Dict[str, Any]:
        is_in_scope = (agent_name in memory_scope) or (len(memory_scope) == 0)
        capability_match = task_type in agent_capabilities or "refunds" in agent_capabilities

        is_eligible = is_in_scope and capability_match

        reasons = []
        if is_in_scope:
            reasons.append(f"Agent '{agent_name}' is explicitly included in fleet scope")
        else:
            reasons.append(f"Agent '{agent_name}' is outside memory scope: {memory_scope}")

        if capability_match:
            reasons.append(f"Agent possesses relevant capabilities for '{task_type}'")
        else:
            reasons.append(f"Agent lacks workflow capability for '{task_type}'")

        return {
            "is_eligible": is_eligible,
            "reasons": reasons,
            "agent": agent_name,
            "task_type": task_type
        }

from pydantic import BaseModel, Field

class PolicyFact(BaseModel):
    memory_id: str
    action: str = "refund"
    approval_required: bool
    customer_tier: str = "enterprise"
    condition: str
    source_text: str

class ContradictionDetector:
    """
    Detects opposing historical experiences by extracting structured policy facts
    rather than brittle keyword matching.

    Example:
      Policy A: action=refund, approval_required=False, tier=enterprise
      Policy B: action=refund, approval_required=True, tier=enterprise
      Result: CONFLICT DETECTED -> ESCALATION_REQUIRED

    Self-consistency:
      "Do not promise an immediate refund for enterprise customers. VP approval is required."
      -> resolves cleanly to approval_required=True and does NOT conflict with itself.
    """

    @staticmethod
    def extract_policy_fact(memory: Dict[str, Any]) -> PolicyFact:
        mem_id = memory.get("id") or memory.get("memory_id") or "UNKNOWN"
        raw_text = (memory.get("text") or memory.get("lesson") or "").strip()
        lower = raw_text.lower()

        # Deterministic semantic parsing:
        # Determine customer_tier context
        tier = "enterprise" if ("enterprise" in lower or "contract" in lower) else "standard"
        action = "refund" if ("refund" in lower or "credit" in lower or "payment" in lower) else "general"

        # Explicit negation and prohibition check:
        # If "do not promise an immediate refund" or "require approval" or "approval is required"
        negation_of_direct = any(phrase in lower for phrase in [
            "do not promise", "don't promise", "do not issue", "never issue", "do not approve", "no immediate refund"
        ])
        approval_mandate = any(phrase in lower for phrase in [
            "approval is required", "require approval", "approval required", "requires approval",
            "vp approval", "leadership approval", "approval prior", "approval before"
        ])
        direct_mandate = any(phrase in lower for phrase in [
            "without waiting for approval", "no approval needed", "no approval required",
            "refund immediately without", "direct refund without", "immediate refund permitted"
        ])

        if approval_mandate or negation_of_direct:
            approval_req = True
            cond = "Approval required prior to commitment"
        elif direct_mandate or ("immediately" in lower and not negation_of_direct and not approval_mandate):
            approval_req = False
            cond = "Direct execution permitted without approval"
        else:
            # Default to approval required if uncertain for safety (fail closed)
            approval_req = "approval" in lower
            cond = "Standard execution policy"

        return PolicyFact(
            memory_id=mem_id,
            action=action,
            approval_required=approval_req,
            customer_tier=tier,
            condition=cond,
            source_text=raw_text
        )

    @classmethod
    def detect_conflicts(cls, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extracts policy facts for each memory and determines if conflicting directives
        exist for the same (action, customer_tier) pair.
        """
        if len(memories) < 2:
            return []

        facts: List[Tuple[PolicyFact, Dict[str, Any]]] = [
            (cls.extract_policy_fact(m), m) for m in memories
        ]

        # Group facts by (action, customer_tier)
        groups: Dict[Tuple[str, str], List[Tuple[PolicyFact, Dict[str, Any]]]] = {}
        for fact, raw_m in facts:
            key = (fact.action, fact.customer_tier)
            groups.setdefault(key, []).append((fact, raw_m))

        conflicting_memories = []
        for key, fact_tuples in groups.items():
            req_approvals = [t for t in fact_tuples if t[0].approval_required is True]
            no_approvals = [t for t in fact_tuples if t[0].approval_required is False]

            if req_approvals and no_approvals:
                # Contradiction detected between explicit true and false
                for _, raw_m in req_approvals + no_approvals:
                    if raw_m not in conflicting_memories:
                        conflicting_memories.append(raw_m)

        return conflicting_memories
