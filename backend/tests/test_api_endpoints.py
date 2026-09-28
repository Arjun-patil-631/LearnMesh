import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.repositories.db_session import init_db

init_db()
client = TestClient(app)

def test_api_system_status():
    response = client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "operational"
    assert "hindsight" in data
    assert "groq" in data

def test_api_list_agents():
    response = client.get("/api/agents")
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) >= 3
    names = [a["name"] for a in agents]
    assert "Billing Agent" in names
    assert "Support Agent" in names

def test_api_full_correction_and_outcome_flow():
    # 1. Reset demo state
    res_reset = client.post("/api/demo/reset")
    assert res_reset.status_code == 200

    # 2. Billing Agent interaction
    res_int = client.post("/api/interactions", json={
        "agent_id": "agent-billing",
        "user_prompt": "Please issue refund for Invoice #9021.",
        "task_type": "refund",
        "customer_tier": "enterprise"
    })
    assert res_int.status_code == 200
    int_data = res_int.json()
    int_id = int_data["interaction_id"]

    # 3. Capture correction
    res_corr = client.post(f"/api/interactions/{int_id}/correction", json={
        "correction_text": "Do not promise a refund for enterprise-contract customers. Approval is required.",
        "human_reason": "Enterprise policy compliance"
    })
    assert res_corr.status_code == 200
    corr_data = res_corr.json()
    cand_id = corr_data["candidate_id"]

    # 4. Validate and promote
    res_promo = client.post(f"/api/lessons/{cand_id}/validate", json={
        "candidate_id": cand_id,
        "confirmed_lesson": "Enterprise refund requests require approval prior to customer commitment.",
        "target_scope": ["Billing Agent", "Support Agent", "Account Management Agent"]
    })
    assert res_promo.status_code == 200
    promo_data = res_promo.json()
    mem_id = promo_data["memory_id"]

    # 5. Check memory in explorer
    res_mem = client.get(f"/api/memory/{mem_id}")
    assert res_mem.status_code == 200
    assert res_mem.json()["lesson"] == "Enterprise refund requests require approval prior to customer commitment."

    # 6. Check lineage
    res_lineage = client.get(f"/api/memory/{mem_id}/lineage")
    assert res_lineage.status_code == 200
    lineage = res_lineage.json()
    assert lineage["memory_id"] == mem_id
    assert lineage["original_candidate"]["id"] == cand_id

    # 7. Record positive outcome confirmation
    res_out = client.post(f"/api/interactions/{int_id}/outcome", json={
        "memory_id": mem_id,
        "outcome_type": "CONFIRMED_SUCCESS",
        "notes": "Verified in downstream test"
    })
    assert res_out.status_code == 200
    out_data = res_out.json()
    assert out_data["supporting_count"] == 1
    assert out_data["new_confidence_score"] > 0.40

def test_api_empty_recall_baseline():
    """Verify clean fallback behavior when non-existent agent or query is invoked."""
    res = client.post("/api/interactions", json={
        "agent_id": "agent-support",
        "user_prompt": "Random inquiry about weather conditions.",
        "task_type": "general",
        "customer_tier": "standard"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert len(data["used_memory_ids"]) == 0
    assert data["requires_approval"] is False

def test_api_reset_idempotency():
    """Verify calling demo reset multiple times is completely idempotent."""
    res1 = client.post("/api/demo/reset")
    assert res1.status_code == 200
    res2 = client.post("/api/demo/reset")
    assert res2.status_code == 200
    
    # Verify database is intact with seed agents
    res_agents = client.get("/api/agents")
    assert res_agents.status_code == 200
    assert len(res_agents.json()) >= 3

