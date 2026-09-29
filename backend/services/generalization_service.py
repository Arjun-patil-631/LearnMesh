import re
from typing import Dict, Any, List, Tuple

GUARDRAIL_PATTERNS = [
    r"\bnever\b",
    r"\bdo not\b",
    r"\bdon't\b",
    r"\bcannot\b",
    r"\bprohibited\b",
    r"\bforbidden\b",
    r"\bmust not\b",
    r"\bdisallow\b",
    r"\bunder no circumstances\b"
]

HIGH_RISK_KEYWORDS = [
    "refund",
    "payment",
    "credit card",
    "wire transfer",
    "bank account",
    "ssn",
    "legal",
    "compliance",
    "confidential",
    "pii",
    "gdpr",
    "delete database",
    "cancel contract"
]

class GeneralizationService:
    @staticmethod
    def analyze_correction(
        correction_text: str,
        context: str,
        task_type: str,
        original_action: str
    ) -> Dict[str, Any]:
        """
        Analyzes a human correction to synthesize:
        1. Specific rule (direct constraint for this specific edge case)
        2. Generalized rule (broader organizational principle)
        3. Negative guardrail classification
        4. Suggested scope of affected agent roles
        5. Suggested condition predicates
        6. Risk category & two-person approval requirement
        """
        norm_text = correction_text.strip()
        norm_lower = norm_text.lower()

        # 1. Negative guardrail detection
        is_negative_guardrail = any(
            re.search(pat, norm_lower) for pat in GUARDRAIL_PATTERNS
        )

        # 2. Risk level assessment
        risk_hits = [kw for kw in HIGH_RISK_KEYWORDS if kw in norm_lower]
        if any(term in norm_lower for term in ["legal", "compliance", "ssn", "gdpr"]):
            risk_level = "CRITICAL"
            requires_two_person = True
        elif risk_hits:
            risk_level = "HIGH"
            requires_two_person = True
        elif is_negative_guardrail:
            risk_level = "MEDIUM"
            requires_two_person = False
        else:
            risk_level = "LOW"
            requires_two_person = False

        # 3. Specific rule synthesis
        specific_rule = norm_text
        if not specific_rule.endswith("."):
            specific_rule += "."

        # 4. Generalized rule synthesis
        generalized_rule = GeneralizationService._generalize_rule(norm_text, task_type)

        # 5. Suggested agent scope
        suggested_scope = GeneralizationService._determine_scope(task_type, norm_lower)

        # 6. Extract condition predicates (e.g., amount, tier)
        suggested_conditions = GeneralizationService._extract_conditions(norm_text)

        return {
            "specific_rule": specific_rule,
            "generalized_rule": generalized_rule,
            "is_negative_guardrail": is_negative_guardrail,
            "suggested_scope": suggested_scope,
            "suggested_conditions": suggested_conditions,
            "risk_level": risk_level,
            "requires_two_person_approval": requires_two_person
        }

    @staticmethod
    def _generalize_rule(text: str, task_type: str) -> str:
        lower = text.lower()
        if "refund" in lower:
            if "receipt" in lower or "return label" in lower:
                return "All customer financial concessions or returns require verified proof of return before disbursement across all support channels."
            if "manager" in lower or "approval" in lower:
                return "Transactions exceeding standard agent authorization thresholds must be formally routed for managerial approval."
            return "Customer compensation and refunds must adhere strictly to verified dispute resolution guidelines."
        elif "sla" in lower or "escalat" in lower:
            return "High-priority support requests must be escalated according to organizational SLA thresholds without delay."
        elif "discount" in lower:
            return "Discounts and promotional pricing must not exceed authorized account management tiers."
        elif "compliance" in lower or "legal" in lower:
            return "Regulatory, security, and compliance verification standards must be enforced uniformly across all agent interactions."
        else:
            return f"Organizational protocol for {task_type}: {text}"

    @staticmethod
    def _determine_scope(task_type: str, lower_text: str) -> List[str]:
        scopes = set()
        if "refund" in lower_text or task_type == "refund":
            scopes.add("Billing Agent")
            scopes.add("Support Agent")
            scopes.add("Account Management Agent")
        elif "warehouse" in lower_text or "shipping" in lower_text or "inventory" in lower_text:
            scopes.add("Warehouse Operations")
            scopes.add("Support Agent")
        elif "compliance" in lower_text or "legal" in lower_text:
            scopes.add("Compliance Officer Agent")
            scopes.add("Billing Agent")
            scopes.add("Support Agent")
            scopes.add("Account Management Agent")
        elif "sales" in lower_text or "discount" in lower_text:
            scopes.add("Sales & Onboarding Agent")
            scopes.add("Account Management Agent")
        elif "it" in lower_text or "password" in lower_text or "hardware" in lower_text:
            scopes.add("IT Helpdesk Agent")
        else:
            scopes.add("Support Agent")
            scopes.add("Billing Agent")
            scopes.add("Account Management Agent")
            scopes.add("Warehouse Operations")

        return sorted(list(scopes))

    @staticmethod
    def _extract_conditions(text: str) -> Dict[str, Any]:
        conditions: Dict[str, Any] = {}
        # Monetary amounts like $50 or $100
        amounts = re.findall(r"\$(\d+(?:\.\d{2})?)", text)
        if amounts:
            conditions["threshold_amount"] = float(amounts[0])
            conditions["currency"] = "USD"

        # Customer tiers
        lower = text.lower()
        for tier in ["enterprise", "premium", "pro", "standard", "basic"]:
            if tier in lower:
                conditions["customer_tier"] = tier
                break

        if "return label" in lower or "receipt" in lower:
            conditions["requires_proof_of_return"] = True

        return conditions
