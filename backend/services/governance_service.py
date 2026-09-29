import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.models.database import (
    ApprovalRequestDB,
    AuditLogEntryDB,
    LessonCandidateDB,
    MemoryReferenceDB
)
from backend.utils.exceptions import ValidationError, ResourceNotFoundError

GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"

class GovernanceService:
    @staticmethod
    def record_audit_entry(
        db: Session,
        action: str,
        actor_id: str,
        tenant_id: str,
        target_entity: str,
        target_id: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> AuditLogEntryDB:
        """
        Appends an entry to the tamper-evident SHA-256 hash chain audit log.
        H_i = SHA256(previous_hash + sequence_number + action + actor_id + tenant_id + target_id + timestamp + payload_json)
        """
        # Get latest entry to find previous hash and sequence number
        last_entry = db.query(AuditLogEntryDB).order_by(desc(AuditLogEntryDB.sequence_number)).first()
        if last_entry:
            prev_hash = last_entry.entry_hash
            seq_num = last_entry.sequence_number + 1
        else:
            prev_hash = GENESIS_HASH
            seq_num = 1

        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        payload_data = payload or {}
        payload_str = json.dumps(payload_data, sort_keys=True, default=str)

        # Compute tamper-evident hash
        raw_string = f"{prev_hash}:{seq_num}:{action}:{actor_id}:{tenant_id}:{target_entity}:{target_id}:{timestamp_str}:{payload_str}"
        entry_hash = hashlib.sha256(raw_string.encode("utf-8")).hexdigest()

        entry = AuditLogEntryDB(
            id=f"audit-{uuid.uuid4().hex[:12]}",
            sequence_number=seq_num,
            previous_hash=prev_hash,
            entry_hash=entry_hash,
            action=action,
            actor_id=actor_id,
            tenant_id=tenant_id,
            target_entity=target_entity,
            target_id=target_id,
            payload=payload_data,
            created_at=now
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def verify_audit_integrity(db: Session) -> Dict[str, Any]:
        """
        Cryptographically verifies the entire audit log hash chain from genesis to head.
        Returns whether the chain is unbroken or identifies the compromised sequence.
        """
        entries = db.query(AuditLogEntryDB).order_by(AuditLogEntryDB.sequence_number.asc()).all()
        if not entries:
            return {
                "is_valid": True,
                "total_entries": 0,
                "status": "EMPTY",
                "message": "Audit log is empty, integrity intact."
            }

        prev_expected_hash = GENESIS_HASH
        for idx, entry in enumerate(entries):
            # Check previous hash link
            if entry.previous_hash != prev_expected_hash:
                return {
                    "is_valid": False,
                    "total_entries": len(entries),
                    "broken_at_sequence": entry.sequence_number,
                    "reason": f"Hash chain broken at sequence {entry.sequence_number}: expected prev {prev_expected_hash}, found {entry.previous_hash}"
                }

            # Recalculate hash of current entry
            payload_str = json.dumps(entry.payload or {}, sort_keys=True, default=str)
            timestamp_str = entry.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(entry.created_at, "strftime") else str(entry.created_at)
            raw_string = f"{entry.previous_hash}:{entry.sequence_number}:{entry.action}:{entry.actor_id}:{entry.tenant_id}:{entry.target_entity}:{entry.target_id}:{timestamp_str}:{payload_str}"
            recomputed = hashlib.sha256(raw_string.encode("utf-8")).hexdigest()

            if recomputed != entry.entry_hash:
                return {
                    "is_valid": False,
                    "total_entries": len(entries),
                    "broken_at_sequence": entry.sequence_number,
                    "reason": f"Tampering detected at sequence {entry.sequence_number}: computed hash mismatch."
                }

            prev_expected_hash = entry.entry_hash

        return {
            "is_valid": True,
            "total_entries": len(entries),
            "head_hash": entries[-1].entry_hash,
            "status": "VERIFIED_UNBROKEN",
            "message": f"All {len(entries)} audit log entries verified successfully with unbroken cryptographic hash chain."
        }

    @staticmethod
    def create_approval_request(
        db: Session,
        candidate_id: str,
        risk_category: str,
        risk_score: float,
        requested_by: str
    ) -> ApprovalRequestDB:
        req = ApprovalRequestDB(
            id=f"appr-{uuid.uuid4().hex[:12]}",
            candidate_id=candidate_id,
            risk_category=risk_category,
            risk_score=risk_score,
            requested_by=requested_by,
            status="pending",
            created_at=datetime.now(timezone.utc)
        )
        db.add(req)
        db.commit()
        db.refresh(req)

        GovernanceService.record_audit_entry(
            db=db,
            action="APPROVAL_REQUESTED",
            actor_id=requested_by,
            tenant_id="default",
            target_entity="ApprovalRequest",
            target_id=req.id,
            payload={"candidate_id": candidate_id, "risk_category": risk_category}
        )
        return req

    @staticmethod
    def process_approval_action(
        db: Session,
        request_id: str,
        approver_id: str,
        action: str,  # approve or reject
        note: Optional[str] = None
    ) -> Tuple[ApprovalRequestDB, bool]:
        """
        Enforces two-person approval rule:
        - Approver 1 approves
        - Approver 2 must be a different human than Approver 1 and the requester!
        - If action is reject, immediately marks request as rejected.
        Returns (request, is_fully_approved)
        """
        req = db.query(ApprovalRequestDB).filter(ApprovalRequestDB.id == request_id).first()
        if not req:
            raise ResourceNotFoundError(f"Approval request '{request_id}' not found.")

        if req.status != "pending":
            raise ValidationError(f"Approval request is already '{req.status}'.")

        now = datetime.now(timezone.utc)

        if action.lower() == "reject":
            req.status = "rejected"
            req.completed_at = now
            if not req.approver_1:
                req.approver_1 = approver_id
                req.approver_1_note = note
                req.approver_1_at = now
            else:
                req.approver_2 = approver_id
                req.approver_2_note = note
                req.approver_2_at = now
            db.commit()
            db.refresh(req)

            GovernanceService.record_audit_entry(
                db=db,
                action="APPROVAL_REJECTED",
                actor_id=approver_id,
                tenant_id="default",
                target_entity="ApprovalRequest",
                target_id=req.id,
                payload={"note": note}
            )
            return req, False

        # If action is approve:
        if not req.approver_1:
            # First approval
            req.approver_1 = approver_id
            req.approver_1_note = note
            req.approver_1_at = now
            db.commit()
            db.refresh(req)

            GovernanceService.record_audit_entry(
                db=db,
                action="APPROVAL_STEP_1_GRANTED",
                actor_id=approver_id,
                tenant_id="default",
                target_entity="ApprovalRequest",
                target_id=req.id,
                payload={"note": note}
            )
            return req, False
        else:
            # Second approval: MUST NOT be the same person as approver 1
            if approver_id == req.approver_1:
                raise ValidationError("Second approval must be granted by a different verifier (two-person rule).")

            req.approver_2 = approver_id
            req.approver_2_note = note
            req.approver_2_at = now
            req.status = "approved"
            req.completed_at = now
            db.commit()
            db.refresh(req)

            GovernanceService.record_audit_entry(
                db=db,
                action="APPROVAL_FULLY_GRANTED",
                actor_id=approver_id,
                tenant_id="default",
                target_entity="ApprovalRequest",
                target_id=req.id,
                payload={"approver_1": req.approver_1, "approver_2": approver_id}
            )
            return req, True
