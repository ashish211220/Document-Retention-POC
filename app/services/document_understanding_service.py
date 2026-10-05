"""
Document Understanding Service.

Uses Azure OpenAI to analyze a DocumentProfile and extract structured
business metadata: title, summary, department, keywords, entities,
document type indicators, and a suggested search query.

CRITICAL BUSINESS RULE:
  This service MUST NEVER return retention rules, retention labels,
  categories, or classification data. It only understands the document.
  Classification happens in Phase 5 using the approved taxonomy.
"""
from typing import Optional
from app.models.document import DocumentProfile
from app.models.classification import DocumentUnderstanding
from app.azure.openai import chat_completion, parse_json_response, truncate_content
from app.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert document analyst for an enterprise document management system.

Your task is to analyze document content and extract structured metadata about the document.

STRICT RULES:
1. Respond ONLY with a valid JSON object. No explanation, no markdown, no preamble.
2. NEVER invent or guess retention periods, retention labels, categories, or classification values.
3. NEVER include fields like "retention_rule", "retention_period", "retention_label", or "classification" in your response.
4. If information is not clearly present in the document, use null for that field.
5. Keep keywords concise (2-3 words max per keyword). Return 3-8 keywords.
6. The suggested_search_query should be a short, natural-language phrase (5-10 words) that best describes this document's type and purpose for searching a records retention taxonomy.

Return ONLY this JSON structure:
{
  "title": "string or null",
  "summary": "1-3 sentence summary of the document purpose",
  "department": "string or null — the organizational department this document belongs to",
  "team_owner": "string or null — the team or function responsible",
  "document_date": "YYYY-MM-DD or null",
  "author": "string or null",
  "keywords": ["keyword1", "keyword2"],
  "entities": ["entity1", "entity2"],
  "document_type_indicators": ["indicator1", "indicator2"],
  "suggested_search_query": "short descriptive query for taxonomy search"
}"""


def analyze_document(profile: DocumentProfile) -> Optional[DocumentUnderstanding]:
    """
    Run AI understanding on a DocumentProfile.

    Args:
        profile: The normalized DocumentProfile from Azure Document Intelligence.

    Returns:
        A DocumentUnderstanding object, or None if OpenAI is not configured or fails.
    """
    from app.config import AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
        logger.warning("Azure OpenAI not configured — skipping document understanding.")
        return None

    logger.info(f"Starting document understanding for: {profile.document_name}")

    # Build the user prompt from available content
    content = truncate_content(profile.content)

    # Include paragraph roles for better context (title, section heading, etc.)
    structured_hints = []
    for para in profile.structure.paragraphs[:20]:  # First 20 paragraphs
        role = para.role or "body"
        if para.role in ("title", "sectionHeading", "pageHeader"):
            structured_hints.append(f"[{role.upper()}] {para.content}")

    hints_text = "\n".join(structured_hints) if structured_hints else "No structured headings detected."

    user_prompt = f"""Document Name: {profile.document_name}
File Type: {profile.file_type}
Page Count: {profile.metadata.page_count}

--- Document Structure (Headings & Titles) ---
{hints_text}

--- Full Document Content ---
{content}
"""

    try:
        raw_response = chat_completion(SYSTEM_PROMPT, user_prompt, temperature=0.0)
        parsed = parse_json_response(raw_response)

        understanding = DocumentUnderstanding(
            title=parsed.get("title"),
            summary=parsed.get("summary"),
            department=parsed.get("department"),
            team_owner=parsed.get("team_owner"),
            document_date=parsed.get("document_date"),
            author=parsed.get("author"),
            keywords=parsed.get("keywords", []),
            entities=parsed.get("entities", []),
            document_type_indicators=parsed.get("document_type_indicators", []),
            suggested_search_query=parsed.get("suggested_search_query"),
        )

        logger.info(
            f"Document understanding complete for '{profile.document_name}': "
            f"title='{understanding.title}', query='{understanding.suggested_search_query}'"
        )
        return understanding

    except Exception as e:
        logger.error(f"Document understanding failed for '{profile.document_name}': {e}")
        return None
