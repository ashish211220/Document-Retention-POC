import os
from dotenv import load_dotenv

load_dotenv(override=True)

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_DOCUMENT_INTELLIGENCE_KEY = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = os.getenv("AZURE_SEARCH_API_KEY")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "retention-taxonomy-v2")
SEARCH_MODE = os.getenv("SEARCH_MODE", "hybrid").lower()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = os.getenv(
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"
)
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

AUTO_TAG_CONFIDENCE_THRESHOLD = float(os.getenv("AUTO_TAG_CONFIDENCE_THRESHOLD", "70"))
MEDIUM_CONFIDENCE_THRESHOLD = float(os.getenv("MEDIUM_CONFIDENCE_THRESHOLD", "0.65"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

DATABASE_URL = os.getenv("DATABASE_URL")
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

SHAREPOINT_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
SHAREPOINT_CLIENT_SECRET = os.getenv(
    "SHAREPOINT_CLIENT_SECRET"
)  # App-only auth (client credentials flow)
SHAREPOINT_TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID")
SHAREPOINT_LIST_ID = os.getenv("SHAREPOINT_LIST_ID")  # legacy; kept for backward compat

SHAREPOINT_SOURCE_DRIVE_ID = os.getenv("SHAREPOINT_SOURCE_DRIVE_ID")
SHAREPOINT_RETENTION_LIST_ID = os.getenv("SHAREPOINT_RETENTION_LIST_ID")
SHAREPOINT_AUDITLOG_LIST_ID = os.getenv("SHAREPOINT_AUDITLOG_LIST_ID")


SYNC_POLL_INTERVAL_MINUTES = int(os.getenv("SYNC_POLL_INTERVAL_MINUTES", "5"))
MAX_PROCESSING_RETRIES = int(os.getenv("MAX_PROCESSING_RETRIES", "3"))
SYNC_BATCH_CONCURRENCY = int(os.getenv("SYNC_BATCH_CONCURRENCY", "3"))


PURVIEW_LABEL_NAME_MAP: dict[str, str] = {
    "AL": "AL_V1",  # Confirm AL_V1 vs AL_V2 has 'Mark items as a record' in Purview portal
    "AV": "AV_V1",
    "FE": "FE_V1",
    "LA": "LA_V1",
    "US": "US_V1",
    "CE": "CE_V1",  # NOT YET CREATED in Purview — will cause label_not_found on sync
    "AC": "AC_V1",  # NOT YET CREATED in Purview — will cause label_not_found on sync
    "PM": "Forever",
}

PURVIEW_LABEL_MAPPING = PURVIEW_LABEL_NAME_MAP


def validate_config():
    if not AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT:
        raise ValueError(
            "Missing environment variable: AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
        )
    if not AZURE_DOCUMENT_INTELLIGENCE_KEY:
        raise ValueError(
            "Missing environment variable: AZURE_DOCUMENT_INTELLIGENCE_KEY"
        )
    if SEARCH_MODE == "hybrid" and not AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME:
        raise ValueError(
            "Missing environment variable: AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME is required when SEARCH_MODE='hybrid'"
        )

    sp_vars = {
        "SHAREPOINT_CLIENT_ID": SHAREPOINT_CLIENT_ID,
        "SHAREPOINT_CLIENT_SECRET": SHAREPOINT_CLIENT_SECRET,
        "SHAREPOINT_TENANT_ID": SHAREPOINT_TENANT_ID,
    }
    missing_sp = [k for k, v in sp_vars.items() if not v]
    if missing_sp:
        print(
            f"WARNING: SharePoint features disabled. Missing env vars: {', '.join(missing_sp)}"
        )


validate_config()
