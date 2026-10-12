import asyncio
import threading
import pytest
from unittest.mock import MagicMock, patch

from app.azure.document_intelligence import analyze_document_raw
from app.azure.openai import (
    _get_client as get_openai_client,
    chat_completion_async,
    generate_embedding_async,
)
from app.azure.search import (
    _get_search_client,
    _get_index_client,
    hybrid_search_async,
    keyword_search_async,
    filter_search_async,
)
from app.services.document_understanding_service import analyze_document_async
from app.services.classification_service import classify_document_async


@pytest.mark.asyncio
async def test_document_intelligence_runs_on_worker_thread():
    """Verify analyze_document_raw executes on a thread distinct from the asyncio event loop."""
    main_thread = threading.current_thread()
    worker_threads = []

    def mock_sync_analyze(file_bytes: bytes):
        worker_threads.append(threading.current_thread())
        mock_result = MagicMock()
        mock_result.content = "OCR test text"
        return mock_result

    with patch("app.azure.document_intelligence._analyze_document_sync", side_effect=mock_sync_analyze):
        result = await analyze_document_raw(b"fake pdf content")
        assert result.content == "OCR test text"
        assert len(worker_threads) == 1
        assert worker_threads[0] != main_thread, "Expected OCR sync execution on a worker thread"


def test_azure_openai_client_singleton():
    """Verify AzureOpenAI client is created once and reused across invocations."""
    client1 = get_openai_client()
    client2 = get_openai_client()
    assert client1 is client2, "AzureOpenAI client should be a reused singleton instance"


@pytest.mark.asyncio
async def test_openai_chat_and_embedding_async_on_worker_thread():
    """Verify chat_completion_async and generate_embedding_async execute on worker threads."""
    main_thread = threading.current_thread()
    captured_threads = []

    def mock_chat(system_prompt, user_prompt, temperature=0.1):
        captured_threads.append(threading.current_thread())
        return '{"title": "Test"}'

    def mock_embed(text):
        captured_threads.append(threading.current_thread())
        return [0.1, 0.2, 0.3]

    with patch("app.azure.openai.chat_completion", side_effect=mock_chat), \
         patch("app.azure.openai.generate_embedding", side_effect=mock_embed):

        chat_res = await chat_completion_async("sys", "user")
        assert chat_res == '{"title": "Test"}'
        assert captured_threads[-1] != main_thread

        embed_res = await generate_embedding_async("sample text")
        assert embed_res == [0.1, 0.2, 0.3]
        assert captured_threads[-1] != main_thread


def test_azure_search_client_singletons():
    """Verify SearchClient and SearchIndexClient instances are singletons."""
    search_client1 = _get_search_client()
    search_client2 = _get_search_client()
    assert search_client1 is search_client2, "SearchClient should be a reused singleton"

    index_client1 = _get_index_client()
    index_client2 = _get_index_client()
    assert index_client1 is index_client2, "SearchIndexClient should be a reused singleton"


@pytest.mark.asyncio
async def test_search_async_functions_on_worker_thread():
    """Verify hybrid_search_async, keyword_search_async, and filter_search_async run on worker threads."""
    main_thread = threading.current_thread()
    threads_used = []

    def mock_hybrid(query, top_k=5, filter_expr=None):
        threads_used.append(threading.current_thread())
        return [{"id": "doc1"}]

    def mock_keyword(query, top_k=5, filter_expr=None):
        threads_used.append(threading.current_thread())
        return [{"id": "doc2"}]

    def mock_filter(filter_expr, top_k=10):
        threads_used.append(threading.current_thread())
        return [{"id": "doc3"}]

    with patch("app.azure.search.hybrid_search", side_effect=mock_hybrid), \
         patch("app.azure.search.keyword_search", side_effect=mock_keyword), \
         patch("app.azure.search.filter_search", side_effect=mock_filter):

        res1 = await hybrid_search_async("query")
        assert res1 == [{"id": "doc1"}]
        assert threads_used[-1] != main_thread

        res2 = await keyword_search_async("query")
        assert res2 == [{"id": "doc2"}]
        assert threads_used[-1] != main_thread

        res3 = await filter_search_async("filter")
        assert res3 == [{"id": "doc3"}]
        assert threads_used[-1] != main_thread


@pytest.mark.asyncio
async def test_service_level_async_wrappers():
    """Verify analyze_document_async and classify_document_async execute in worker threads."""
    main_thread = threading.current_thread()
    service_threads = []

    mock_profile = MagicMock()
    mock_understanding = MagicMock()
    mock_classification = MagicMock()

    def mock_analyze(profile):
        service_threads.append(threading.current_thread())
        return mock_understanding

    def mock_classify(profile, understanding):
        service_threads.append(threading.current_thread())
        return mock_classification

    with patch("app.services.document_understanding_service.analyze_document", side_effect=mock_analyze), \
         patch("app.services.classification_service.classify_document", side_effect=mock_classify):

        u_res = await analyze_document_async(mock_profile)
        assert u_res is mock_understanding
        assert service_threads[-1] != main_thread

        c_res = await classify_document_async(mock_profile, mock_understanding)
        assert c_res is mock_classification
        assert service_threads[-1] != main_thread
