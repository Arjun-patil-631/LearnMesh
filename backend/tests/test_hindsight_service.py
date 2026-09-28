import pytest
from unittest.mock import MagicMock, patch
from backend.services.hindsight_service import HindsightService, RetainedMemoryResult, RecalledMemoryItem

def test_hindsight_service_init():
    service = HindsightService(base_url="http://mock-hindsight:8888", bank_id="test-fleet")
    assert service.base_url == "http://mock-hindsight:8888"
    assert service.bank_id == "test-fleet"

@patch("backend.services.hindsight_service.Hindsight")
def test_hindsight_retain_flow(mock_hindsight_cls):
    mock_client = MagicMock()
    mock_hindsight_cls.return_value = mock_client
    
    mock_response = MagicMock()
    mock_response.success = True
    mock_response.items_count = 1
    mock_response.operation_id = "op-1234"
    
    async def mock_aretain(*args, **kwargs):
        return mock_response
    mock_client.aretain = mock_aretain

    service = HindsightService(base_url="http://test:8888", bank_id="test-bank")
    result = service.retain(
        content="Enterprise refunds require approval",
        context="Contract review",
        document_id="HM-001",
        tags=["refund", "enterprise"]
    )

    assert isinstance(result, RetainedMemoryResult)
    assert result.memory_id == "HM-001"
    assert result.bank_id == "test-bank"
    assert result.success is True

@patch("backend.services.hindsight_service.Hindsight")
def test_hindsight_recall_flow(mock_hindsight_cls):
    mock_client = MagicMock()
    mock_hindsight_cls.return_value = mock_client

    mock_result_item = MagicMock()
    mock_result_item.id = "HM-999"
    mock_result_item.text = "Enterprise refunds need VP approval"
    mock_result_item.context = "Billing context"
    mock_result_item.tags = ["billing", "refund"]
    mock_result_item.metadata = {"source_agent": "Billing Agent"}
    mock_result_item.scores = MagicMock(final=0.92)
    mock_result_item.entities = ["Enterprise", "VP"]

    mock_response = MagicMock()
    mock_response.results = [mock_result_item]
    
    async def mock_arecall(*args, **kwargs):
        return mock_response
    mock_client.arecall = mock_arecall

    service = HindsightService(base_url="http://test:8888", bank_id="test-bank")
    memories = service.recall(query="Can I refund an enterprise customer?")

    assert len(memories) == 1
    assert memories[0].id == "HM-999"
    assert memories[0].learnmesh_memory_id == "HM-999"
    assert memories[0].hindsight_document_id == "HM-999"
    assert memories[0].score == 0.92
    assert "VP approval" in memories[0].text

def test_hindsight_demo_session_isolation():
    service = HindsightService(base_url="http://test:8888", bank_id="test-bank")
    initial_session = service.demo_session_id
    assert initial_session.startswith("demo-")
    
    new_session = service.reset_demo_session()
    assert new_session.startswith("demo-")
    assert new_session != initial_session
    assert service.demo_session_id == new_session
