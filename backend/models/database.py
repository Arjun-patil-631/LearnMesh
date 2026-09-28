from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class AgentDB(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    capabilities = Column(JSON, default=list)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class InteractionDB(Base):
    __tablename__ = "interactions"

    id = Column(String, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    customer_id = Column(String, nullable=True)
    customer_tier = Column(String, default="standard")
    task_type = Column(String, nullable=False)
    user_prompt = Column(Text, nullable=False)
    agent_response = Column(Text, nullable=True)
    recalled_memory_ids = Column(JSON, default=list)
    status = Column(String, default="pending")  # completed, corrected, failed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class CorrectionDB(Base):
    __tablename__ = "corrections"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id"), nullable=False)
    correction_text = Column(Text, nullable=False)
    original_action = Column(Text, nullable=False)
    human_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class LessonCandidateDB(Base):
    __tablename__ = "lesson_candidates"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id"), nullable=False)
    source_agent = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    context = Column(Text, nullable=False)
    original_action = Column(Text, nullable=False)
    correction_text = Column(Text, nullable=False)
    extracted_lesson = Column(Text, nullable=False)
    recommended_scope = Column(JSON, default=list)
    is_validated = Column(Boolean, default=False)
    is_promoted = Column(Boolean, default=False)
    promoted_memory_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class MemoryReferenceDB(Base):
    """
    Metadata pointer linking to the Hindsight organizational memory unit.
    Note: SQLite stores audit pointers, lineage links, and tracking metrics.
    Actual memory retention and recall occur in Hindsight.
    """
    __tablename__ = "memory_references"

    memory_id = Column(String, primary_key=True, index=True)
    hindsight_id = Column(String, nullable=False, index=True)
    memory_type = Column(String, default="SHARED_LESSON")
    source_agent = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    context = Column(Text, nullable=False)
    lesson = Column(Text, nullable=False)
    scope = Column(JSON, default=list)
    confidence_level = Column(String, default="Limited")
    confidence_score = Column(Float, default=0.4)
    supporting_outcomes_count = Column(Integer, default=0)
    contradicting_outcomes_count = Column(Integer, default=0)
    candidate_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_reinforced_at = Column(DateTime, nullable=True)

class OutcomeDB(Base):
    __tablename__ = "outcomes"

    id = Column(String, primary_key=True, index=True)
    interaction_id = Column(String, ForeignKey("interactions.id"), nullable=False)
    memory_id = Column(String, ForeignKey("memory_references.memory_id"), nullable=True)
    outcome_type = Column(String, nullable=False)  # CONFIRMED_SUCCESS, CONTRADICTION
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class SystemEventDB(Base):
    __tablename__ = "system_events"

    id = Column(String, primary_key=True, index=True)
    event_type = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    memory_id = Column(String, nullable=True)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
