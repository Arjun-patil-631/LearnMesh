import re
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.models.database import (
    LessonCandidateDB,
    MemoryReferenceDB
)
from backend.services.confidence_service import ConfidenceService
from backend.schemas.enterprise_schemas import MemoryState

class ImporterService:
    @staticmethod
    def parse_markdown_sop(
        sop_text: str,
        department: str = "General",
        target_agents: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Parses Markdown Standard Operating Procedures (SOPs).
        Extracts sections with headers (## or ###), bullet points, and rule directives.
        """
        rules = []
        lines = sop_text.splitlines()
        current_section = "Standard Operating Procedure"

        for line in lines:
            line_str = line.strip()
            if line_str.startswith("#"):
                current_section = re.sub(r"^#+\s*", "", line_str)
            elif line_str.startswith(("-", "*", "1.", "2.", "3.", "4.", "5.")):
                rule_text = re.sub(r"^[-*\d.]+\s*", "", line_str).strip()
                if len(rule_text) > 10:
                    is_guardrail = any(
                        kw in rule_text.lower() for kw in ["never", "must not", "do not", "prohibited", "cannot"]
                    )
                    rules.append({
                        "section": current_section,
                        "lesson": rule_text,
                        "is_negative_guardrail": is_guardrail,
                        "department": department,
                        "target_agents": target_agents or ["Support Agent", "Billing Agent"]
                    })

        return rules

    @staticmethod
    def bulk_import_sop(
        db: Session,
        content: str,
        format_type: str = "markdown",
        department: str = "General",
        target_agents: Optional[List[str]] = None,
        auto_verify: bool = False,
        verifier_id: str = "sop_importer"
    ) -> Dict[str, Any]:
        """
        Imports SOP document as lesson candidates or directly into active memories.
        """
        if format_type == "json":
            try:
                parsed_rules = json.loads(content)
                if not isinstance(parsed_rules, list):
                    parsed_rules = [parsed_rules]
            except json.JSONDecodeError:
                parsed_rules = []
        else:
            parsed_rules = ImporterService.parse_markdown_sop(content, department, target_agents)

        imported_candidates = []
        imported_memories = []

        now = datetime.now(timezone.utc)

        for item in parsed_rules:
            lesson_str = item.get("lesson", "")
            if not lesson_str:
                continue

            sec = item.get("section", "SOP")
            is_guardrail = item.get("is_negative_guardrail", False)
            scopes = item.get("target_agents") or target_agents or ["Support Agent", "Billing Agent"]

            if auto_verify:
                mem_id = f"mem-sop-{uuid.uuid4().hex[:8]}"
                final_score, conf_level, breakdown = ConfidenceService.calculate_confidence(
                    verifier_role="admin",
                    supporting_count=1,
                    base_score=0.85
                )

                mem = MemoryReferenceDB(
                    memory_id=mem_id,
                    memory_type="SHARED_LESSON",
                    source_agent=scopes[0] if scopes else "SOP Importer",
                    task_type="sop_import",
                    context=f"Standard Operating Procedure: {sec}",
                    lesson=lesson_str,
                    scope=scopes,
                    confidence_level=conf_level.value,
                    confidence_score=final_score,
                    confidence_breakdown=breakdown.model_dump(),
                    state=MemoryState.ACTIVE.value,
                    version=1,
                    priority=80,  # Corporate policy priority
                    is_negative_guardrail=is_guardrail,
                    department=department,
                    created_at=now
                )
                db.add(mem)
                imported_memories.append(mem_id)
            else:
                cand_id = f"cand-sop-{uuid.uuid4().hex[:8]}"
                cand = LessonCandidateDB(
                    id=cand_id,
                    interaction_id=f"sop-{uuid.uuid4().hex[:6]}",
                    source_agent=scopes[0] if scopes else "SOP Importer",
                    task_type="sop_import",
                    context=f"Standard Operating Procedure: {sec}",
                    original_action="N/A",
                    correction_text=lesson_str,
                    extracted_lesson=lesson_str,
                    recommended_scope=scopes,
                    is_validated=False,
                    is_promoted=False,
                    is_negative_guardrail=is_guardrail,
                    risk_level="MEDIUM" if is_guardrail else "LOW",
                    created_at=now
                )
                db.add(cand)
                imported_candidates.append(cand_id)

        db.commit()

        return {
            "total_rules_extracted": len(parsed_rules),
            "candidates_created": len(imported_candidates),
            "memories_created": len(imported_memories),
            "candidate_ids": imported_candidates,
            "memory_ids": imported_memories,
            "status": "IMPORT_SUCCESS"
        }
