import pytest
import time
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.main import app
from backend.utils.resilience import CircuitBreaker, CircuitState, CircuitBreakerOpenException
from backend.utils.task_queue import AsyncTaskQueue
from backend.repositories.db_session import init_db

init_db()
client = TestClient(app)

def test_health_liveness_and_readiness():
    # 1. Liveness
    live_res = client.get("/health/live")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "alive"

    # 2. Readiness
    ready_res = client.get("/health/ready")
    assert ready_res.status_code == 200
    assert ready_res.json()["status"] == "ready"
    assert ready_res.json()["database"] == "connected"

def test_api_v1_versioned_routing():
    # Primary versioned route
    res_v1 = client.get("/api/v1/system/status")
    assert res_v1.status_code == 200
    data_v1 = res_v1.json()
    assert "status" in data_v1
    assert "groq" in data_v1

    # Legacy unversioned route
    res_legacy = client.get("/api/system/status")
    assert res_legacy.status_code == 200
    data_legacy = res_legacy.json()
    assert data_legacy["status"] == data_v1["status"]
    assert data_legacy["database"] == data_v1["database"]
    assert data_legacy["groq"]["model"] == data_v1["groq"]["model"]

def test_correlation_id_and_timing_headers():
    custom_req_id = "test-corr-id-12345"
    res = client.get("/health/live", headers={"X-Request-ID": custom_req_id})
    assert res.status_code == 200
    assert res.headers.get("x-request-id") == custom_req_id
    assert "x-response-time-ms" in res.headers

def test_standardized_error_envelope():
    # Trigger 404
    res = client.get("/api/v1/agents/non-existent-agent-id-999")
    assert res.status_code == 404
    err_data = res.json()
    assert err_data["code"] == "NOT_FOUND"
    assert "Agent not found" in err_data["message"]
    assert "request_id" in err_data

    # Trigger 422 Validation Error
    res_invalid = client.post("/api/v1/interactions", json={})
    assert res_invalid.status_code == 422
    val_data = res_invalid.json()
    assert val_data["code"] == "VALIDATION_ERROR"
    assert "details" in val_data
    assert "request_id" in val_data

def test_circuit_breaker_state_machine():
    cb = CircuitBreaker(
        name="TestService",
        failure_threshold=2,
        recovery_timeout_seconds=0.1
    )
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1st failure
    def failing_call():
        raise ConnectionError("Service unreachable")

    with pytest.raises(ConnectionError):
        cb.execute(failing_call)
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 1

    # 2nd failure -> trips to OPEN
    with pytest.raises(ConnectionError):
        cb.execute(failing_call)
    assert cb.state == CircuitState.OPEN
    assert cb.total_trips == 1

    # While OPEN, fast-fails immediately with CircuitBreakerOpenException
    with pytest.raises(CircuitBreakerOpenException):
        cb.execute(failing_call)

    # Wait for recovery timeout -> transitions to HALF_OPEN
    time.sleep(0.15)
    assert cb.can_execute() is True

    # Successful call in HALF_OPEN resets to CLOSED
    def successful_call():
        return "success"

    res = cb.execute(successful_call)
    assert res == "success"
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0

def test_idempotency_key_caching():
    import uuid
    idempotency_key = f"idemp-test-{uuid.uuid4().hex}"
    payload = {
        "agent_id": "agent-billing",
        "user_prompt": "Idempotency test request invoice #1234.",
        "task_type": "refund",
        "customer_tier": "enterprise"
    }

    # 1st call
    res1 = client.post(
        "/api/v1/interactions",
        json=payload,
        headers={"Idempotency-Key": idempotency_key}
    )
    assert res1.status_code == 200
    int_id_1 = res1.json()["interaction_id"]
    assert "X-Cache-Lookup" not in res1.headers

    # 2nd call with identical key -> returns cached response
    res2 = client.post(
        "/api/v1/interactions",
        json=payload,
        headers={"Idempotency-Key": idempotency_key}
    )
    assert res2.status_code == 200
    int_id_2 = res2.json()["interaction_id"]
    assert int_id_1 == int_id_2
    assert res2.headers.get("x-cache-lookup") == "HIT-IDEMPOTENCY"

def test_pagination_and_sorting_on_memories():
    # 1. Reset demo state to populate seed data
    client.post("/api/v1/demo/reset")

    # Add two distinct memories for testing pagination
    res_int = client.post("/api/v1/interactions", json={
        "agent_id": "agent-billing",
        "user_prompt": "Invoice refund test.",
        "task_type": "refund",
        "customer_tier": "enterprise"
    })
    int_id = res_int.json()["interaction_id"]

    res_corr = client.post(f"/api/v1/interactions/{int_id}/correction", json={
        "correction_text": "Always verify approval for enterprise refunds.",
        "human_reason": "Policy check"
    })
    cand_id = res_corr.json()["candidate_id"]

    client.post(f"/api/v1/lessons/{cand_id}/validate", json={
        "candidate_id": cand_id,
        "confirmed_lesson": "Always verify approval for enterprise refunds.",
        "target_scope": ["Billing Agent", "Support Agent"]
    })

    # Test unpaginated backward compatibility
    res_all = client.get("/api/v1/memories")
    assert res_all.status_code == 200
    assert isinstance(res_all.json(), list)

    # Test paginated response
    res_page = client.get("/api/v1/memories?page=1&page_size=1")
    assert res_page.status_code == 200
    data = res_page.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) == 1

def test_prometheus_metrics_endpoint():
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    text_content = res.text
    assert "learnmesh_http_requests_total" in text_content
    assert "learnmesh_http_request_duration_seconds" in text_content

@pytest.mark.asyncio
async def test_async_task_queue_execution():
    queue = AsyncTaskQueue()
    executed_payloads = []

    def sample_worker(payload):
        executed_payloads.append(payload)
        return {"processed": True}

    queue.register_handler("test_job", sample_worker)
    await queue.start()

    try:
        job_id = await queue.enqueue("test_job", {"param": "value123"})
        assert job_id.startswith("job-")

        # Allow worker loop to execute
        import asyncio
        await asyncio.sleep(0.1)

        job_record = queue.get_job(job_id)
        assert job_record is not None
        assert job_record["status"] == "completed"
        assert len(executed_payloads) == 1
        assert executed_payloads[0]["param"] == "value123"
    finally:
        await queue.stop()
