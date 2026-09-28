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

class ContradictionDetector:
    """
    Detects opposing historical experiences rather than silently suppressing them.
    If Memory A mandates approval and Memory B allows direct refund in same context,
    flags the contradiction explicitly for human operators.
    """

    @staticmethod
    def detect_conflicts(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        approval_rules = []
        direct_actions = []

        for m in memories:
            text = (m.get("text") or m.get("lesson") or "").lower()
            if "without waiting for approval" in text or "no approval" in text or "immediately" in text or "direct refund" in text:
                direct_actions.append(m)
            elif "approval" in text or "require approval" in text:
                approval_rules.append(m)

        if approval_rules and direct_actions:
            return approval_rules + direct_actions
        return []
