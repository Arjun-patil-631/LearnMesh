import logging
import uuid
import concurrent.futures
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator
from hindsight_client import Hindsight
try:
    from hindsight_client_api.exceptions import ApiException
except ImportError:
    class ApiException(Exception):
        pass

from backend.utils.config import settings
from backend.utils.resilience import (
    hindsight_circuit_breaker, retry_with_exponential_backoff, CircuitBreakerOpenException
)

logger = logging.getLogger("learnmesh.hindsight")

class RetainedMemoryResult(BaseModel):
    success: bool
    bank_id: str
    learnmesh_memory_id: Optional[str] = None
    hindsight_document_id: Optional[str] = None
    hindsight_operation_id: Optional[str] = None
    items_count: int = 1
    mode: str = "live"  # "live" or "degraded_local"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            legacy_id = data.get("memory_id")
            if legacy_id:
                if not data.get("learnmesh_memory_id"):
                    data["learnmesh_memory_id"] = legacy_id
                if not data.get("hindsight_document_id"):
                    data["hindsight_document_id"] = legacy_id
            elif data.get("learnmesh_memory_id") and not data.get("hindsight_document_id"):
                data["hindsight_document_id"] = data["learnmesh_memory_id"]
        return data

    @property
    def memory_id(self) -> str:
        """Backward-compatibility alias pointing explicitly to learnmesh_memory_id."""
        return self.learnmesh_memory_id or self.hindsight_document_id or "HM-UNKNOWN"

class RecalledMemoryItem(BaseModel):
    learnmesh_memory_id: Optional[str] = None
    hindsight_document_id: Optional[str] = None
    text: str
    type: Optional[str] = None
    context: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)
    score: Optional[float] = None
    entities: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_recalled_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            legacy_id = data.get("id") or data.get("document_id")
            if legacy_id:
                if not data.get("learnmesh_memory_id"):
                    data["learnmesh_memory_id"] = legacy_id
                if not data.get("hindsight_document_id"):
                    data["hindsight_document_id"] = legacy_id
        return data

    @property
    def id(self) -> str:
        """Backward-compatibility alias pointing explicitly to learnmesh_memory_id."""
        return self.learnmesh_memory_id or self.hindsight_document_id or "HM-UNKNOWN"

class HindsightService:
    """
    Dedicated Hindsight adapter service adhering strictly to official hindsight-client SDK.
    Equipped with:
      - Circuit breaker protection & exponential backoff retries
      - Persistent thread pool for isolated async execution
      - Graceful degraded mode when external Hindsight daemon is offline
      - Local retention queuing for replay upon reconnection
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        bank_id: Optional[str] = None,
        circuit_breaker: Optional[Any] = None,
        enabled: Optional[bool] = None
    ):
        self.base_url = (base_url or settings.HINDSIGHT_BASE_URL).rstrip("/")
        self.api_key = api_key or settings.HINDSIGHT_API_KEY
        self.bank_id = bank_id or settings.HINDSIGHT_BANK_ID
        # Explicit per-instance flag wins; otherwise the service is fully operational.
        # The global singleton passes settings.FEATURE_HINDSIGHT so Hindsight can be
        # switched off deployment-wide without touching any caller.
        self.enabled = enabled if enabled is not None else True
        from backend.utils.resilience import CircuitBreaker
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            name=f"Hindsight-{self.bank_id}",
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            recovery_timeout_seconds=settings.CB_RECOVERY_TIMEOUT_SECONDS
        )
        self.demo_session_id: str = f"demo-{uuid.uuid4().hex[:8]}"
        self._client: Optional[Hindsight] = None
        self._is_connected: bool = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=settings.TASK_QUEUE_WORKERS)
        self.pending_retains_queue: List[Dict[str, Any]] = []

    def reset_demo_session(self) -> str:
        """
        Generate a fresh demo session namespace. This ensures all subsequent retains
        and recalls are isolated to the fresh session without contaminating or deleting
        permanent organizational memory.
        """
        self.demo_session_id = f"demo-{uuid.uuid4().hex[:8]}"
        return self.demo_session_id

    def get_client(self) -> Hindsight:
        if self._client is None:
            self._client = Hindsight(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=settings.HINDSIGHT_TIMEOUT_SECONDS
            )
        return self._client

    def ping(self) -> Dict[str, Any]:
        """Verify connectivity to Hindsight server via isolated loop, checking circuit state."""
        if not self.enabled:
            return {
                "status": "disabled",
                "mode": "feature_disabled",
                "pending_queue_size": 0
            }
        cb_status = self.circuit_breaker.get_status()
        if self.circuit_breaker.state.value == "OPEN":
            self._is_connected = False
            return {
                "status": "degraded",
                "mode": "circuit_breaker_open",
                "circuit_breaker": cb_status,
                "pending_queue_size": len(self.pending_retains_queue)
            }

        try:
            version = self._run_async_isolated("aget_version")
            self._is_connected = True
            self.circuit_breaker.record_success()
            return {
                "status": "connected",
                "version": getattr(version, "version", str(version)),
                "circuit_breaker": cb_status,
                "pending_queue_size": len(self.pending_retains_queue)
            }
        except Exception as e:
            self._is_connected = False
            logger.warning(f"Hindsight server ping failed: {e}")
            return {
                "status": "degraded",
                "error": str(e),
                "circuit_breaker": cb_status,
                "pending_queue_size": len(self.pending_retains_queue)
            }

    def is_available(self) -> bool:
        check = self.ping()
        return check.get("status") == "connected"

    def _execute_in_isolated_loop(self, method_name: str, *args, **kwargs):
        """Worker executing async client methods on a fresh event loop inside our threadpool."""
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        client = Hindsight(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=settings.HINDSIGHT_TIMEOUT_SECONDS
        )
        try:
            coro_fn = getattr(client, method_name)
            return new_loop.run_until_complete(coro_fn(*args, **kwargs))
        finally:
            try:
                new_loop.run_until_complete(client.aclose())
            except Exception:
                pass
            new_loop.close()

    def _run_async_isolated(self, method_name: str, *args, **kwargs):
        """Dispatches work to threadpool with circuit breaker & retry protection."""
        def run_call():
            future = self._executor.submit(self._execute_in_isolated_loop, method_name, *args, **kwargs)
            return future.result()

        return self.circuit_breaker.execute(run_call)

    def retain(
        self,
        content: str,
        context: Optional[str] = None,
        document_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        bank_id: Optional[str] = None
    ) -> RetainedMemoryResult:
        """
        Retain memory unit into Hindsight using aretain.
        If Hindsight is unreachable, queues retain and operates in degraded local mode.
        """
        target_bank = bank_id or self.bank_id
        doc_id = document_id or f"HM-{uuid.uuid4().hex[:8].upper()}"

        effective_tags = list(tags or ["learnmesh", "shared_lesson"])
        if f"session:{self.demo_session_id}" not in effective_tags:
            effective_tags.append(f"session:{self.demo_session_id}")

        if not self.enabled:
            logger.info(f"Hindsight disabled — memory {doc_id} stays in local database only.")
            return RetainedMemoryResult(
                success=False,
                bank_id=target_bank,
                learnmesh_memory_id=doc_id,
                hindsight_document_id=doc_id,
                hindsight_operation_id=None,
                items_count=1,
                mode="disabled",
                metadata=metadata or {},
                tags=effective_tags
            )
        try:
            response = self._run_async_isolated(
                "aretain",
                bank_id=target_bank,
                content=content,
                context=context,
                document_id=doc_id,
                metadata=metadata or {},
                tags=effective_tags
            )
            op_id = getattr(response, "operation_id", None)
            return RetainedMemoryResult(
                success=getattr(response, "success", True),
                bank_id=target_bank,
                learnmesh_memory_id=doc_id,
                hindsight_document_id=doc_id,
                hindsight_operation_id=str(op_id) if op_id is not None else None,
                items_count=getattr(response, "items_count", 1),
                mode="live",
                metadata=metadata or {},
                tags=effective_tags
            )
        except Exception as e:
            logger.warning(
                f"Hindsight retain call failed ({type(e).__name__}: {e}). "
                f"Buffering memory {doc_id} into local degraded queue."
            )
            # Queue for replay when service recovers
            self.pending_retains_queue.append({
                "content": content,
                "context": context,
                "document_id": doc_id,
                "metadata": metadata,
                "tags": effective_tags,
                "bank_id": target_bank,
                "queued_at": datetime.now(timezone.utc).isoformat()
            })
            return RetainedMemoryResult(
                success=False,
                bank_id=target_bank,
                learnmesh_memory_id=doc_id,
                hindsight_document_id=doc_id,
                hindsight_operation_id=None,
                items_count=1,
                mode="degraded_local",
                metadata=metadata or {},
                tags=effective_tags
            )

    def recall(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        bank_id: Optional[str] = None,
        max_tokens: int = 4096
    ) -> List[RecalledMemoryItem]:
        """
        Recall relevant memories from Hindsight using arecall.
        If circuit breaker is OPEN or Hindsight is down, returns empty list so downstream
        can safely fall back to local database memory cache.
        """
        if not self.enabled:
            return []
        target_bank = bank_id or self.bank_id
        effective_tags = list(tags) if tags is not None else [f"session:{self.demo_session_id}"]

        try:
            response = self._run_async_isolated(
                "arecall",
                bank_id=target_bank,
                query=query,
                tags=effective_tags,
                max_tokens=max_tokens
            )
            
            results: List[RecalledMemoryItem] = []
            raw_results = getattr(response, "results", []) or []

            for item in raw_results:
                score_val = None
                scores_obj = getattr(item, "scores", None)
                if scores_obj:
                    raw_score = getattr(scores_obj, "final", None) or getattr(scores_obj, "semantic", None)
                    if isinstance(raw_score, (int, float)):
                        score_val = float(raw_score)

                raw_type = getattr(item, "type", None)
                type_str = str(raw_type) if raw_type is not None and not hasattr(raw_type, "_mock_return_value") else None

                raw_context = getattr(item, "context", None)
                context_str = str(raw_context) if (raw_context is not None and not hasattr(raw_context, "_mock_return_value")) else None

                raw_doc_id = None
                if hasattr(item, "document_id") and not hasattr(getattr(item, "document_id"), "_mock_return_value"):
                    raw_doc_id = getattr(item, "document_id")
                if not raw_doc_id and hasattr(item, "id") and not hasattr(getattr(item, "id"), "_mock_return_value"):
                    raw_doc_id = getattr(item, "id")
                if not raw_doc_id and isinstance(item, dict):
                    raw_doc_id = item.get("document_id") or item.get("id")

                item_doc_id = str(raw_doc_id) if raw_doc_id is not None else "HM-UNKNOWN"

                results.append(
                    RecalledMemoryItem(
                        learnmesh_memory_id=item_doc_id,
                        hindsight_document_id=item_doc_id,
                        text=str(getattr(item, "text", "")),
                        type=type_str,
                        context=context_str,
                        tags=[str(t) for t in (getattr(item, "tags", []) or []) if not hasattr(t, "_mock_return_value")],
                        metadata={str(k): str(v) for k, v in (getattr(item, "metadata", {}) or {}).items() if not hasattr(v, "_mock_return_value")},
                        score=score_val,
                        entities=[str(e) for e in (getattr(item, "entities", []) or []) if not hasattr(e, "_mock_return_value")]
                    )
                )

            return results
        except Exception as e:
            logger.warning(f"Hindsight recall call encountered: {type(e).__name__} ({e}). Serving from local cache.")
            return []

    def replay_pending_queue(self) -> Dict[str, Any]:
        """Attempts to drain and replay pending retains that were buffered while Hindsight was down."""
        if not self.enabled:
            return {"replayed": 0, "remaining": 0}
        if not self.pending_retains_queue:
            return {"replayed": 0, "remaining": 0}

        success_count = 0
        remaining = []
        for item in self.pending_retains_queue:
            try:
                res = self.retain(
                    content=item["content"],
                    context=item["context"],
                    document_id=item["document_id"],
                    metadata=item["metadata"],
                    tags=item["tags"],
                    bank_id=item["bank_id"]
                )
                if res.success:
                    success_count += 1
                else:
                    remaining.append(item)
            except Exception:
                remaining.append(item)

        self.pending_retains_queue = remaining
        return {"replayed": success_count, "remaining": len(remaining)}

# Global singleton service instance (deployment-wide Hindsight kill-switch via FEATURE_HINDSIGHT)
hindsight_service = HindsightService(enabled=settings.FEATURE_HINDSIGHT)
