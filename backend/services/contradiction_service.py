import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    MemoryReferenceDB,
    ContradictionReviewDB
)
from backend.schemas.enterprise_schemas import (
    ConflictType,
    ConflictSeverity,
    PolicyPrecedence
)

OPPOSING_KEYWORD_PAIRS = [
    (r"\ballow\b", r"\bnever allow\b"),
    (r"\bpermit\b", r"\bprohibit\b"),
    (r"\bdo not\b", r"\balways\b"),
    (r"\bno refund\b", r"\bissue refund\b"),
    (r"\bwithout receipt\b", r"\brequires receipt\b"),
    (r"\bimmediate\b", r"\bwait 24 hours\b"),
    (r"\bexempt\b", r"\bmandatory\b")
]

class ContradictionService:
    @staticmethod
    def detect_contradictions(
        candidate_lesson: str,
        existing_memories: List[MemoryReferenceDB]
    ) -> List[Dict[str, Any]]:
        """
        3-Layer Contradiction Detection:
        Layer 1: Rule-based heuristic pattern matching
        Layer 2: Token overlap / Jaccard similarity & numerical threshold divergence
        Layer 3: Policy Precedence conflict evaluation
        """
        conflicts = []
        cand_lower = candidate_lesson.lower()
        cand_tokens = set(re.findall(r"\w+", cand_lower))

        for existing in existing_memories:
            exist_lower = (existing.lesson or "").lower()
            exist_tokens = set(re.findall(r"\w+", exist_lower))

            # Calculate token Jaccard similarity to verify topical relevance
            intersection = cand_tokens.intersection(exist_tokens)
            union = cand_tokens.union(exist_tokens)
            jaccard = len(intersection) / len(union) if union else 0.0

            # Only check for contradictions if there's enough topical overlap
            if jaccard < 0.15 and not ("refund" in cand_lower and "refund" in exist_lower):
                continue

            # Layer 1: Heuristic Opposing Patterns
            for pat_a, pat_b in OPPOSING_KEYWORD_PAIRS:
                if (re.search(pat_a, cand_lower) and re.search(pat_b, exist_lower)) or \
                   (re.search(pat_b, cand_lower) and re.search(pat_a, exist_lower)):
                    conflicts.append({
                        "existing_memory": existing,
                        "conflict_type": ConflictType.DIRECT_OPPOSITION.value,
                        "severity": ConflictSeverity.HIGH.value,
                        "evidence": f"Opposing directives detected between candidate and existing memory: '{candidate_lesson}' vs '{existing.lesson}'",
                        "jaccard_similarity": round(jaccard, 3)
                    })
                    break

            # Layer 2: Numerical threshold divergence (e.g. $50 vs $20)
            cand_amounts = re.findall(r"\$(\d+)", cand_lower)
            exist_amounts = re.findall(r"\$(\d+)", exist_lower)
            if cand_amounts and exist_amounts and cand_amounts[0] != exist_amounts[0]:
                conflicts.append({
                    "existing_memory": existing,
                    "conflict_type": ConflictType.VALUE_RANGE_CONFLICT.value,
                    "severity": ConflictSeverity.MEDIUM.value,
                    "evidence": f"Numerical threshold mismatch: candidate mentions ${cand_amounts[0]}, existing mentions ${exist_amounts[0]}",
                    "jaccard_similarity": round(jaccard, 3)
                })

        return conflicts

    @staticmethod
    def evaluate_precedence(
        mem_a_priority: int,
        mem_b_priority: int
    ) -> Tuple[str, bool]:
        """
        Determines precedence based on hierarchical priority:
        Legal/Compliance (100) > Corporate Policy (80) > Department (60) > Agent (40) > Tip (20)
        Returns (winning_side: 'A'|'B'|'TIE', can_auto_resolve: bool)
        """
        diff = mem_a_priority - mem_b_priority
        if diff >= 20:
            return "A", True  # Significant precedence difference allows auto-supersede
        elif diff <= -20:
            return "B", True
        else:
            return "TIE", False  # Close or equal priority requires human governance review

    @staticmethod
    def create_contradiction_record(
        db: Session,
        memory_id_a: str,
        memory_id_b: str,
        conflict_type: str,
        severity: str,
        evidence: str,
        precedence_rule: Optional[str] = None,
        auto_resolved: bool = False
    ) -> ContradictionReviewDB:
        now = datetime.now(timezone.utc)
        # SLA: 48h for HIGH/CRITICAL, 7 days for LOW/MEDIUM
        sla_hours = 48 if severity in ["HIGH", "CRITICAL"] else 168
        sla_due = now + timedelta(hours=sla_hours)

        record = ContradictionReviewDB(
            id=f"rev-{uuid.uuid4().hex[:12]}",
            memory_id_a=memory_id_a,
            memory_id_b=memory_id_b,
            conflict_type=conflict_type,
            severity=severity,
            evidence=evidence,
            precedence_rule=precedence_rule,
            auto_resolved=auto_resolved,
            status="RESOLVED" if auto_resolved else "PENDING_REVIEW",
            sla_due_at=sla_due,
            created_at=now
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
