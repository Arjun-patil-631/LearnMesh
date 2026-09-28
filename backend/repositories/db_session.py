from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from backend.utils.config import settings
from backend.models.database import Base, AgentDB

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    
    # Initialize standard default agents if not present
    db = SessionLocal()
    try:
        if db.query(AgentDB).count() == 0:
            default_agents = [
                AgentDB(
                    id="agent-billing",
                    name="Billing Agent",
                    role="Billing & Invoicing Specialist",
                    capabilities=["billing", "refunds", "invoices", "payment_terms"],
                    description="Handles customer billing inquiries, fee adjustments, and refund approvals."
                ),
                AgentDB(
                    id="agent-support",
                    name="Support Agent",
                    role="Customer Tier-1 & Tier-2 Support",
                    capabilities=["support", "ticketing", "refunds", "service_health"],
                    description="Frontline customer support handling tickets, technical queries, and customer satisfaction."
                ),
                AgentDB(
                    id="agent-account",
                    name="Account Management Agent",
                    role="Enterprise Account Manager",
                    capabilities=["account", "enterprise_contracts", "retention", "renewals", "refunds"],
                    description="Manages high-value enterprise accounts, renewals, and special contractual requirements."
                ),
                AgentDB(
                    id="agent-warehouse",
                    name="Warehouse Operations",
                    role="Logistics & Inventory Agent",
                    capabilities=["inventory", "logistics", "shipping", "physical_goods"],
                    description="Manages physical inventory, fulfillment, and logistics dispatch."
                )
            ]
            db.add_all(default_agents)
            db.commit()
    finally:
        db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
