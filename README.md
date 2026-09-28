# LearnMesh

> **Correct once. Learn everywhere.**

LearnMesh is a shared organizational learning layer for AI agent fleets. When a human corrects, validates, or guides one specialized AI agent, LearnMesh captures and synthesizes that experience using Hindsight memory. The validated lesson is scoped and made available to all relevant peer agents across the organization, preventing repeated mistakes and enabling collective intelligence.

---

## The Core Thesis
Most AI agents learn only inside their own isolated conversational workflow. When a human corrects:
* **Billing Agent**: *"Do not promise an immediate refund for enterprise contracts; VP approval is required."*
That knowledge usually stays trapped in that single thread or agent silo. Later, **Support Agent** or **Account Agent** repeats the exact same mistake with another customer.

**LearnMesh solves this:**
```
ONE HUMAN CORRECTION
        ↓
EXPERIENCE CAPTURE
        ↓
HINDSIGHT RETAIN
        ↓
LESSON PROMOTION & SCOPE ROUTING
        ↓
PEER AGENT RECALL (Support Agent / Account Agent)
        ↓
CHANGED BEHAVIOR (Old mistake avoided)
        ↓
REAL OUTCOME CONFIRMED
        ↓
MEMORY REINFORCED / CONTRADICTED
        ↓
ORGANIZATIONAL INTELLIGENCE
```

---

## Architectural Highlights
- **Mandatory Memory Subsystem:** Built with genuine [Hindsight](https://github.com/vectorize-io/hindsight) long-term memory (`retain`, `recall`, `tags`, `metadata`, `bank_id`).
- **Deterministic Confidence:** Derived mathematically from supporting outcomes, contradiction counts, and verification status.
- **Applicability Engine:** Scopes and filters organizational lessons so only relevant agents receive them (e.g., Billing & Support vs. Warehouse Operations).
- **Contradiction Detection:** Surfaces conflicting experiences transparently to the human operator rather than making silent arbitrary assumptions.
- **Traceable Memory Lineage:** Every recommendation links back to the original human correction, interaction ID, and Hindsight memory identifier.
