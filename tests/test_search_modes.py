import pytest
from unittest.mock import patch, MagicMock
import app.config

# Must patch BEFORE importing the module that uses it to avoid import-time evaluation issues in some setups,
# but our metadata_service imports SEARCH_MODE inside the function, so it's safe to patch during test runtime.

@patch("app.services.metadata_service.SEARCH_MODE", "hybrid")
@patch("app.azure.search.hybrid_search")
def test_hybrid_search_routing(mock_hybrid):
    """Test that SEARCH_MODE=hybrid routes to hybrid_search."""
    from app.services.metadata_service import _azure_search
    mock_hybrid.return_value = [{"id": "RET-1", "keywords_string": "test"}]
    
    results = _azure_search(["test", "query"], top_k=5)
    
    mock_hybrid.assert_called_once_with("test query", top_k=5)
    assert len(results) == 1
    assert results[0].id == "RET-1"

@patch("app.services.metadata_service.SEARCH_MODE", "keyword")
@patch("app.azure.search.keyword_search")
def test_keyword_search_routing(mock_keyword):
    """Test that SEARCH_MODE=keyword routes to keyword_search."""
    from app.services.metadata_service import _azure_search
    mock_keyword.return_value = [{"id": "RET-2", "keywords_string": "legacy"}]
    
    results = _azure_search(["legacy", "query"], top_k=5)
    
    mock_keyword.assert_called_once_with("legacy query", top_k=5)
    assert len(results) == 1
    assert results[0].id == "RET-2"

@patch("app.azure.search.generate_embedding")
@patch("app.azure.search._get_search_client")
def test_hybrid_search_fallback(mock_client_factory, mock_generate):
    """Test that hybrid_search falls back gracefully to keyword when embedding fails."""
    from app.azure.search import hybrid_search
    
    mock_client = MagicMock()
    mock_client_factory.return_value = mock_client
    
    # Simulate embedding API failure
    mock_generate.side_effect = Exception("OpenAI Rate Limit Exceeded")
    
    mock_client.search.return_value = [{"id": "RET-3", "keywords_string": "fallback"}]
    
    results = hybrid_search("test query")
    
    # Verify search was still called (fallback to keyword)
    mock_client.search.assert_called_once()
    
    # Verify vector_queries was None due to failure
    args, kwargs = mock_client.search.call_args
    assert kwargs.get("vector_queries") is None
    assert kwargs.get("search_text") == "test query"
    
    assert len(results) == 1
    assert results[0]["id"] == "RET-3"
