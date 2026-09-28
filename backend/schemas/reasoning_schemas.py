from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class AgentReasoningInput(BaseModel):
    agent_id: str
    agent_name: str
    task_type: str
    customer_tier: str
    user_prompt: str
    relevant_memories: List[Dict[str, Any]] = Field(default_factory=list)
    has_conflicts: bool = False
    conflicting_memories: List[Dict[str, Any]] = Field(default_factory=list)

class AgentReasoningOutput(BaseModel):
    response_text: str
    action_type: str
    requires_approval: bool
    used_memory_ids: List[str] = Field(default_factory=list)
    decision_rationale: str
    confidence_assessment: str
    reasoning_mode: str = "LIVE_AI_GROQ"  # or "DETERMINISTIC_FALLBACK"
