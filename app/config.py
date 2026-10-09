import os
from dotenv import load_dotenv

load_dotenv(override=True)

# Azure Key Vault Configuration
AZURE_KEY_VAULT_URL = os.getenv("AZURE_KEY_VAULT_URL")
_secret_client = None

if AZURE_KEY_VAULT_URL:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        import logging
        logging.info(f"Connecting to Azure Key Vault: {AZURE_KEY_VAULT_URL}")
        credential = DefaultAzureCredential()
        _secret_client = SecretClient(vault_url=AZURE_KEY_VAULT_URL, credential=credential)
    except ImportError:
        import logging
        logging.warning("Azure identity/keyvault packages not installed. Falling back to local env.")

def get_secret(env_key: str, default: str = None) -> str:
    """
    Fetches a secret from Azure Key Vault if configured, otherwise falls back to local environment.
    AKV secret names do not support underscores, so they are converted to hyphens.
    """
    if _secret_client:
        akv_key = env_key.replace("_", "-")
        try:
            # Note: synchronous fetching blocks startup, but is acceptable during app initialization
            secret = _secret_client.get_secret(akv_key)
            if secret.value is not None:
                return secret.value
        except Exception as e:
            # If secret is missing or access denied, fall back to environment variables
            import logging
            logging.debug(f"Failed to fetch {akv_key} from AKV: {e}. Falling back to env.")
    
    return os.getenv(env_key, default)

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = get_secret("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
AZURE_DOCUMENT_INTELLIGENCE_KEY = get_secret("AZURE_DOCUMENT_INTELLIGENCE_KEY")

AZURE_SEARCH_ENDPOINT = get_secret("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_API_KEY = get_secret("AZURE_SEARCH_API_KEY")
AZURE_SEARCH_INDEX_NAME = get_secret("AZURE_SEARCH_INDEX_NAME", "retention-taxonomy-v2")
SEARCH_MODE = get_secret("SEARCH_MODE", "hybrid").lower()

AZURE_OPENAI_ENDPOINT = get_secret("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = get_secret("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = get_secret("AZURE_OPENAI_DEPLOYMENT_NAME")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = get_secret("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
AZURE_OPENAI_API_VERSION = get_secret("AZURE_OPENAI_API_VERSION", "2024-02-01")

AUTO_TAG_CONFIDENCE_THRESHOLD = float(get_secret("AUTO_TAG_CONFIDENCE_THRESHOLD", "70"))
MEDIUM_CONFIDENCE_THRESHOLD = float(get_secret("MEDIUM_CONFIDENCE_THRESHOLD", "0.65"))
BATCH_SIZE = int(get_secret("BATCH_SIZE", "10"))

DATABASE_URL = get_secret("DATABASE_URL")
AZURE_STORAGE_CONNECTION_STRING = get_secret("AZURE_STORAGE_CONNECTION_STRING")

SHAREPOINT_CLIENT_ID = get_secret("SHAREPOINT_CLIENT_ID")
SHAREPOINT_CLIENT_SECRET = get_secret("SHAREPOINT_CLIENT_SECRET")  # App-only auth (client credentials flow)
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


# Legacy base-code map — kept for PURVIEW_LABEL_STRATEGY=base_code rollback only
PURVIEW_LABEL_NAME_MAP: dict[str, str] = {
    "AL": "AL_V1",
    "AV": "AV_V1",
    "FE": "FE_V1",
    "LA": "LA_V1",
    "US": "US_V1",
    "CE": "CE_V1",
    "AC": "AC_V1",
    "PM": "Forever",
}
PURVIEW_LABEL_MAPPING = PURVIEW_LABEL_NAME_MAP

# 'per_rule' = look up FE+2 etc. from app/purview_labels.json (default)
# 'base_code' = old behavior: look up FE, AL, etc. from PURVIEW_LABEL_NAME_MAP above
PURVIEW_LABEL_STRATEGY = os.getenv("PURVIEW_LABEL_STRATEGY", "per_rule").lower()

# When True, falls back to base_code label if per_rule mapping is missing (testing only)
ALLOW_BASE_CODE_FALLBACK = os.getenv("ALLOW_BASE_CODE_FALLBACK", "false").lower() == "true"


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
