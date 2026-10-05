"""
Graph Auth Service — Client Credentials (Application) flow.

Since Sites.ReadWrite.All is configured as an Application permission (not Delegated),
the app authenticates directly using its own Client ID + Secret. No user sign-in,
no device code, no browser redirect is needed.

Token acquisition:
  - Uses msal.ConfidentialClientApplication with acquire_token_for_client().
  - Scope must be "https://graph.microsoft.com/.default" (not the individual permission
    string) — this tells AAD to issue a token for all Application permissions that have
    been granted admin consent on the app registration.
  - MSAL caches the token in memory and returns a cached token on subsequent calls
    until it expires (typically 1 hour), at which point it silently re-acquires.

Required environment variables:
  SHAREPOINT_CLIENT_ID     — App Registration Application (client) ID
  SHAREPOINT_CLIENT_SECRET — Client secret created in the App Registration
  SHAREPOINT_TENANT_ID     — Directory (tenant) ID
"""

import logging
import msal
from app.config import SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID

logger = logging.getLogger(__name__)

AUTHORITY = f"https://login.microsoftonline.com/{SHAREPOINT_TENANT_ID}"

# For application (app-only) permissions, the scope is always ".default".
# This tells AAD to issue a token covering all Application permissions that
# have been granted admin consent on the app registration.
APP_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphAuthService:
    def __init__(self):
        if not SHAREPOINT_CLIENT_ID or not SHAREPOINT_CLIENT_SECRET or not SHAREPOINT_TENANT_ID:
            raise ValueError(
                "SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, and SHAREPOINT_TENANT_ID "
                "must all be set for app-only Graph API authentication."
            )
        # ConfidentialClientApplication is used for server-side / daemon flows
        # where the application authenticates with its own credentials.
        self._app = msal.ConfidentialClientApplication(
            client_id=SHAREPOINT_CLIENT_ID,
            client_credential=SHAREPOINT_CLIENT_SECRET,
            authority=AUTHORITY,
        )

    def get_access_token(self) -> str:
        """
        Acquires (or returns a cached) access token using the client credentials flow.

        MSAL checks its in-memory cache first and silently re-acquires when the token
        is about to expire — no external state or refresh token management needed.

        Returns:
            A valid bearer token string ready to use in Authorization headers.

        Raises:
            Exception: if token acquisition fails (wrong secret, no admin consent, etc.)
        """
        # Try the cache first (MSAL handles expiry internally)
        result = self._app.acquire_token_silent(scopes=APP_SCOPES, account=None)

        if not result:
            # Cache miss or expired — fetch a fresh token from AAD
            logger.info("Acquiring new Graph API token via client credentials flow...")
            result = self._app.acquire_token_for_client(scopes=APP_SCOPES)

        if "access_token" in result:
            logger.debug("Graph API token acquired successfully.")
            return result["access_token"]

        error = result.get("error", "unknown_error")
        description = result.get("error_description", "No description provided.")
        raise Exception(
            f"Graph API token acquisition failed: [{error}] {description}\n"
            f"Ensure admin consent has been granted for Sites.ReadWrite.All "
            f"(Application) on your app registration."
        )


# Module-level singleton — initialised once at startup if config is present.
if SHAREPOINT_CLIENT_ID and SHAREPOINT_CLIENT_SECRET and SHAREPOINT_TENANT_ID:
    logger.info("GraphAuthService (client credentials) initialised successfully.")
    graph_auth_service = GraphAuthService()
else:
    missing = []
    if not SHAREPOINT_CLIENT_ID:
        missing.append("SHAREPOINT_CLIENT_ID")
    if not SHAREPOINT_CLIENT_SECRET:
        missing.append("SHAREPOINT_CLIENT_SECRET")
    if not SHAREPOINT_TENANT_ID:
        missing.append("SHAREPOINT_TENANT_ID")
    logger.warning(
        f"GraphAuthService not initialised. Missing config: {', '.join(missing)}. "
        f"SharePoint features will be unavailable."
    )
    graph_auth_service = None

