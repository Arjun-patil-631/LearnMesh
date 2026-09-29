import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class MemoryState(str, enum.Enum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

class PolicyPrecedence(int, enum.Enum):
    LEGAL_COMPLIANCE = 100
    CORPORATE_POLICY = 80
    DEPARTMENT_STANDARD = 60
    AGENT_SPECIFIC = 40
    ADHOC_TIP = 20

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    LEAD_VERIFIER = "lead_verifier"
    VERIFIER = "verifier"
    AGENT_OPERATOR = "agent_operator"
    AUDITOR = "auditor"

class ConflictType(str, enum.Enum):
    DIRECT_OPPOSITION = "DIRECT_OPPOSITION"
    VALUE_RANGE_CONFLICT = "VALUE_RANGE_CONFLICT"
    PREDICATE_CONFLICT = "PREDICATE_CONFLICT"
    SEMANTIC_OVERLAP = "SEMANTIC_OVERLAP"

class ConflictSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class ScopeDefinition(BaseModel):
    tenants: List[str] = Field(default_factory=lambda: ["*"])
    departments: List[str] = Field(default_factory=list)
    agents: List[str] = Field(default_factory=list)
    customer_segments: List[str] = Field(default_factory=list)
    predicates: Dict[str, Any] = Field(default_factory=dict)

class ConfidenceBreakdown(BaseModel):
    base_score: float = 0.4
    verifier_role: str = "verifier"
    verifier_weight: float = 0.7
    supporting_bonus: float = 0.0
    contradiction_penalty: float = 0.0
    time_decay_penalty: float = 0.0
    final_score: float = 0.4
    explanation: str = "Initial baseline confidence"

class MemoryVersionView(BaseModel):
    id: str
    memory_id: str
    version_number: int
    lesson: str
    context: str
    scope: List[str]
    conditions: Dict[str, Any]
    diff_summary: Optional[str] = None
    changed_by: Optional[str] = None
    change_reason: Optional[str] = None
    created_at: datetime

class MemoryTransitionView(BaseModel):
    id: str
    memory_id: str
    from_state: str
    to_state: str
    reason: Optional[str] = None
    actor_id: Optional[str] = None
    created_at: datetime

class ContradictionReviewView(BaseModel):
    id: str
    memory_id_a: str
    memory_id_b: str
    conflict_type: str
    severity: str
    evidence: Optional[str] = None
    precedence_rule: Optional[str] = None
    auto_resolved: bool = False
    status: str
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None
    justification: Optional[str] = None
    sla_due_at: Optional[datetime] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

class ContradictionResolutionRequest(BaseModel):
    resolution: str  # SUPERSEDE_A, SUPERSEDE_B, MERGE, EXCEPTION_RULE, DISMISS
    justification: str
    actor_id: str = "admin"

class ApprovalRequestView(BaseModel):
    id: str
    candidate_id: str
    risk_category: str
    risk_score: float
    requested_by: str
    status: str  # pending, approved, rejected
    approver_1: Optional[str] = None
    approver_1_note: Optional[str] = None
    approver_1_at: Optional[datetime] = None
    approver_2: Optional[str] = None
    approver_2_note: Optional[str] = None
    approver_2_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

class ApprovalActionRequest(BaseModel):
    approver_id: str
    action: str  # approve, reject
    note: Optional[str] = None

class PipelineStepTrace(BaseModel):
    step_name: str
    status: str  # SUCCESS, SKIPPED, WARNING, FAILED
    duration_ms: float
    details: Dict[str, Any] = Field(default_factory=dict)

class ExecutionTrace(BaseModel):
    trace_id: str
    agent_id: str
    interaction_id: str
    total_duration_ms: float
    steps: List[PipelineStepTrace] = Field(default_factory=list)
    accepted_memory_ids: List[str] = Field(default_factory=list)
    rejected_memory_ids: List[str] = Field(default_factory=list)
    rejection_reasons: Dict[str, str] = Field(default_factory=dict)
    tokens_used: int = 0
    estimated_cost_usd: float = 0.0

class ActionExecutionRequest(BaseModel):
    action_name: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    mode: str = "dry_run"  # dry_run or execute
    interaction_id: Optional[str] = None
    agent_id: str = "Billing Agent"
    user_confirmed: bool = False

class ActionExecutionResult(BaseModel):
    action_id: str
    action_name: str
    mode: str
    status: str  # SIMULATED, EXECUTED, REQUIRES_CONFIRMATION, BLOCKED
    threshold_exceeded: bool = False
    threshold_amount: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class BenchmarkScenario(BaseModel):
    scenario_id: str
    name: str
    description: str
    agent_id: str
    prompt: str
    task_type: str
    customer_tier: str
    expected_policy_keyword: str
    negative_guardrail_check: Optional[str] = None
    requires_approval: bool = False

class BenchmarkRunResult(BaseModel):
    scenario_id: str
    scenario_name: str
    agent_id: str
    policy_compliant: bool
    negative_guardrail_passed: bool
    response_snippet: str
    memories_used_count: int
    duration_ms: float

class ROIMetrics(BaseModel):
    total_evaluations: int
    policy_compliance_rate: float
    repeat_mistake_reduction_rate: float
    contradictions_avoided_count: int
    knowledge_propagation_speed_seconds: float
    estimated_hours_saved: float
    cost_avoided_usd: float

class EvaluationSuiteResponse(BaseModel):
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: str  # BEFORE_LEARNING, AFTER_LEARNING, A_B_COMPARISON
    total_scenarios: int
    passed_scenarios: int
    compliance_rate: float
    metrics: ROIMetrics
    scenario_results: List[BenchmarkRunResult]

class WebhookSubscriptionCreate(BaseModel):
    url: str
    secret: Optional[str] = None
    subscribed_events: List[str] = Field(default_factory=lambda: ["memory.created", "memory.verified", "contradiction.detected"])

class WebhookSubscriptionView(BaseModel):
    id: str
    url: str
    subscribed_events: List[str]
    is_active: bool
    created_at: datetime

class SOPImportRequest(BaseModel):
    content: str
    format: str = "markdown"  # markdown or json
    department: str = "General"
    target_agents: List[str] = Field(default_factory=list)
    auto_verify: bool = False
    verifier_id: str = "admin"
