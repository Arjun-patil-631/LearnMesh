import difflib
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    MemoryReferenceDB,
    MemoryVersionDB,
    MemoryTransitionAuditDB
)
from backend.schemas.enterprise_schemas import MemoryState
from backend.utils.exceptions import ValidationError, ResourceNotFoundError

VALID_TRANSITIONS: Dict[str, List[str]] = {
    MemoryState.CANDIDATE.value: [MemoryState.VERIFIED.value, MemoryState.ARCHIVED.value],
    MemoryState.VERIFIED.value: [MemoryState.ACTIVE.value, MemoryState.UNDER_REVIEW.value, MemoryState.ARCHIVED.value],
    MemoryState.ACTIVE.value: [MemoryState.UNDER_REVIEW.value, MemoryState.DEPRECATED.value, MemoryState.SUPERSEDED.value, MemoryState.ARCHIVED.value],
    MemoryState.UNDER_REVIEW.value: [MemoryState.ACTIVE.value, MemoryState.DEPRECATED.value, MemoryState.SUPERSEDED.value, MemoryState.ARCHIVED.value],
    MemoryState.DEPRECATED.value: [MemoryState.ACTIVE.value, MemoryState.ARCHIVED.value],
    MemoryState.SUPERSEDED.value: [MemoryState.ARCHIVED.value],
    MemoryState.ARCHIVED.value: []
}

class LifecycleService:
    @staticmethod
    def transition_state(
        db: Session,
        memory_id: str,
        to_state: MemoryState,
        actor_id: str = "system",
        reason: Optional[str] = None
    ) -> MemoryReferenceDB:
        memory = db.query(MemoryReferenceDB).filter(
            MemoryReferenceDB.memory_id == memory_id,
            MemoryReferenceDB.is_deleted == False
        ).first()

        if not memory:
            raise ResourceNotFoundError(f"Memory with ID '{memory_id}' not found.")

        current_state = memory.state
        target_state = to_state.value if isinstance(to_state, MemoryState) else str(to_state)

        # Allow no-op transition if already in target state
        if current_state == target_state:
            return memory

        allowed = VALID_TRANSITIONS.get(current_state, [])
        if target_state not in allowed:
            raise ValidationError(
                f"Invalid memory state transition from '{current_state}' to '{target_state}'. "
                f"Allowed transitions: {allowed}"
            )

        # Apply state transition
        memory.state = target_state
        memory.updated_at = datetime.now(timezone.utc)

        # Record audit trail
        audit_entry = MemoryTransitionAuditDB(
            id=f"trans-{uuid.uuid4().hex[:12]}",
            memory_id=memory.memory_id,
            from_state=current_state,
            to_state=target_state,
            reason=reason or f"Transitioned to {target_state}",
            actor_id=actor_id,
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit_entry)
        db.commit()
        db.refresh(memory)
        return memory

    @staticmethod
    def create_version_snapshot(
        db: Session,
        memory: MemoryReferenceDB,
        new_lesson: str,
        new_context: Optional[str] = None,
        new_scope: Optional[List[str]] = None,
        new_conditions: Optional[Dict[str, Any]] = None,
        changed_by: str = "system",
        change_reason: Optional[str] = None,
        is_initial: bool = False
    ) -> MemoryVersionDB:
        """
        Creates an immutable version snapshot of the memory reference with diff computation.
        """
        current_version = memory.version or 1
        version_number = 1 if is_initial else (current_version + 1)

        # Compute diff summary
        old_text = memory.lesson or ""
        diff_lines = list(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_lesson.splitlines(keepends=True),
            fromfile=f"v{current_version}",
            tofile=f"v{version_number}",
            n=1
        ))
        diff_summary = "".join(diff_lines) if diff_lines else ("Initial memory version." if is_initial else "No textual differences in lesson.")

        version_entry = MemoryVersionDB(
            id=f"ver-{uuid.uuid4().hex[:12]}",
            memory_id=memory.memory_id,
            version_number=version_number,
            lesson=new_lesson,
            context=new_context or memory.context,
            scope=new_scope if new_scope is not None else memory.scope,
            conditions=new_conditions if new_conditions is not None else memory.conditions,
            diff_summary=diff_summary,
            changed_by=changed_by,
            change_reason=change_reason or ("Initial version created" if is_initial else "Lesson content updated"),
            created_at=datetime.now(timezone.utc)
        )
        db.add(version_entry)

        # Update memory reference current pointers
        memory.lesson = new_lesson
        if new_context:
            memory.context = new_context
        if new_scope is not None:
            memory.scope = new_scope
        if new_conditions is not None:
            memory.conditions = new_conditions
        memory.version = version_number
        memory.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(memory)
        return version_entry

    @staticmethod
    def rollback_memory(
        db: Session,
        memory_id: str,
        target_version_number: int,
        actor_id: str = "admin",
        reason: Optional[str] = None
    ) -> MemoryReferenceDB:
        """
        Rolls back memory content to an earlier version, creating a new head version snapshot.
        """
        memory = db.query(MemoryReferenceDB).filter(
            MemoryReferenceDB.memory_id == memory_id,
            MemoryReferenceDB.is_deleted == False
        ).first()

        if not memory:
            raise ResourceNotFoundError(f"Memory with ID '{memory_id}' not found.")

        target_version = db.query(MemoryVersionDB).filter(
            MemoryVersionDB.memory_id == memory_id,
            MemoryVersionDB.version_number == target_version_number
        ).first()

        if not target_version:
            raise ResourceNotFoundError(
                f"Version {target_version_number} for memory '{memory_id}' not found."
            )

        # Create a new version snapshot restoring earlier content
        rollback_reason = reason or f"Rollback to version {target_version_number}"
        LifecycleService.create_version_snapshot(
            db=db,
            memory=memory,
            new_lesson=target_version.lesson,
            new_context=target_version.context,
            new_scope=target_version.scope,
            new_conditions=target_version.conditions,
            changed_by=actor_id,
            change_reason=rollback_reason
        )
        return memory

    @staticmethod
    def check_and_flag_stale_memories(
        db: Session,
        inactivity_days: int = 90
    ) -> List[MemoryReferenceDB]:
        """
        Scans active memories; if past review date or inactive beyond threshold,
        flags them as stale and transitions to under_review.
        """
        now = datetime.now(timezone.utc)
        threshold_date = now - timedelta(days=inactivity_days)

        active_memories = db.query(MemoryReferenceDB).filter(
            MemoryReferenceDB.state.in_([MemoryState.ACTIVE.value, MemoryState.VERIFIED.value]),
            MemoryReferenceDB.is_deleted == False
        ).all()

        flagged = []
        for mem in active_memories:
            ref_date = mem.last_reinforced_at or mem.created_at
            if ref_date and ref_date.tzinfo is None:
                ref_date = ref_date.replace(tzinfo=timezone.utc)

            is_overdue = mem.review_due_at and (mem.review_due_at.replace(tzinfo=timezone.utc) if mem.review_due_at.tzinfo is None else mem.review_due_at) < now
            is_inactive = ref_date and ref_date < threshold_date

            if is_overdue or is_inactive:
                mem.is_stale = True
                mem.state = MemoryState.UNDER_REVIEW.value
                mem.updated_at = now
                flagged.append(mem)

        if flagged:
            db.commit()

        return flagged
