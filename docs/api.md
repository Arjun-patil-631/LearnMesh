# LearnMesh REST API Reference

All requests and responses use JSON.

Base URL: `http://localhost:8000/api`

---

## Endpoints

### 1. System Health & Connectivity
- **`GET /system/status`**
  - Returns real connectivity status for Hindsight, Groq LLM, and SQLite database.

### 2. Fleet Agents
- **`GET /agents`**
  - Lists all registered agents (Billing Agent, Support Agent, Account Management Agent, Warehouse Operations).
- **`GET /agents/{id}`**
  - Returns metadata, capabilities, and role description for a single agent.

### 3. Agent Interactions Pipeline
- **`POST /interactions`**
  - Runs the full agent execution pipeline (recall relevant memories, check scope, reason, respond).
  - Body:
    ```json
    {
      "agent_id": "agent-support",
      "user_prompt": "Customer requests immediate refund.",
      "task_type": "refund",
      "customer_tier": "enterprise"
    }
    ```
- **`GET /interactions/{id}`**
  - Retrieves full interaction details and applied memory references.

### 4. Human Corrections & Memory Promotion
- **`POST /interactions/{id}/correction`**
  - Captures human corrective guidance and stages a candidate lesson.
- **`POST /lessons/{candidate_id}/validate`**
  - Human verification gate that approves lesson content, scopes eligible agents, and triggers **Hindsight retain**.

### 5. Outcome Recording & Reinforcement
- **`POST /interactions/{id}/outcome`**
  - Records an outcome (`CONFIRMED_SUCCESS` or `CONTRADICTION`) for an applied memory, dynamically adjusting confidence.

### 6. Memory Explorer & Lineage
- **`GET /memories`**
  - Lists all retained organizational lessons with confidence metrics.
- **`GET /memory/{id}`**
  - Returns individual memory detail.
- **`GET /memory/{id}/lineage`**
  - Returns traceable history from human correction to peer agent reuse and reinforcement.

### 7. Fleet Learning & Demo Reset
- **`GET /fleet/learning`**
  - Summarizes cross-agent transfers and active memories.
- **`POST /demo/reset`**
  - Resets demo state to a clean baseline for repeatable presentations.
