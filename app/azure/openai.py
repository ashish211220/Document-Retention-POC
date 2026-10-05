"""
Azure OpenAI client wrapper for the Document Retention System.

Handles:
- Client initialization (Azure OpenAI endpoint)
- Chat completion with structured JSON output
- Prompt truncation to avoid context window limits
- Response parsing and error handling

NOTE: The endpoint in .env may be a full URL including deployment path.
We extract the base endpoint and use deployment name separately for SDK compatibility.
"""
import json
import re
from typing import Optional
from openai import AzureOpenAI
from app.config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_DEPLOYMENT_NAME,
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME,
    AZURE_OPENAI_API_VERSION,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Max characters of document content to send to OpenAI (to stay within context limits)
MAX_CONTENT_CHARS = 8000


def _get_base_endpoint(endpoint: str) -> str:
    """
    Extract the base Azure OpenAI endpoint URL.
    Handles both:
      - Clean: https://my-resource.openai.azure.com/
      - Full path: https://my-resource.openai.azure.com/openai/deployments/gpt-4.1/chat/...
    """
    match = re.match(r"(https://[^/]+\.openai\.azure\.com/?)", endpoint)
    if match:
        return match.group(1).rstrip("/") + "/"
    return endpoint


def _get_client() -> AzureOpenAI:
    base_endpoint = _get_base_endpoint(AZURE_OPENAI_ENDPOINT)
    return AzureOpenAI(
        azure_endpoint=base_endpoint,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )


def chat_completion(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Optional[str]:
    """
    Send a chat completion request to Azure OpenAI and return the raw response text.

    Args:
        system_prompt: Instructions for the model (role + output format).
        user_prompt: The actual content/question to process.
        temperature: Low temperature (0.1) for deterministic, structured responses.

    Returns:
        Raw string response from the model, or None on failure.
    """
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
        logger.info(f"Azure OpenAI response received. Tokens used: {response.usage.total_tokens}")
        return content
    except Exception as e:
        logger.error(f"Azure OpenAI call failed: {e}")
        raise


def parse_json_response(raw: str) -> dict:
    """
    Parse the model's JSON response string into a Python dict.
    Handles common formatting issues like markdown code fences.
    """
    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def truncate_content(content: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Truncate document content to avoid exceeding context window limits."""
    if len(content) <= max_chars:
        return content
    logger.warning(f"Document content truncated from {len(content)} to {max_chars} chars for OpenAI.")
    return content[:max_chars] + "\n\n[...content truncated for analysis...]"


def generate_embedding(text: str) -> list[float]:
    """
    Generate a vector embedding for the given text.
    """
    client = _get_client()
    try:
        # Avoid hitting token limits for embeddings
        text = truncate_content(text, max_chars=8000)
        response = client.embeddings.create(
            input=text,
            model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Azure OpenAI embedding failed: {e}")
        raise
