import json
import logging
from typing import Optional
from backend.utils.config import settings
from backend.schemas.reasoning_schemas import AgentReasoningInput, AgentReasoningOutput

logger = logging.getLogger("learnmesh.llm")

class LLMService:
    """
    LLM reasoning service utilizing Groq when configured,
    with robust structured output validation and deterministic fallbacks.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self._client = None

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("gsk_"))

    def get_client(self):
        if self._client is None and self.is_available():
            import groq
            self._client = groq.Groq(api_key=self.api_key)
        return self._client

    def generate_agent_response(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        """
        Generate structured agent response using Groq if available,
        or deterministic memory-guided reasoning fallback.
        """
        if self.is_available():
            try:
                return self._call_groq_reasoning(input_data)
            except Exception as e:
                logger.warning(f"Groq generation failed ({e}), falling back to deterministic reasoning.")

        return self._deterministic_fallback(input_data)

    def _call_groq_reasoning(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        client = self.get_client()

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
1. If organizational memories require approval for enterprise refund requests, follow that lesson strictly.
2. Return only valid JSON conforming to this schema:
{{
  "response_text": "text for the user",
  "action_type": "refund_approved" | "approval_required" | "general_response",
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
        return AgentReasoningOutput(**data)

    def _deterministic_fallback(self, input_data: AgentReasoningInput) -> AgentReasoningOutput:
        """
        Deterministic, audit-verifiable reasoning engine.
        Ensures 100% reproducible behavior across demo scenarios.
        """
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
                confidence_assessment="Strong"
            )

        # Baseline default behavior when no relevant shared memory is recalled:
        if is_refund_task:
            return AgentReasoningOutput(
                response_text="I will process the refund for your account immediately as requested.",
                action_type="refund_approved",
                requires_approval=False,
                used_memory_ids=[],
                decision_rationale="Standard automatic refund processing.",
                confidence_assessment="Limited"
            )

        return AgentReasoningOutput(
            response_text=f"Handled inquiry for {input_data.task_type}.",
            action_type="general_response",
            requires_approval=False,
            used_memory_ids=[],
            decision_rationale="Standard query resolution.",
            confidence_assessment="Moderate"
        )

llm_service = LLMService()
