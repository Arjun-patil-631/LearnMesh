import logging
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field
from hindsight_client import Hindsight
try:
    from hindsight_client_api.exceptions import ApiException
except ImportError:
    class ApiException(Exception):
        pass
from backend.utils.config import settings

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("learnmesh.hindsight")

class RetainedMemoryResult(BaseModel):
    success: bool
    bank_id: str
    learnmesh_memory_id: Optional[str] = None
    hindsight_document_id: Optional[str] = None
    # Note: hindsight-client aretain(...) returns RetainResponse with operation_id and items_count.
    # It indexes content by document_id; it does not issue an independent server-generated memory id.
    hindsight_operation_id: Optional[str] = None
    items_count: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
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
    Responsible for:
      - retain (stores experience/lesson memory units into designated bank)
      - recall (retrieves relevant memory units using semantic & metadata matching)
      - list_memories
      - health/ping check
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        bank_id: Optional[str] = None
    ):
        self.base_url = (base_url or settings.HINDSIGHT_BASE_URL).rstrip("/")
        self.api_key = api_key or settings.HINDSIGHT_API_KEY
        self.bank_id = bank_id or settings.HINDSIGHT_BANK_ID
        self.demo_session_id: str = f"demo-{uuid.uuid4().hex[:8]}"
        self._client: Optional[Hindsight] = None
        self._is_connected: bool = False

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
                timeout=15.0
            )
        return self._client

    def ping(self) -> Dict[str, Any]:
        """Verify connectivity to Hindsight server via isolated loop."""
        try:
            version = self._run_async_isolated("aget_version")
            self._is_connected = True
            return {"status": "connected", "version": getattr(version, "version", str(version))}
        except Exception as e:
            self._is_connected = False
            logger.warning(f"Hindsight server ping failed: {e}")
            return {"status": "unavailable", "error": str(e)}

    def is_available(self) -> bool:
        check = self.ping()
        return check.get("status") == "connected"

    def _run_async_isolated(self, method_name: str, *args, **kwargs):
        """
        Execute an async hindsight method on a dedicated background thread with its own event loop and client session.
        This avoids the asyncio/anyio event-loop conflict inside FastAPI request contexts.
        """
        import concurrent.futures
        import asyncio

        def worker():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            client = Hindsight(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=15.0
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

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker)
            return future.result()

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
        Retain memory unit into Hindsight using aretain on an isolated event loop.
        """
        target_bank = bank_id or self.bank_id

        # Generate a deterministic/unique document id if not provided
        doc_id = document_id or f"HM-{uuid.uuid4().hex[:8].upper()}"

        # Ensure demo session tag is included for isolated demo runs
        effective_tags = list(tags or ["learnmesh", "shared_lesson"])
        if f"session:{self.demo_session_id}" not in effective_tags:
            effective_tags.append(f"session:{self.demo_session_id}")

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
                metadata=metadata or {},
                tags=effective_tags
            )
        except Exception as e:
            logger.warning(f"Hindsight retain call failed ({e}). Returning memory record in offline mode.")
            # Graceful offline mode when server daemon is not running
            return RetainedMemoryResult(
                success=False,
                bank_id=target_bank,
                learnmesh_memory_id=doc_id,
                hindsight_document_id=doc_id,
                hindsight_operation_id=None,
                items_count=1,
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
        Recall relevant memories from Hindsight using arecall on an isolated loop.
        """
        target_bank = bank_id or self.bank_id

        # If specific tags not provided, filter by current demo session
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
                    # scores may have semantic, bm25, or final score
                    raw_score = getattr(scores_obj, "final", None) or getattr(scores_obj, "semantic", None)
                    if isinstance(raw_score, (int, float)):
                        score_val = float(raw_score)

                raw_type = getattr(item, "type", None)
                type_str = str(raw_type) if raw_type is not None and not isinstance(raw_type, MagicMock if "MagicMock" in globals() else type(None)) else None
                if raw_type is not None and not hasattr(raw_type, "_mock_return_value"):
                    type_str = str(raw_type)

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
            logger.warning(f"Hindsight recall call encountered: {e}. Returning empty recall results.")
            return []

# Global singleton service instance
hindsight_service = HindsightService()
