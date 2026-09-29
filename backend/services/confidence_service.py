import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
from backend.schemas.enterprise_schemas import ConfidenceBreakdown
from backend.schemas.memory_schemas import ConfidenceLevel

ROLE_WEIGHTS = {
    "admin": 1.0,
    "lead_verifier": 0.95,
    "senior_verifier": 0.85,
    "verifier": 0.70,
    "junior_verifier": 0.50,
    "agent_operator": 0.40,
    "system": 0.80
}

class ConfidenceService:
    @staticmethod
    def calculate_confidence(
        verifier_role: str = "verifier",
        supporting_count: int = 0,
        contradiction_count: int = 0,
        created_at: Optional[datetime] = None,
        last_reinforced_at: Optional[datetime] = None,
        base_score: float = 0.40
    ) -> Tuple[float, ConfidenceLevel, ConfidenceBreakdown]:
        """
        Confidence 2.0 Formula:
        Confidence = (w_v * V_role) + (w_s * S_bonus) - (w_c * C_penalty) - (lambda * Delta_t)
        Normalized to [0.05, 0.99]
        """
        # 1. Verifier Role Weight
        v_weight = ROLE_WEIGHTS.get(verifier_role.lower(), 0.70)
        role_component = base_score * v_weight

        # 2. Supporting Outcomes Bonus (Logarithmic scaling, max +0.35)
        # S_bonus = min(0.35, 0.08 * ln(1 + supporting_count))
        s_bonus = min(0.35, 0.08 * math.log(1.0 + max(0, supporting_count)))

        # 3. Contradiction Penalty (linear 0.20 per conflict, max -0.50)
        c_penalty = min(0.50, 0.20 * max(0, contradiction_count))

        # 4. Time Decay Penalty
        # Half-life decay: delta days since last reinforced or created
        ref_time = last_reinforced_at or created_at or datetime.now(timezone.utc)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        delta_days = max(0.0, (now - ref_time).total_seconds() / 86400.0)
        
        # λ = 0.002 per day (~6% drop after 30 days of inactivity)
        time_decay = min(0.25, 0.002 * delta_days)

        # Final raw score calculation
        raw_score = role_component + s_bonus - c_penalty - time_decay
        final_score = round(max(0.05, min(0.99, raw_score)), 3)

        # Confidence level classification
        if final_score >= 0.75:
            level = ConfidenceLevel.STRONG
        elif final_score >= 0.50:
            level = ConfidenceLevel.MODERATE
        else:
            level = ConfidenceLevel.LIMITED

        # Human-readable explanation generator
        explanation_parts = [
            f"Base score: {base_score:.2f} scaled by {verifier_role} weight ({v_weight:.2f}) -> {role_component:.2f}",
        ]
        if supporting_count > 0:
            explanation_parts.append(f"+{s_bonus:.2f} bonus from {supporting_count} successful outcome(s)")
        if contradiction_count > 0:
            explanation_parts.append(f"-{c_penalty:.2f} penalty from {contradiction_count} contradiction(s)")
        if time_decay > 0.01:
            explanation_parts.append(f"-{time_decay:.2f} time decay ({delta_days:.1f} days inactive)")
        
        explanation = f"Confidence {final_score:.2f} ({level.value}): " + "; ".join(explanation_parts)

        breakdown = ConfidenceBreakdown(
            base_score=round(base_score, 3),
            verifier_role=verifier_role,
            verifier_weight=round(v_weight, 3),
            supporting_bonus=round(s_bonus, 3),
            contradiction_penalty=round(c_penalty, 3),
            time_decay_penalty=round(time_decay, 3),
            final_score=final_score,
            explanation=explanation
        )

        return final_score, level, breakdown
