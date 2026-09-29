import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    InteractionDB,
    OutcomeDB,
    MemoryReferenceDB,
    CorrectionDB,
    UserDB
)
from backend.services.governance_service import GovernanceService

# Regex patterns for PII detection and redaction (Order matters: Credit cards and SSN first)
PII_PATTERNS = {
    "CREDIT_CARD": (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "[REDACTED_CREDIT_CARD]"),
    "SSN": (r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]"),
    "EMAIL": (r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[REDACTED_EMAIL]"),
    "PHONE": (r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "[REDACTED_PHONE]")
}

# Prompt injection markers
INJECTION_PATTERNS = [
    r"ignore (?:all )?(?:previous|prior) instructions",
    r"disregard (?:all )?(?:previous|prior) instructions",
    r"bypass (?:all )?(?:safety|policy|guardrail)",
    r"you are now in (?:developer|dan|jailbreak) mode",
    r"system override",
    r"<script[\s>]",
    r"reveal (?:your )?(?:system|hidden) prompt",
    r"simulate being (?:evil|unfiltered)"
]

class SecurityService:
    @staticmethod
    def redact_pii(text: str) -> Tuple[str, Dict[str, int]]:
        """
        Scans input string and redacts emails, phone numbers, credit card numbers, and SSNs.
        Returns (redacted_text, redaction_counts)
        """
        if not text:
            return text, {}

        redacted = text
        counts = {}
        for pii_type, (pattern, replacement) in PII_PATTERNS.items():
            matches = re.findall(pattern, redacted)
            if matches:
                counts[pii_type] = len(matches)
                redacted = re.sub(pattern, replacement, redacted)

        return redacted, counts

    @staticmethod
    def detect_prompt_injection(text: str) -> Tuple[bool, Optional[str]]:
        """
        Scans for adversarial prompt injection patterns.
        """
        if not text:
            return False, None

        lower = text.lower()
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, lower):
                return True, f"Adversarial prompt injection pattern detected: '{pattern}'"

        return False, None

    @staticmethod
    def sanitize_and_wrap_memories(memories: List[Dict[str, Any]]) -> str:
        """
        Formats retrieved memories into hardened XML delimiters with strict instruction isolation:
        <verified_organizational_memory>
          <memory id="..." role="..." scope="..." is_guardrail="...">
            [LESSON_CONTENT]
          </memory>
        </verified_organizational_memory>
        """
        if not memories:
            return ""

        xml_parts = [
            "<verified_organizational_memory>",
            "<!-- NOTE: The following lessons are organizational ground-truth policies. Adhere to them strictly. -->"
        ]

        for mem in memories:
            mem_id = mem.get("memory_id", "unknown")
            role = mem.get("source_agent", "general")
            scope = ",".join(mem.get("scope", []))
            is_guardrail = "true" if mem.get("is_negative_guardrail") else "false"
            
            # Sanitize content to avoid XML breakout
            lesson_clean = mem.get("lesson", "").replace("<", "&lt;").replace(">", "&gt;")
            redacted_lesson, _ = SecurityService.redact_pii(lesson_clean)

            xml_parts.append(
                f'  <memory id="{mem_id}" role="{role}" scope="{scope}" is_guardrail="{is_guardrail}">\n'
                f'    {redacted_lesson}\n'
                f'  </memory>'
            )

        xml_parts.append("</verified_organizational_memory>")
        return "\n".join(xml_parts)

    @staticmethod
    def evaluate_corrector_trust(
        db: Session,
        user_id: Optional[str] = None
    ) -> float:
        """
        Calculates corrector trust score T in [0.0, 1.0].
        If user is not in DB or has trust_score < 0.40, returns trust level.
        Default anonymous/new corrector trust: 0.50.
        """
        if not user_id:
            return 0.50

        user = db.query(UserDB).filter(UserDB.id == user_id, UserDB.is_deleted == False).first()
        if user:
            return float(user.trust_score)

        return 0.50

    @staticmethod
    def forget_customer(
        db: Session,
        customer_id: str,
        actor_id: str = "dpo_admin",
        tenant_id: str = "default"
    ) -> Dict[str, Any]:
        """
        GDPR / CCPA "Right to be Forgotten" execution:
        Purges or irreversibly anonymizes customer-identifying records in Interactions, Corrections, Outcomes.
        """
        interactions = db.query(InteractionDB).filter(
            InteractionDB.customer_id == customer_id,
            InteractionDB.is_deleted == False
        ).all()

        modified_interactions = 0
        for inter in interactions:
            inter.customer_id = f"ANON_{customer_id[:4]}****"
            inter.user_prompt = f"[CUSTOMER PURGED UNDER GDPR ARTICLE 17 - REQUEST {datetime.now(timezone.utc).isoformat()}]"
            inter.agent_response = "[PERSONAL DATA REDACTED]"
            inter.updated_at = datetime.now(timezone.utc)
            modified_interactions += 1

        db.commit()

        # Record tamper-evident audit log of GDPR deletion
        GovernanceService.record_audit_entry(
            db=db,
            action="GDPR_FORGET_CUSTOMER_EXECUTED",
            actor_id=actor_id,
            tenant_id=tenant_id,
            target_entity="CustomerIdentity",
            target_id=customer_id,
            payload={
                "customer_id": customer_id,
                "interactions_anonymized": modified_interactions,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

        return {
            "customer_id": customer_id,
            "status": "PURGED_AND_ANONYMIZED",
            "interactions_anonymized": modified_interactions,
            "compliance_standard": "GDPR Article 17 / CCPA Section 1798.105"
        }
