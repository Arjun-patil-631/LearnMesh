# LearnMesh Memory Model

## 1. Memory Types & Taxonomies

To prevent undifferentiated text bloat, LearnMesh defines explicit conceptual memory classifications:

| Memory Type | Description | Persistence Layer |
|---|---|---|
| `EPISODE` | A single concrete interaction event between an agent and a user. | SQLite / Event Store |
| `HUMAN_CORRECTION` | Explicit corrective input supplied by a human operator over an agent's response. | SQLite audit log |
| `FAILURE` | An agent approach that produced an undesirable or non-compliant outcome. | SQLite |
| `SUCCESS` | A verified action that resolved a request correctly and met policy rules. | SQLite |
| `LESSON_CANDIDATE` | An extracted reusable principle awaiting human review and scope assignment. | SQLite staging |
| `SHARED_LESSON` | A human-verified lesson retained in the long-term memory bank for fleet-wide reuse. | **Hindsight** (`bank_id`) |
| `CONTRADICTION` | A conflicting experience where historical outcomes or policies oppose each other. | Evaluated dynamically |
| `PATTERN` | Repeated multi-episode experiences supporting systemic organizational knowledge. | Hindsight observation |

---

## 2. Conceptual Schema

Each shared organizational lesson in LearnMesh encapsulates:

```json
{
  "memory_id": "HM-0021",
  "memory_type": "SHARED_LESSON",
  "source_agent": "Billing Agent",
  "task_type": "refund",
  "context": "Customer tier: enterprise | Enterprise Master Services Agreement",
  "lesson": "Enterprise refund requests require approval prior to customer commitment.",
  "scope": [
    "Billing Agent",
    "Support Agent",
    "Account Management Agent"
  ],
  "confidence_level": "Strong",
  "confidence_score": 0.85,
  "supporting_outcomes_count": 3,
  "contradicting_outcomes_count": 0,
  "created_at": "2026-09-28T05:30:00Z",
  "last_reinforced_at": "2026-09-28T05:40:00Z"
}
```

---

## 3. Storage Separation Principle

- **Hindsight Memory Bank:** The authoritative repository for retained content, semantic indices, temporal windows, entity graphs, and similarity recall queries.
- **SQLite Application DB:** Stores operational state, agent registration metadata, interaction histories, candidate staging queues, and audit pointers referencing Hindsight document IDs.
