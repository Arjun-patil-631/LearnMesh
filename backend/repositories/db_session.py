from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from backend.utils.config import settings
from backend.models.database import Base, AgentDB, MemoryReferenceDB

# Swappable engine configuration: SQLite for development/testing, PostgreSQL for production
if settings.is_sqlite:
    engine = create_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=settings.DB_ECHO
    )
else:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_pre_ping=True,
        echo=settings.DB_ECHO
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initializes tables and seeds baseline fleet agents if not present."""
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        existing_agent_ids = {a.id for a in db.query(AgentDB).all()}
        default_agents = [
            AgentDB(
                id="agent-billing",
                name="Billing Agent",
                role="Billing & Invoicing Specialist",
                department="Finance",
                capabilities=["billing", "refunds", "invoices", "payment_terms"],
                description="Handles customer billing inquiries, fee adjustments, and refund approvals."
            ),
            AgentDB(
                id="agent-support",
                name="Support Agent",
                role="Customer Tier-1 & Tier-2 Support",
                department="Customer Experience",
                capabilities=["support", "ticketing", "refunds", "service_health"],
                description="Frontline customer support handling tickets, technical queries, and customer satisfaction."
            ),
            AgentDB(
                id="agent-account",
                name="Account Management Agent",
                role="Enterprise Account Manager",
                department="Customer Success",
                capabilities=["account", "enterprise_contracts", "retention", "renewals", "refunds"],
                description="Manages high-value enterprise accounts, renewals, and special contractual requirements."
            ),
            AgentDB(
                id="agent-warehouse",
                name="Warehouse Operations",
                role="Logistics & Inventory Agent",
                department="Supply Chain",
                capabilities=["inventory", "logistics", "shipping", "physical_goods", "returns"],
                description="Manages physical inventory, fulfillment, returns inspection, and logistics dispatch."
            ),
            AgentDB(
                id="agent-compliance",
                name="Compliance Officer Agent",
                role="Legal & Regulatory Auditor",
                department="Legal & Compliance",
                capabilities=["compliance", "gdpr", "aml", "export_control", "audit"],
                description="Enforces legal adherence, regulatory policies, GDPR erasure, and AML limits."
            ),
            AgentDB(
                id="agent-sales",
                name="Sales & Onboarding Agent",
                role="Commercial & Pricing Specialist",
                department="Sales",
                capabilities=["sales", "pricing", "discounts", "pilot_contracts", "onboarding"],
                description="Manages trial agreements, discount limits, and enterprise pricing proposals."
            ),
            AgentDB(
                id="agent-it",
                name="IT Helpdesk Agent",
                role="Internal Systems & Access Specialist",
                department="Information Technology",
                capabilities=["helpdesk", "access_control", "password_reset", "mfa", "hardware"],
                description="Automates user provisioning, password resets, and IT helpdesk requests."
            )
        ]
        for ag in default_agents:
            if ag.id not in existing_agent_ids:
                db.add(ag)
        db.commit()

        # Permanent starter memories: guarantees Memory Explorer / dashboard /
        # pipeline recall NEVER come up empty — critical on serverless hosts
        # (e.g. Vercel /tmp SQLite) where the database starts fresh.
        # Runs only once per database (fixed IDs, skipped when rows exist).
        if db.query(MemoryReferenceDB).count() == 0:
            now = datetime.now(timezone.utc)
            seed_memories = [
                MemoryReferenceDB(
                    memory_id="HM-SEED-REFUND-01",
                    hindsight_id="HM-SEED-REFUND-01",
                    memory_type="NEGATIVE_GUARDRAIL",
                    source_agent="Billing Agent",
                    task_type="refund",
                    context="Enterprise refund policy baseline",
                    lesson="Never issue a refund without a verified return shipping label, especially for enterprise accounts. All refunds above $100 require manager approval.",
                    scope=["Billing Agent", "Support Agent", "Account Management Agent", "Warehouse Operations"],
                    customer_segments=["enterprise"],
                    conditions={"customer_tier": "enterprise", "requires_proof_of_return": True},
                    state="verified",
                    priority=100,
                    is_negative_guardrail=True,
                    confidence_level="Strong",
                    confidence_score=0.92,
                    confidence_breakdown={"base": 0.55, "verifier_boost": 0.35, "confirmations": 0.02, "decay": 0.0, "explanation": "Seeded organizational policy verified at deploy."},
                    verifier_role="EXECUTIVE",
                    created_at=now,
                    last_reinforced_at=now,
                ),
                MemoryReferenceDB(
                    memory_id="HM-SEED-APPROVAL-01",
                    hindsight_id="HM-SEED-APPROVAL-01",
                    memory_type="SHARED_LESSON",
                    source_agent="Account Management Agent",
                    task_type="refund",
                    context="Enterprise contract governance baseline",
                    lesson="All enterprise contract refunds require account leadership approval before committing. Initiate the approval workflow first.",
                    scope=["Billing Agent", "Support Agent", "Account Management Agent", "Sales & Onboarding Agent"],
                    customer_segments=["enterprise"],
                    conditions={"customer_tier": "enterprise"},
                    state="verified",
                    priority=90,
                    is_negative_guardrail=False,
                    confidence_level="Strong",
                    confidence_score=0.88,
                    confidence_breakdown={"base": 0.55, "verifier_boost": 0.30, "confirmations": 0.03, "decay": 0.0, "explanation": "Seeded organizational policy verified at deploy."},
                    verifier_role="LEGAL",
                    created_at=now,
                    last_reinforced_at=now,
                ),
                MemoryReferenceDB(
                    memory_id="HM-SEED-SLA-01",
                    hindsight_id="HM-SEED-SLA-01",
                    memory_type="NEGATIVE_GUARDRAIL",
                    source_agent="Support Agent",
                    task_type="support",
                    context="Support SLA baseline",
                    lesson="Never promise same-day resolution for enterprise tickets without engineer confirmation. Escalate with a tracking ID instead.",
                    scope=["Support Agent", "IT Helpdesk Agent", "Account Management Agent"],
                    customer_segments=["enterprise"],
                    conditions={"customer_tier": "enterprise"},
                    state="verified",
                    priority=85,
                    is_negative_guardrail=True,
                    confidence_level="Strong",
                    confidence_score=0.85,
                    confidence_breakdown={"base": 0.55, "verifier_boost": 0.30, "confirmations": 0.0, "decay": 0.0, "explanation": "Seeded organizational policy verified at deploy."},
                    verifier_role="SPECIALIST",
                    created_at=now,
                    last_reinforced_at=now,
                ),
            ]
            for mem in seed_memories:
                db.add(mem)
            db.commit()
    finally:
        db.close()

def get_db():
    """FastAPI dependency for yielding request database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def get_db_context():
    """Context manager for background tasks, CLI commands, and standalone scripts."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
