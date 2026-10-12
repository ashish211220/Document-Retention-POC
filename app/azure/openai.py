import asyncio
import json
import os
import re
from typing import Optional
from openai import AzureOpenAI, AsyncAzureOpenAI
from app.config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT_NAME,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME,
    AZURE_OPENAI_API_VERSION,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_CONTENT_CHARS = 8000
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60.0"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))

_client: Optional[AzureOpenAI] = None
_async_client: Optional[AsyncAzureOpenAI] = None


def _get_base_endpoint(endpoint: str) -> str:
    match = re.match(r"(https://[^/]+\.openai\.azure\.com/?)", endpoint)
    if match:
        return match.group(1).rstrip("/") + "/"
    return endpoint


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        base_endpoint = _get_base_endpoint(AZURE_OPENAI_ENDPOINT)
        _client = AzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            timeout=OPENAI_TIMEOUT_SECONDS,
            max_retries=OPENAI_MAX_RETRIES,
        )
    return _client


def _get_async_client() -> AsyncAzureOpenAI:
    global _async_client
    if _async_client is None:
        base_endpoint = _get_base_endpoint(AZURE_OPENAI_ENDPOINT)
        _async_client = AsyncAzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            timeout=OPENAI_TIMEOUT_SECONDS,
            max_retries=OPENAI_MAX_RETRIES,
        )
    return _async_client



def chat_completion(
    system_prompt: str, user_prompt: str, temperature: float = 0.1
) -> Optional[str]:
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        logger.info(
            f"Azure OpenAI response received. Tokens used: {response.usage.total_tokens}"
        )
        return content
    except Exception as e:
        logger.error(f"Azure OpenAI call failed: {e}")
        raise


def parse_json_response(raw: str) -> dict:
   
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def truncate_content(content: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Truncate document content to avoid exceeding context window limits."""
    if len(content) <= max_chars:
        return content
    logger.warning(
        f"Document content truncated from {len(content)} to {max_chars} chars for OpenAI."
    )
    return content[:max_chars] + "\n\n[...content truncated for analysis...]"


def generate_embedding(text: str) -> list[float]:
    """
    Generate a vector embedding for the given text.
    """
    client = _get_client()
    try:
        text = truncate_content(text, max_chars=8000)
        response = client.embeddings.create(
            input=text, model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Azure OpenAI embedding failed: {e}")
        raise


async def chat_completion_async(
    system_prompt: str, user_prompt: str, temperature: float = 0.1
) -> Optional[str]:
    """Asynchronously execute chat completion on a background worker thread."""
    return await asyncio.to_thread(chat_completion, system_prompt, user_prompt, temperature)


async def generate_embedding_async(text: str) -> list[float]:
    """Asynchronously generate embedding on a background worker thread."""
    return await asyncio.to_thread(generate_embedding, text)

