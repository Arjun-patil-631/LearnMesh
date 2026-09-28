import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class MemoryType(str, enum.Enum):
    EPISODE = "EPISODE"
    HUMAN_CORRECTION = "HUMAN_CORRECTION"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"
    LESSON_CANDIDATE = "LESSON_CANDIDATE"
    SHARED_LESSON = "SHARED_LESSON"
    CONTRADICTION = "CONTRADICTION"
    PATTERN = "PATTERN"

class ConfidenceLevel(str, enum.Enum):
    LIMITED = "Limited"
    MODERATE = "Moderate"
    STRONG = "Strong"

class AgentRole(str, enum.Enum):
    BILLING = "Billing Agent"
    SUPPORT = "Support Agent"
    ACCOUNT = "Account Management Agent"
    WAREHOUSE = "Warehouse Operations"

class MemoryReference(BaseModel):
    memory_id: str
    memory_type: MemoryType
    source_agent: str
    task_type: str
    context: str
    original_action: Optional[str] = None
    corrected_action: Optional[str] = None
    reason: Optional[str] = None
    lesson: str
    scope: List[str] = Field(default_factory=list)
    confidence_level: ConfidenceLevel = ConfidenceLevel.LIMITED
    confidence_score: float = Field(default=0.4, ge=0.0, le=1.0)
    supporting_outcomes_count: int = 0
    contradicting_outcomes_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_reinforced_at: Optional[datetime] = None
    hindsight_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class LessonCandidate(BaseModel):
    id: str
    interaction_id: str
    source_agent: str
    task_type: str
    context: str
    original_action: str
    correction_text: str
    reason: str
    extracted_lesson: str
    recommended_scope: List[str]
    is_validated: bool = False
    is_promoted: bool = False
    promoted_memory_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ValidationRequest(BaseModel):
    candidate_id: str
    confirmed_lesson: str
    target_scope: List[str]
    notes: Optional[str] = None

class PromotionResult(BaseModel):
    candidate_id: str
    memory_id: str
    hindsight_id: str
    lesson: str
    eligible_agents: List[str]
    ineligible_agents: List[str]
    confidence_level: ConfidenceLevel
    retained_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
