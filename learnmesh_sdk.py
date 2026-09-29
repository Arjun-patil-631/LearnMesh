"""
LearnMesh Python SDK Client - Enterprise Edition
"Correct once. Learn everywhere."

Official Python client library for integrating multi-agent fleets with LearnMesh.
Supports synchronous and typed interaction with LearnMesh core API, reasoning pipeline,
governance, and cryptographic audit log.
"""

from typing import Dict, Any, List, Optional
import httpx

class LearnMeshClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        tenant_id: str = "default",
        timeout: float = 30.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "X-Tenant-ID": self.tenant_id,
                **({"Authorization": f"Bearer {api_key}"} if api_key else {})
            }
        )

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # --- Agent Fleet & Interactions ---

    def list_agents(self) -> List[Dict[str, Any]]:
        resp = self.client.get("/api/v1/agents")
        resp.raise_for_status()
        return resp.json()

    def create_interaction(
        self,
        agent_id: str,
        user_prompt: str,
        task_type: str = "refund",
        customer_tier: str = "enterprise",
        customer_id: str = "cust-enterprise-01"
    ) -> Dict[str, Any]:
        payload = {
            "agent_id": agent_id,
            "user_prompt": user_prompt,
            "task_type": task_type,
            "customer_tier": customer_tier,
            "customer_id": customer_id
        }
        resp = self.client.post("/api/v1/interactions", json=payload)
        resp.raise_for_status()
        return resp.json()

    def execute_reasoning_pipeline(
        self,
        agent_id: str,
        user_prompt: str,
        task_type: str = "refund",
        customer_tier: str = "enterprise",
        customer_id: str = "cust-enterprise-01"
    ) -> Dict[str, Any]:
        """Runs the 7-step decomposed reasoning pipeline with full step traces."""
        payload = {
            "agent_id": agent_id,
            "user_prompt": user_prompt,
            "task_type": task_type,
            "customer_tier": customer_tier,
            "customer_id": customer_id
        }
        resp = self.client.post("/api/v1/agents/pipeline/execute", json=payload)
        resp.raise_for_status()
        return resp.json()

    # --- Memory Lifecycle & Human-in-the-Loop ---

    def submit_correction(
        self,
        interaction_id: str,
        correction_text: str,
        human_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "correction_text": correction_text,
            "human_reason": human_reason
        }
        resp = self.client.post(f"/api/v1/interactions/{interaction_id}/correct", json=payload)
        resp.raise_for_status()
        return resp.json()

    def validate_and_promote(
        self,
        candidate_id: str,
        confirmed_lesson: str,
        target_scope: List[str],
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "candidate_id": candidate_id,
            "confirmed_lesson": confirmed_lesson,
            "target_scope": target_scope,
            "notes": notes
        }
        resp = self.client.post(f"/api/v1/candidates/{candidate_id}/validate", json=payload)
        resp.raise_for_status()
        return resp.json()

    def record_outcome(
        self,
        interaction_id: str,
        memory_id: str,
        outcome_type: str = "CONFIRMED_SUCCESS",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "memory_id": memory_id,
            "outcome_type": outcome_type,
            "notes": notes
        }
        resp = self.client.post(f"/api/v1/interactions/{interaction_id}/outcome", json=payload)
        resp.raise_for_status()
        return resp.json()

    def list_memories(
        self,
        agent_filter: Optional[str] = None,
        task_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        params = {}
        if agent_filter:
            params["agent_filter"] = agent_filter
        if task_type:
            params["task_type"] = task_type
        resp = self.client.get("/api/v1/memories", params=params)
        resp.raise_for_status()
        return resp.json()

    # --- Governance & Tool Guardrails ---

    def execute_action(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        mode: str = "dry_run",
        agent_id: str = "Billing Agent",
        user_confirmed: bool = False
    ) -> Dict[str, Any]:
        payload = {
            "action_name": action_name,
            "parameters": parameters,
            "mode": mode,
            "agent_id": agent_id,
            "user_confirmed": user_confirmed
        }
        resp = self.client.post("/api/v1/agents/actions/execute", json=payload)
        resp.raise_for_status()
        return resp.json()

    def verify_audit_log(self) -> Dict[str, Any]:
        resp = self.client.get("/api/v1/governance/audit-log/verify")
        resp.raise_for_status()
        return resp.json()

    def run_benchmarks(self, mode: str = "AFTER_LEARNING") -> Dict[str, Any]:
        resp = self.client.post("/api/v1/evaluation/run-benchmarks", params={"mode": mode})
        resp.raise_for_status()
        return resp.json()

    def get_metrics(self) -> Dict[str, Any]:
        resp = self.client.get("/api/v1/evaluation/metrics")
        resp.raise_for_status()
        return resp.json()
