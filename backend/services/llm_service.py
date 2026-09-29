import json
import logging
from typing import Optional
from backend.utils.config import settings
from backend.schemas.reasoning_schemas import AgentReasoningInput, AgentReasoningOutput
from backend.utils.resilience import groq_circuit_breaker, retry_with_exponential_backoff

logger = logging.getLogger("learnmesh.llm")

class LLMService:
    """
    LLM reasoning service utilizing Groq when configured,
    with circuit breaker protection, retry backoff, and deterministic fallback.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.gemini_key = settings.GEMINI_API_KEY
        self.gemini_model = settings.GEMINI_MODEL
        self._client = None

    def is_available(self) -> bool:
        if not (self.api_key and self.api_key.startswith("gsk_")):
            return False
        return groq_circuit_breaker.can_execute()

    def is_gemini_available(self) -> bool:
        return bool(self.gemini_key and str(self.gemini_key).strip())

    def get_client(self):
        if self._client is None and bool(self.api_key and self.api_key.startswith("gsk_")):
            import groq
            self._client = groq.Groq(api_key=self.api_key, timeout=settings.GROQ_TIMEOUT_SECONDS)
        return self._client

    def generate_agent_response(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        """
        Generate structured agent response using Groq if available,
        then Gemini fallback, or deterministic memory-guided reasoning fallback.
        """
        # Safety gate: If organizational memories are directly contradictory,
        # escalate immediately to avoid non-deterministic model arbitration
        if input_data.has_conflicts or len(input_data.conflicting_memories) > 0:
            return self._deterministic_fallback(input_data)

        if self.is_available():
            try:
                return groq_circuit_breaker.execute(self._call_groq_reasoning, input_data)
            except Exception as e:
                logger.warning(
                    f"Groq generation failed ({type(e).__name__}: {e}), "
                    f"circuit breaker state: {groq_circuit_breaker.state.value}. "
                    f"Trying Gemini fallback."
                )

        if self.is_gemini_available():
            try:
                return self._call_gemini_reasoning(input_data)
            except Exception as e:
                logger.warning(
                    f"Gemini generation failed ({type(e).__name__}: {e}). "
                    f"Falling back to deterministic reasoning."
                )

        return self._deterministic_fallback(input_data)

    def _call_groq_reasoning(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        client = self.get_client()
        if client is None:
            raise ValueError("Groq client not initialized")

        memories_text = json.dumps(input_data.relevant_memories, indent=2)
        conflicts_text = json.dumps(input_data.conflicting_memories, indent=2)

        prompt = f"""
You are the AI reasoning engine for {input_data.agent_name}.
TASK: {input_data.task_type}
CUSTOMER TIER: {input_data.customer_tier}
USER REQUEST: "{input_data.user_prompt}"

RELEVANT ORGANIZATIONAL MEMORIES:
{memories_text}

CONFLICTS / CONTRADICTIONS:
{conflicts_text}

INSTRUCTIONS:
1. If conflicting memories exist (contradicting rules in CONFLICTS), set "action_type": "escalation_required", "requires_approval": true, cite both memory IDs in "used_memory_ids", and explain that a human operator must resolve the policy conflict.
2. If organizational memories require approval for enterprise refund requests, follow that lesson strictly and set "action_type": "approval_required", "requires_approval": true.
3. Otherwise, proceed with standard resolution.
4. Return only valid JSON conforming to this schema:
{{
  "response_text": "text for the user",
  "action_type": "refund_approved" | "approval_required" | "escalation_required" | "general_response",
  "requires_approval": true/false,
  "used_memory_ids": ["HM-xxxx"],
  "decision_rationale": "Clear rationale citing memories",
  "confidence_assessment": "Strong" | "Moderate" | "Limited"
}}
"""
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.1
        )
        raw_content = response.choices[0].message.content
        data = json.loads(raw_content)
        data["reasoning_mode"] = "LIVE_AI_GROQ"
        return AgentReasoningOutput(**data)

    def _call_gemini_reasoning(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        """Live reasoning via Google Gemini REST API (no extra SDK dependency)."""
        import re
        import httpx

        memories_text = json.dumps(input_data.relevant_memories, indent=2)
        conflicts_text = json.dumps(input_data.conflicting_memories, indent=2)

        prompt = f"""
You are the AI reasoning engine for {input_data.agent_name}.
TASK: {input_data.task_type}
CUSTOMER TIER: {input_data.customer_tier}
USER REQUEST: "{input_data.user_prompt}"

RELEVANT ORGANIZATIONAL MEMORIES:
{memories_text}

CONFLICTS / CONTRADICTIONS:
{conflicts_text}

INSTRUCTIONS:
1. If conflicting memories exist, set "action_type": "escalation_required", "requires_approval": true, cite both memory IDs in "used_memory_ids".
2. If organizational memories require approval for enterprise refund requests, follow that lesson strictly and set "action_type": "approval_required", "requires_approval": true.
3. Otherwise, proceed with standard resolution.
4. Return ONLY valid JSON with keys: response_text, action_type (refund_approved | approval_required | escalation_required | general_response), requires_approval, used_memory_ids, decision_rationale, confidence_assessment (Strong | Moderate | Limited).
"""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        resp = httpx.post(
            url,
            params={"key": self.gemini_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
            },
            timeout=settings.GEMINI_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        try:
            raw_text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"Unexpected Gemini response shape: {e}")
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.IGNORECASE)
        data = json.loads(cleaned)
        data["reasoning_mode"] = "LIVE_AI_GEMINI"
        return AgentReasoningOutput(**data)

    def _deterministic_fallback(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        """
        Deterministic, audit-verifiable reasoning engine.
        Ensures 100% reproducible behavior across demo scenarios.
        """
        # If conflicting memories are detected, flag and escalate to human operator:
        if input_data.has_conflicts or len(input_data.conflicting_memories) > 0:
            conflict_ids = [m.get("id") or m.get("memory_id") for m in input_data.conflicting_memories if m.get("id") or m.get("memory_id")]
            return AgentReasoningOutput(
                response_text=(
                    f"Warning: Conflicting organizational policies were recalled for this request. "
                    f"A human operator must resolve the policy conflict before action is taken."
                ),
                action_type="escalation_required",
                requires_approval=True,
                used_memory_ids=conflict_ids,
                decision_rationale=(
                    f"Contradiction detected among recalled lessons ({', '.join(conflict_ids)}). Escalating to prevent unsafe execution."
                ),
                confidence_assessment="Limited",
                reasoning_mode="DETERMINISTIC_FALLBACK"
            )

        enterprise_refund_lesson = None
        for m in input_data.relevant_memories:
            text = m.get("text", "") or m.get("lesson", "")
            if "approval" in text.lower() and ("refund" in text.lower() or "enterprise" in text.lower()):
                enterprise_refund_lesson = m
                break

        is_enterprise = input_data.customer_tier.lower() == "enterprise" or "enterprise" in input_data.user_prompt.lower()
        is_refund_task = input_data.task_type.lower() == "refund" or "refund" in input_data.user_prompt.lower()

        # If organizational memory exists and matches enterprise refund:
        if enterprise_refund_lesson and is_enterprise and is_refund_task:
            mem_id = enterprise_refund_lesson.get("id") or enterprise_refund_lesson.get("memory_id", "HM-UNKNOWN")
            return AgentReasoningOutput(
                response_text=(
                    f"Because this involves an enterprise contract, approval from account leadership "
                    f"must be verified before committing to a refund. I have initiated the approval workflow."
                ),
                action_type="approval_required",
                requires_approval=True,
                used_memory_ids=[mem_id],
                decision_rationale=(
                    f"Applied shared organizational lesson {mem_id} requiring approval for enterprise refunds."
                ),
                confidence_assessment="Strong",
                reasoning_mode="DETERMINISTIC_FALLBACK"
            )

        # Baseline default behavior when no relevant shared memory is recalled:
        if is_refund_task:
            return AgentReasoningOutput(
                response_text="I will process the refund for your account immediately as requested.",
                action_type="refund_approved",
                requires_approval=False,
                used_memory_ids=[],
                decision_rationale="Standard automatic refund processing.",
                confidence_assessment="Limited",
                reasoning_mode="DETERMINISTIC_FALLBACK"
            )

        return AgentReasoningOutput(
            response_text=f"Handled inquiry for {input_data.task_type}.",
            action_type="general_response",
            requires_approval=False,
            used_memory_ids=[],
            decision_rationale="Standard query resolution.",
            confidence_assessment="Moderate",
            reasoning_mode="DETERMINISTIC_FALLBACK"
        )

llm_service = LLMService()
