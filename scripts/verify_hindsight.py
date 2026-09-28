import sys
import logging
from backend.services.hindsight_service import HindsightService
from backend.utils.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_hindsight")

def test_hindsight_live_or_diagnostic():
    print(f"Connecting to Hindsight at: {settings.HINDSIGHT_BASE_URL} (Bank: {settings.HINDSIGHT_BANK_ID})")
    service = HindsightService()
    
    ping_res = service.ping()
    print(f"Hindsight Ping Result: {ping_res}")
    
    if ping_res.get("status") != "connected":
        print("\n[DIAGNOSTIC] Hindsight server is not reachable at the configured address.")
        print("Note: In environments where the Hindsight server daemon is offline, degraded mode will be active.")
        print("To run local Hindsight server, ensure: hindsight-all or Docker container running on port 8888.")
        return False
        
    try:
        # Step 1: Retain memory
        print("\nAttempting to retain test experience into Hindsight...")
        sample_lesson = "Enterprise contract refund requests require VP approval prior to customer commitment."
        res = service.retain(
            content=sample_lesson,
            context="Billing customer interaction refund check",
            tags=["learnmesh", "test_verification", "billing"]
        )
        print(f"Successfully retained memory. ID: {res.memory_id}, Bank: {res.bank_id}")
        
        # Step 2: Recall memory
        print("\nAttempting to recall test experience from Hindsight...")
        recalled = service.recall(
            query="Does enterprise refund need approval?",
            tags=["learnmesh"]
        )
        print(f"Recall returned {len(recalled)} memories.")
        for idx, m in enumerate(recalled, 1):
            print(f" [{idx}] ID: {m.id} | Score: {m.score} | Text: {m.text[:60]}...")
            
        print("\nHindsight SDK verification completed successfully!")
        return True
    except Exception as e:
        print(f"\nHindsight verification failed with error: {e}")
        return False

if __name__ == "__main__":
    success = test_hindsight_live_or_diagnostic()
    # Exit with appropriate code
    sys.exit(0 if success else 1)
