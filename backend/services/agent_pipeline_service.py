import uuid
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from backend.models.database import (
    AgentDB, InteractionDB, MemoryReferenceDB, SystemEventDB
)
from backend.services.hindsight_service import hindsight_service, HindsightService
from backend.services.llm_service import llm_service, LLMService
from backend.services.applicability_service import ContradictionDetector
from backend.schemas.reasoning_schemas import AgentReasoningInput, AgentReasoningOutput

class AgentPipelineService:
    """
    Executes the full agent reasoning pipeline:
      1. identify agent & task
      2. recall relevant memories from Hindsight
      3. evaluate applicability & conflicts
      4. pass to LLM / reasoning layer
      5. record interaction state & lineage
    """

    def __init__(
        self,
        hindsight: Optional[HindsightService] = None,
        llm: Optional[LLMService] = None
    ):
        self.hindsight = hindsight or hindsight_service
        self.llm = llm or llm_service

    def run_interaction(
        self,
        db: Session,
        agent_id: str,
        user_prompt: str,
        task_type: str = "refund",
        customer_tier: str = "enterprise",
        customer_id: Optional[str] = None
    ) -> Tuple[InteractionDB, AgentReasoningOutput, List[Dict[str, Any]]]:
        agent = db.query(AgentDB).filter(AgentDB.id == agent_id).first()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found.")

        # 1. Recall relevant memories from Hindsight
        query = f"{task_type} {customer_tier} {user_prompt}"
        recalled_items = []
        try:
            recalled_items = self.hindsight.recall(query=query)
        except Exception:
            # Fall back to local memory references if Hindsight is offline in development
            local_refs = db.query(MemoryReferenceDB).filter(
                MemoryReferenceDB.task_type == task_type
            ).all()
            for ref in local_refs:
                recalled_items.append({
                    "id": ref.memory_id,
                    "text": ref.lesson,
                    "scope": ref.scope,
                    "confidence_score": ref.confidence_score
                })

        # 2. Filter memories based on agent applicability scope
        agent_name = agent.name
        applicable_memories = []
        for item in recalled_items:
            mem_id = getattr(item, "id", None) or item.get("id")
            mem_ref = db.query(MemoryReferenceDB).filter(MemoryReferenceDB.memory_id == mem_id).first()
            if mem_ref:
                if agent_name in mem_ref.scope or len(mem_ref.scope) == 0:
                    applicable_memories.append({
                        "id": mem_ref.memory_id,
                        "text": mem_ref.lesson,
                        "source_agent": mem_ref.source_agent,
                        "confidence_level": mem_ref.confidence_level,
                        "confidence_score": mem_ref.confidence_score,
                        "scope": mem_ref.scope
                    })
            else:
                # Direct item formatting if local or untracked
                item_text = getattr(item, "text", "") or item.get("text", "")
                applicable_memories.append({
                    "id": mem_id,
                    "text": item_text,
                    "source_agent": "Shared Fleet",
                    "confidence_level": "Limited",
                    "confidence_score": 0.40,
                    "scope": [agent_name]
                })

        # 3. Detect conflicting historical memories
        conflicts = ContradictionDetector.detect_conflicts(applicable_memories)
        has_conflicts = len(conflicts) > 0

        # 4. Build reasoning input
        reasoning_input = AgentReasoningInput(
            agent_id=agent.id,
            agent_name=agent.name,
            task_type=task_type,
            customer_tier=customer_tier,
            user_prompt=user_prompt,
            relevant_memories=applicable_memories,
            has_conflicts=has_conflicts,
            conflicting_memories=conflicts
        )

        # 5. Generate structured response
        agent_output = self.llm.generate_agent_response(reasoning_input)

        # 5. Save interaction
        interaction_id = f"int-{uuid.uuid4().hex[:8]}"
        interaction = InteractionDB(
            id=interaction_id,
            agent_id=agent.id,
            customer_id=customer_id or "cust-enterprise-01",
            customer_tier=customer_tier,
            task_type=task_type,
            user_prompt=user_prompt,
            agent_response=agent_output.response_text,
            recalled_memory_ids=agent_output.used_memory_ids,
            status="completed"
        )
        db.add(interaction)

        # Record system event
        event = SystemEventDB(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            event_type="interaction_created",
            agent_id=agent.id,
            memory_id=agent_output.used_memory_ids[0] if agent_output.used_memory_ids else None,
            details={
                "interaction_id": interaction_id,
                "used_memories": agent_output.used_memory_ids,
                "action_type": agent_output.action_type
            }
        )
        db.add(event)
        db.commit()

        return interaction, agent_output, applicable_memories

agent_pipeline_service = AgentPipelineService()
