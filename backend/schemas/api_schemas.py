from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class CreateInteractionRequest(BaseModel):
    agent_id: str
    user_prompt: str
    task_type: str = "refund"
    customer_tier: str = "enterprise"
    customer_id: Optional[str] = "cust-enterprise-01"

class InteractionResponseModel(BaseModel):
    interaction_id: str
    agent_id: str
    agent_name: str
    status: str
    user_prompt: str
    agent_response: str
    action_type: str
    requires_approval: bool
    used_memory_ids: List[str]
    recalled_memories: List[Dict[str, Any]]
    created_at: datetime

class CorrectionRequest(BaseModel):
    correction_text: str
    human_reason: Optional[str] = None

class CorrectionResponse(BaseModel):
    correction_id: str
    candidate_id: str
    interaction_id: str
    original_action: str
    correction_text: str
    extracted_lesson: str
    recommended_scope: List[str]

class OutcomeRecordRequest(BaseModel):
    memory_id: str
    outcome_type: str  # CONFIRMED_SUCCESS, CONTRADICTION
    notes: Optional[str] = None

class OutcomeRecordResponse(BaseModel):
    outcome_id: str
    interaction_id: str
    memory_id: str
    new_confidence_level: str
    new_confidence_score: float
    supporting_count: int
    contradiction_count: int
