import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from backend.models.database import IdempotencyRecordDB
from backend.utils.config import settings

def calculate_request_hash(path: str, body_bytes: bytes) -> str:
    """Computes a SHA-256 hash of the request path and serialized body."""
    hasher = hashlib.sha256()
    hasher.update(path.encode("utf-8"))
    hasher.update(body_bytes)
    return hasher.hexdigest()

def check_idempotency(
    db: Session,
    idempotency_key: Optional[str],
    request_path: str,
    body_dict: Any
) -> Optional[JSONResponse]:
    """
    Checks if a request with this idempotency key was previously executed.
    If cached, returns the stored JSONResponse immediately.
    """
    if not settings.FEATURE_IDEMPOTENCY or not idempotency_key:
        return None

    record = db.query(IdempotencyRecordDB).filter(
        IdempotencyRecordDB.idempotency_key == idempotency_key
    ).first()

    if not record:
        return None

    # Check if expired (handling SQLite offset-naive timestamps gracefully)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < datetime.now(timezone.utc):
        db.delete(record)
        db.commit()
        return None

    # Return cached response
    return JSONResponse(
        status_code=record.status_code,
        content=record.response_body,
        headers={"x-cache-lookup": "HIT-IDEMPOTENCY"}
    )

def store_idempotency_record(
    db: Session,
    idempotency_key: Optional[str],
    request_path: str,
    body_dict: Any,
    status_code: int,
    response_body: Any,
    ttl_hours: int = 24
):
    """Saves the completed response for an idempotency key using jsonable_encoder."""
    if not settings.FEATURE_IDEMPOTENCY or not idempotency_key:
        return

    req_hash = calculate_request_hash(request_path, json.dumps(jsonable_encoder(body_dict or {}), sort_keys=True).encode("utf-8"))
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)

    record = IdempotencyRecordDB(
        idempotency_key=idempotency_key,
        request_path=request_path,
        request_hash=req_hash,
        status_code=status_code,
        response_body=jsonable_encoder(response_body),
        created_at=now,
        expires_at=expires_at
    )
    try:
        db.merge(record)
        db.commit()
    except Exception:
        db.rollback()
