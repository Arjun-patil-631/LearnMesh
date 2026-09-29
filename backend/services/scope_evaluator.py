from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from backend.models.database import MemoryReferenceDB

class ScopeEvaluator:
    @staticmethod
    def is_memory_applicable(
        memory: MemoryReferenceDB,
        agent_name: str,
        task_type: Optional[str] = None,
        customer_tier: Optional[str] = None,
        tenant_id: str = "default",
        department: Optional[str] = None,
        context_payload: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        Evaluates hierarchical applicability of a memory reference:
        1. Tenant isolation (matching tenant_id or global "*")
        2. State check (must be active or verified)
        3. Validity window (valid_from <= now <= valid_until)
        4. Department check (if memory specifies a department)
        5. Agent role match (in memory.scope or "*")
        6. Customer segment match (if memory.customer_segments specified)
        7. Predicate conditions match (e.g. amount thresholds)
        """
        now = datetime.now(timezone.utc)
        payload = context_payload or {}

        # 1. State check
        if memory.state not in ["active", "verified"]:
            return False, f"Memory state is '{memory.state}', not active/verified"

        # 2. Tenant isolation
        if memory.tenant_id and memory.tenant_id not in ["*", "global", tenant_id]:
            if not ({memory.tenant_id, tenant_id}.issubset({"default", "tenant-default"})):
                return False, f"Tenant mismatch (memory: {memory.tenant_id}, context: {tenant_id})"

        # 3. Validity window
        if memory.valid_from:
            vf = memory.valid_from.replace(tzinfo=timezone.utc) if memory.valid_from.tzinfo is None else memory.valid_from
            if now < vf:
                return False, f"Memory not yet effective (valid from {vf.isoformat()})"
        if memory.valid_until:
            vu = memory.valid_until.replace(tzinfo=timezone.utc) if memory.valid_until.tzinfo is None else memory.valid_until
            if now > vu:
                return False, f"Memory expired (valid until {vu.isoformat()})"

        # 4. Department hierarchy check
        if memory.department and memory.department != "General" and department:
            if memory.department.lower() != department.lower():
                return False, f"Department mismatch (memory: {memory.department}, agent: {department})"

        # 5. Agent scope match
        scope_list = memory.scope or []
        if scope_list and "*" not in scope_list:
            # Check normalized match
            matched_agent = False
            for target_scope in scope_list:
                ts = target_scope.strip().lower()
                if ts == agent_name.strip().lower() or ts in agent_name.strip().lower() or agent_name.strip().lower() in ts:
                    matched_agent = True
                    break
            if not matched_agent:
                return False, f"Agent '{agent_name}' not in memory scope {scope_list}"

        # 6. Customer segment match
        if memory.customer_segments and customer_tier:
            norm_segments = [s.lower() for s in memory.customer_segments]
            if "*" not in norm_segments and customer_tier.lower() not in norm_segments:
                return False, f"Customer tier '{customer_tier}' not in allowed segments {memory.customer_segments}"

        # 7. Condition Predicates match
        if memory.conditions and isinstance(memory.conditions, dict):
            # Check threshold amount if provided
            req_max_amount = memory.conditions.get("max_amount")
            if req_max_amount is not None and "amount" in payload:
                try:
                    if float(payload["amount"]) > float(req_max_amount):
                        return False, f"Amount ${payload['amount']} exceeds maximum threshold ${req_max_amount}"
                except (ValueError, TypeError):
                    pass

            req_tier = memory.conditions.get("customer_tier")
            if req_tier and customer_tier and req_tier.lower() != customer_tier.lower():
                return False, f"Condition requires customer tier '{req_tier}'"

        match_reason = f"Matched scope for '{agent_name}' (tenant: {tenant_id}, state: {memory.state})"
        if memory.priority:
            match_reason += f", priority: {memory.priority}"
        if memory.is_negative_guardrail:
            match_reason += " [NEGATIVE GUARDRAIL]"

        return True, match_reason
