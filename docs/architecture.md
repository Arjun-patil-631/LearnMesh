# LearnMesh Technical Architecture

## 1. System Overview

**LearnMesh** provides a persistent, shared organizational memory layer for fleets of specialized AI agents. Unlike standard RAG systems or isolated conversation threads, LearnMesh enables **Cross-Agent Experience Transfer**. When a human provides a correction or validation to one agent (e.g., Billing Agent), that experience is extracted, verified through a human-in-the-loop gate, retained into a long-term **Hindsight** memory bank, and automatically made available to peer agents (e.g., Support Agent, Account Management Agent).

```
+-----------------------------------------------------------------------------------+
|                               HUMAN OPERATOR                                      |
|                     (Corrects action & Validates lesson)                          |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+------------------------+      +---------------------------+      +----------------+
|      Billing Agent     | ---> |  Memory Promotion Service | ---> |   HINDSIGHT    |
| (Initial mistake made) |      | (Human Verification Gate) |      |  (retain/bank) |
+------------------------+      +---------------------------+      +----------------+
                                                                            |
                                                                            v
+------------------------+      +---------------------------+      +----------------+
|      Support Agent     | <--- |   Applicability Engine    | <--- |   HINDSIGHT    |
| (Recalls & changes act)|      | (Scope & Conflict filter) |      | (recall/match) |
+------------------------+      +---------------------------+      +----------------+
                                         |
                                         v
                               +--------------------+
                               | Outcome Confirmed  |
                               | (Score reinforces) |
                               +--------------------+
```

---

## 2. Core Components

### A. Hindsight Adapter (`backend/services/hindsight_service.py`)
- Direct integration with official `hindsight-client` SDK.
- Implements `aretain` and `arecall` executed in isolated event loops to prevent event-loop conflicts inside web application threads.
- Retains memory units tagged with domain taxonomies (`learnmesh`, `shared_lesson`, `agent:support_agent`).
- Graceful degraded-mode resilience when the external daemon is offline.

### B. Memory Promotion Pipeline (`backend/services/memory_promotion_service.py`)
- Prevents turning unverified conversations into organizational truth.
- **Workflow:**
  1. Capture raw interaction.
  2. Extract candidate lesson upon human correction.
  3. Require explicit human confirmation (`confirmed_lesson`, `target_scope`).
  4. Retain validated lesson into Hindsight bank.
  5. Record audit reference in SQLite.

### C. Applicability & Confidence Engine (`backend/services/applicability_service.py`)
- **Applicability:** Evaluates agent capabilities and explicit scope. E.g., refund lessons route to Billing, Support, and Account agents, but are excluded from Warehouse Operations.
- **Deterministic Confidence:** Derived mathematically:
  $$\text{score} = \text{base} + 0.15 \times \min(\text{confirmations}, 4) - 0.30 \times \text{contradictions}$$
  Clamped between $0.05$ and $0.98$.
  Categorized as **Limited**, **Moderate**, or **Strong**.
- **Contradiction Detection:** Opposing historical guidance (e.g., "require approval" vs. "direct refund immediately") is explicitly flagged rather than silently suppressed.

### D. LLM Reasoning Service (`backend/services/llm_service.py`)
- Leverages Groq (`llama-3.3-70b-versatile`) when configured with `GROQ_API_KEY`.
- Includes deterministic reasoning fallback when credentials are not present, ensuring predictable and verifiable behavior in all environments.
