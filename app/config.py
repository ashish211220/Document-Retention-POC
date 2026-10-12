import os
from dotenv import load_dotenv

load_dotenv(override=False)

# Ensure Azure CLI is on PATH so DefaultAzureCredential can find it
_az_cli_path = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"
if _az_cli_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] = os.environ.get("PATH", "") + ";" + _az_cli_path

from app.keyvault import get_secrets

# Fetch secrets from Azure Key Vault
_secret_names = [
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "AZURE_SEARCH_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DATABASE_URL",
    "SHAREPOINT_CLIENT_SECRET"
]
_secrets = get_secrets(_secret_names)

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_DOCUMENT_INTELLIGENCE_KEY = _secrets["AZURE_DOCUMENT_INTELLIGENCE_KEY"]

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = _secrets["AZURE_SEARCH_API_KEY"]
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "retention-taxonomy-v2")
SEARCH_MODE = os.getenv("SEARCH_MODE", "hybrid").lower()

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = _secrets["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = os.getenv(
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"
)
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

AUTO_TAG_CONFIDENCE_THRESHOLD = float(os.getenv("AUTO_TAG_CONFIDENCE_THRESHOLD", "70"))
MEDIUM_CONFIDENCE_THRESHOLD = float(os.getenv("MEDIUM_CONFIDENCE_THRESHOLD", "0.65"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

DATABASE_URL = _secrets["DATABASE_URL"]
# AZURE_STORAGE_CONNECTION_STRING = _secrets["AZURE_STORAGE_CONNECTION_STRING"]

SHAREPOINT_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
SHAREPOINT_CLIENT_SECRET = _secrets["SHAREPOINT_CLIENT_SECRET"]  # App-only auth (client credentials flow)
SHAREPOINT_TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID")
SHAREPOINT_LIST_ID = os.getenv("SHAREPOINT_LIST_ID")  # legacy; kept for backward compat

SHAREPOINT_SOURCE_DRIVE_ID = os.getenv("SHAREPOINT_SOURCE_DRIVE_ID")
SHAREPOINT_RETENTION_LIST_ID = os.getenv("SHAREPOINT_RETENTION_LIST_ID")
SHAREPOINT_AUDITLOG_LIST_ID = os.getenv("SHAREPOINT_AUDITLOG_LIST_ID")


SYNC_POLL_INTERVAL_MINUTES = int(os.getenv("SYNC_POLL_INTERVAL_MINUTES", "5"))
MAX_PROCESSING_RETRIES = int(os.getenv("MAX_PROCESSING_RETRIES", "3"))
SYNC_BATCH_CONCURRENCY = int(os.getenv("SYNC_BATCH_CONCURRENCY", "3"))

# --- Human review write-back ---
REVIEW_SYNC_ENABLED = os.getenv("REVIEW_SYNC_ENABLED", "true").lower() == "true"
REVIEW_SETTLE_SECONDS = int(os.getenv("REVIEW_SETTLE_SECONDS", "60"))
REVIEW_HUMAN_CONFIDENCE = float(os.getenv("REVIEW_HUMAN_CONFIDENCE", "100"))
# Retention-list column names written by Himank's Power App
REVIEW_LIST_TAGGED_COL = "DocumentTagged"
REVIEW_LIST_TAGGED_DETECT = "Reviewed"          # value that triggers write-back
REVIEW_LIST_TAGGED_DONE = "Manually Tagged"      # value backend sets when done
REVIEW_LIST_DOC_TYPE_COL = "DocumentType"        # reviewer's chosen document type
REVIEW_LIST_RETENTION_RULE_COL = "RetentionRule" # reviewer's chosen rule e.g. AL+3

# --- Fiscal Year Settings (UTIMCO: August 31) ---
FISCAL_YEAR_END_MONTH = int(os.getenv("FISCAL_YEAR_END_MONTH", "8"))  # August
FISCAL_YEAR_END_DAY = int(os.getenv("FISCAL_YEAR_END_DAY", "31"))    # 31st


import logging as _logging
_cfg_logger = _logging.getLogger(__name__)


def validate_config():
    missing = []
    if not AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
    if not AZURE_DOCUMENT_INTELLIGENCE_KEY:
        missing.append("AZURE_DOCUMENT_INTELLIGENCE_KEY")
    if SEARCH_MODE == "hybrid" and not AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME:
        missing.append("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
    if missing:
        _cfg_logger.warning(
            f"Missing AI configuration: {', '.join(missing)}. Document Intelligence or embeddings may be disabled."
        )

    sp_vars = {
        "SHAREPOINT_CLIENT_ID": SHAREPOINT_CLIENT_ID,
        "SHAREPOINT_CLIENT_SECRET": SHAREPOINT_CLIENT_SECRET,
        "SHAREPOINT_TENANT_ID": SHAREPOINT_TENANT_ID,
    }
    missing_sp = [k for k, v in sp_vars.items() if not v]
    if missing_sp:
        _cfg_logger.warning(
            f"SharePoint features disabled. Missing env vars: {', '.join(missing_sp)}"
        )
