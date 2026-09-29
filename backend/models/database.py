import uuid
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, ForeignKey, JSON, Index, func, text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def utc_now():
    return datetime.now(timezone.utc)

# ------------------------------------------------------------------------------
# User & RBAC Authentication Model (Phase 3 & 4)
# ------------------------------------------------------------------------------
class UserDB(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, default="OPERATOR", nullable=False)  # ADMIN, REVIEWER, OPERATOR, AUDITOR, VIEWER
    tenant_id = Column(String, default="tenant-default", nullable=False, index=True)
    department = Column(String, nullable=True)
    trust_score = Column(Float, default=1.0, nullable=False)  # 0.0 to 1.0 (poisoning defense)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

# ------------------------------------------------------------------------------
# Fleet Agent Definition Model
# ------------------------------------------------------------------------------
class AgentDB(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    department = Column(String, default="Operations", nullable=False)
    capabilities = Column(JSON, default=list)
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    tools = Column(JSON, default=list)  # Registered tools for tool-use
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    interactions = relationship("InteractionDB", back_populates="agent", cascade="all, delete-orphan")

# ------------------------------------------------------------------------------
# Agent Interactions & Reasoning Traces (Phase 5)
# ------------------------------------------------------------------------------
class InteractionDB(Base):
    __tablename__ = "interactions"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(String, default="tenant-default", nullable=False, index=True)
    customer_id = Column(String, nullable=True, index=True)
    customer_tier = Column(String, default="standard", index=True)
    task_type = Column(String, nullable=False, index=True)
    user_prompt = Column(Text, nullable=False)
    agent_response = Column(Text, nullable=True)
    recalled_memory_ids = Column(JSON, default=list)
    rejected_memory_ids = Column(JSON, default=list)  # Memories evaluated but rejected
    execution_trace = Column(JSON, default=dict)      # 7-step decomposed reasoning trace
    tool_calls = Column(JSON, default=list)           # Actions executed or simulated
    status = Column(String, default="completed", index=True)  # completed, corrected, failed, escalated
    reasoning_mode = Column(String, default="LIVE_AI_GROQ")
    cost_usd = Column(Float, default=0.0)
    tokens_used = Column(Integer, default=0)
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    agent = relationship("AgentDB", back_populates="interactions")
    corrections = relationship("CorrectionDB", back_populates="interaction", cascade="all, delete-orphan")
    candidates = relationship("LessonCandidateDB", back_populates="interaction", cascade="all, delete-orphan")
    outcomes = relationship("OutcomeDB", back_populates="interaction", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_interactions_agent_created", "agent_id", "created_at"),
        Index("ix_interactions_task_tier", "task_type", "customer_tier"),
    )

class CorrectionDB(Base):
    __tablename__ = "corrections"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False, index=True)
    corrector_id = Column(String, nullable=True)
    correction_text = Column(Text, nullable=False)
    original_action = Column(Text, nullable=False)
    human_reason = Column(Text, nullable=True)
    sanitized_text = Column(Text, nullable=True)  # Prompt-injection & PII redacted
    is_quarantined = Column(Boolean, default=False, nullable=False)  # Anomaly/poisoning defense
    quarantine_reason = Column(String, nullable=True)
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    interaction = relationship("InteractionDB", back_populates="corrections")

# ------------------------------------------------------------------------------
# Lesson Candidate Model with Multi-tier Generalization Proposals (Phase 2)
# ------------------------------------------------------------------------------
class LessonCandidateDB(Base):
    __tablename__ = "lesson_candidates"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_agent = Column(String, nullable=False, index=True)
    task_type = Column(String, nullable=False, index=True)
    context = Column(Text, nullable=False)
    original_action = Column(Text, nullable=False)
    correction_text = Column(Text, nullable=False)
    extracted_lesson = Column(Text, nullable=False)
    # Generalization options proposed by AI
    specific_rule = Column(Text, nullable=True)
    generalized_rule = Column(Text, nullable=True)
    is_negative_guardrail = Column(Boolean, default=False, nullable=False)
    recommended_scope = Column(JSON, default=list)
    suggested_conditions = Column(JSON, default=dict)
    risk_level = Column(String, default="LOW", index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    requires_two_person_approval = Column(Boolean, default=False)
    is_validated = Column(Boolean, default=False, index=True)
    is_promoted = Column(Boolean, default=False, index=True)
    promoted_memory_id = Column(String, nullable=True, index=True)
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    interaction = relationship("InteractionDB", back_populates="candidates")

    __table_args__ = (
        Index("ix_candidates_validated_promoted", "is_validated", "is_promoted"),
    )

# ------------------------------------------------------------------------------
# Core Organizational Memory Model (Phase 2, 3, 4)
# ------------------------------------------------------------------------------
class MemoryReferenceDB(Base):
    """
    Authoritative metadata pointer linking to the Hindsight organizational memory unit.
    Features:
      - Explicit state machine (candidate, verified, active, under_review, deprecated, superseded, archived)
      - Immutability & versioning pointer
      - Confidence 2.0 with multidimensional breakdown & plain language explanation
      - Hierarchical scope (tenant -> department -> agent -> segment) + predicate conditions
      - Freshness validity (valid_from, valid_until)
      - Negative guardrail precedence
    """
    __tablename__ = "memory_references"

    memory_id = Column(String, primary_key=True, index=True)
    hindsight_id = Column(String, nullable=True, index=True)
    memory_type = Column(String, default="SHARED_LESSON", index=True)  # SHARED_LESSON, NEGATIVE_GUARDRAIL, POLICY
    state = Column(String, default="active", nullable=False, index=True)  # candidate, verified, active, under_review, deprecated, superseded, archived
    version = Column(Integer, default=1, nullable=False)
    parent_memory_id = Column(String, nullable=True, index=True)
    root_memory_id = Column(String, nullable=True, index=True)
    source_agent = Column(String, nullable=False, index=True)
    task_type = Column(String, nullable=False, index=True)
    context = Column(Text, nullable=False)
    lesson = Column(Text, nullable=False)
    # Hierarchical scope & predicates
    tenant_id = Column(String, default="tenant-default", nullable=False, index=True)
    department = Column(String, nullable=True, index=True)
    scope = Column(JSON, default=list)         # List of agent names
    customer_segments = Column(JSON, default=list) # e.g. ["enterprise", "strategic"]
    conditions = Column(JSON, default=dict)    # Predicates: {"min_amount": 1000, "region": "US"}
    priority = Column(Integer, default=0, index=True) # Negative guardrails = 100+
    is_negative_guardrail = Column(Boolean, default=False, nullable=False, index=True)

    # Confidence 2.0
    confidence_level = Column(String, default="Limited", index=True)
    confidence_score = Column(Float, default=0.40, index=True)
    confidence_breakdown = Column(JSON, default=dict)  # {"base": 0.4, "verifier_boost": 0.15, "confirmations": 0.3, "decay": -0.05, "explanation": "..."}
    verifier_role = Column(String, default="OPERATOR")
    supporting_outcomes_count = Column(Integer, default=0)
    contradicting_outcomes_count = Column(Integer, default=0)
    candidate_id = Column(String, nullable=True, index=True)

    # Freshness & Expiry
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    review_due_at = Column(DateTime(timezone=True), nullable=True)
    is_stale = Column(Boolean, default=False, index=True)

    # Deduplication & Merge Lineage
    merged_source_ids = Column(JSON, default=list)  # IDs of previous lessons merged into this one
    dedup_cluster_id = Column(String, nullable=True)

    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)
    last_reinforced_at = Column(DateTime(timezone=True), nullable=True)

    outcomes = relationship("OutcomeDB", back_populates="memory_reference")
    versions = relationship("MemoryVersionDB", back_populates="memory", cascade="all, delete-orphan")
    transitions = relationship("MemoryTransitionAuditDB", back_populates="memory", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_memory_task_confidence", "task_type", "confidence_score"),
        Index("ix_memory_tenant_state", "tenant_id", "state"),
    )

# ------------------------------------------------------------------------------
# Immutable Version History for Rollback & Auditing (Phase 2)
# ------------------------------------------------------------------------------
class MemoryVersionDB(Base):
    __tablename__ = "memory_versions"

    id = Column(String, primary_key=True, index=True)
    memory_id = Column(String, ForeignKey("memory_references.memory_id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    lesson = Column(Text, nullable=False)
    context = Column(Text, nullable=False)
    scope = Column(JSON, default=list)
    conditions = Column(JSON, default=dict)
    diff_summary = Column(Text, nullable=True)
    changed_by = Column(String, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)

    memory = relationship("MemoryReferenceDB", back_populates="versions")

    __table_args__ = (
        Index("ix_memory_versions_lookup", "memory_id", "version_number"),
    )

# ------------------------------------------------------------------------------
# Explicit State Machine Transition Audit Trail (Phase 2)
# ------------------------------------------------------------------------------
class MemoryTransitionAuditDB(Base):
    __tablename__ = "memory_transitions"

    id = Column(String, primary_key=True, index=True)
    memory_id = Column(String, ForeignKey("memory_references.memory_id", ondelete="CASCADE"), nullable=False, index=True)
    from_state = Column(String, nullable=False)
    to_state = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    actor_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)

    memory = relationship("MemoryReferenceDB", back_populates="transitions")

# ------------------------------------------------------------------------------
# Contradiction Governance & Human Review Queue (Phase 3)
# ------------------------------------------------------------------------------
class ContradictionReviewDB(Base):
    __tablename__ = "contradiction_reviews"

    id = Column(String, primary_key=True, index=True)
    memory_id_a = Column(String, nullable=False, index=True)
    memory_id_b = Column(String, nullable=False, index=True)
    conflict_type = Column(String, default="direct", nullable=False)  # direct, partial, scope_overlap, temporal
    severity = Column(String, default="HIGH", nullable=False)         # HIGH, MEDIUM, LOW
    evidence = Column(Text, nullable=True)
    precedence_rule = Column(String, nullable=True)
    auto_resolved = Column(Boolean, default=False)
    status = Column(String, default="pending_review", nullable=False, index=True)  # pending_review, resolved, escalated
    assigned_to = Column(String, nullable=True)
    resolution = Column(String, nullable=True)  # KEEP_A, KEEP_B, MERGE, SCOPE_SPLIT, BOTH_VALID_WITH_CONDITIONS
    justification = Column(Text, nullable=True)
    sla_due_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

# ------------------------------------------------------------------------------
# Two-Person Approval Workflows for High-Risk Lessons (Phase 3)
# ------------------------------------------------------------------------------
class ApprovalRequestDB(Base):
    __tablename__ = "approval_requests"

    id = Column(String, primary_key=True, index=True)
    candidate_id = Column(String, nullable=False, index=True)
    risk_category = Column(String, nullable=False)  # MONEY, LEGAL, SECURITY
    risk_score = Column(Float, default=0.8)
    requested_by = Column(String, nullable=False)
    status = Column(String, default="pending_first_approval", nullable=False, index=True)  # pending_first_approval, pending_second_approval, approved, rejected
    approver_1 = Column(String, nullable=True)
    approver_1_note = Column(Text, nullable=True)
    approver_1_at = Column(DateTime(timezone=True), nullable=True)
    approver_2 = Column(String, nullable=True)
    approver_2_note = Column(Text, nullable=True)
    approver_2_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

# ------------------------------------------------------------------------------
# Tamper-Evident Cryptographic Audit Log with Hash Chaining (Phase 3)
# ------------------------------------------------------------------------------
class AuditLogEntryDB(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, index=True)
    sequence_number = Column(Integer, unique=True, nullable=False, index=True)
    previous_hash = Column(String, nullable=False)
    entry_hash = Column(String, nullable=False, index=True)  # SHA-256
    action = Column(String, nullable=False, index=True)      # e.g., LESSON_PROMOTED, STATE_CHANGED, CONFLICT_RESOLVED
    actor_id = Column(String, nullable=False)
    tenant_id = Column(String, default="tenant-default", nullable=False)
    target_entity = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)

    @staticmethod
    def compute_hash(seq: int, prev_hash: str, action: str, actor: str, entity: str, target: str, payload_str: str, ts: str) -> str:
        raw = f"{seq}|{prev_hash}|{action}|{actor}|{entity}|{target}|{payload_str}|{ts}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

# ------------------------------------------------------------------------------
# Outbound Webhook Subscriptions (Phase 8)
# ------------------------------------------------------------------------------
class WebhookSubscriptionDB(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(String, primary_key=True, index=True)
    url = Column(String, nullable=False)
    secret = Column(String, nullable=False)  # For HMAC-SHA256 signature
    subscribed_events = Column(JSON, default=list)  # ["lesson.verified", "contradiction.detected", "memory.deprecated"]
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)

# ------------------------------------------------------------------------------
# Action Guardrail Audit Log (Phase 4)
# ------------------------------------------------------------------------------
class AgentActionAuditDB(Base):
    __tablename__ = "agent_action_audits"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    action_name = Column(String, nullable=False)  # e.g. "process_refund"
    parameters = Column(JSON, default=dict)
    execution_mode = Column(String, default="execute")  # dry_run, execute
    exceeds_confirmation_threshold = Column(Boolean, default=False)
    threshold_amount = Column(Float, nullable=True)
    status = Column(String, default="executed")  # simulated, executed, blocked, pending_approval
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)

# ------------------------------------------------------------------------------
# Outcomes & Systems Events (Preserved & Enhanced)
# ------------------------------------------------------------------------------
class OutcomeDB(Base):
    __tablename__ = "outcomes"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_id = Column(String, ForeignKey("memory_references.memory_id", ondelete="SET NULL"), nullable=True, index=True)
    outcome_type = Column(String, nullable=False, index=True)  # CONFIRMED_SUCCESS, CONTRADICTION
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, server_default=text("0"), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)

    interaction = relationship("InteractionDB", back_populates="outcomes")
    memory_reference = relationship("MemoryReferenceDB", back_populates="outcomes")

class SystemEventDB(Base):
    __tablename__ = "system_events"

    id = Column(String, primary_key=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=True, index=True)
    memory_id = Column(String, nullable=True, index=True)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_events_type_created", "event_type", "created_at"),
    )

class JobDB(Base):
    __tablename__ = "background_jobs"

    id = Column(String, primary_key=True, index=True)
    job_type = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", nullable=False, index=True)  # pending, running, completed, failed, dead_letter
    payload = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

class IdempotencyRecordDB(Base):
    __tablename__ = "idempotency_records"

    idempotency_key = Column(String, primary_key=True, index=True)
    request_path = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    status_code = Column(Integer, nullable=False)
    response_headers = Column(JSON, default=dict)
    response_body = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
