"""
scripts/check_purview_labels.py

Sanity-check: calls the Microsoft Graph API to list all retention labels
currently published in the Purview tenant, then cross-references them against
PURVIEW_LABEL_NAME_MAP in app/config.py.

Logs a WARNING for every mapped label name that is NOT present in the tenant
so missing labels (e.g. CE_V1, AC_V1) are caught early - before a sync job
runs and produces a label_not_found failure.

Usage:
    python scripts/check_purview_labels.py

Requires:
    SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET in .env
    RecordsManagement.Read.All application permission on the app registration.

Graph endpoint used:
    GET /beta/security/labels/retentionLabels
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("check_purview_labels")

from app.config import PURVIEW_LABEL_NAME_MAP  # noqa: E402

TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID", "")
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "")

TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
LABELS_URL = "https://graph.microsoft.com/beta/security/labels/retentionLabels"


async def _get_token() -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _list_tenant_labels(token: str) -> list:
    """Returns display names of all retention labels in the tenant."""
    headers = {"Authorization": f"Bearer {token}"}
    names = []
    url = LABELS_URL

    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 403:
                logger.error(
                    "403 Forbidden - the app may be missing RecordsManagement.Read.All "
                    "permission. Grant it in Azure portal and re-run."
                )
                return []
            resp.raise_for_status()
            data = resp.json()
            for label in data.get("value", []):
                display_name = label.get("displayName") or label.get("name") or ""
                if display_name:
                    names.append(display_name)
            url = data.get("@odata.nextLink")

    return names


async def check_purview_label_sanity() -> dict:
    """
    Cross-references PURVIEW_LABEL_NAME_MAP against what is published in Purview.
    Returns a result dict with keys: tenant_labels, map_values, missing, ok.
    """
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        logger.warning(
            "Skipping Purview label sanity check - credentials not set in environment."
        )
        return {"skipped": True, "ok": True}

    logger.info("Fetching retention labels from Purview tenant ...")
    token = await _get_token()
    tenant_labels = await _list_tenant_labels(token)
    tenant_label_set = set(tenant_labels)

    map_values = list(PURVIEW_LABEL_NAME_MAP.values())
    missing = [name for name in map_values if name not in tenant_label_set]

    if missing:
        for name in missing:
            codes = [k for k, v in PURVIEW_LABEL_NAME_MAP.items() if v == name]
            logger.warning(
                "MISSING Purview label '%s' (mapped from retention_code(s): %s). "
                "Documents with these codes will produce a 'label_not_found' failure "
                "until the label is created and published in Purview.",
                name, codes,
            )
    else:
        logger.info("All labels in PURVIEW_LABEL_NAME_MAP are present in the tenant.")

    return {
        "tenant_labels": sorted(tenant_labels),
        "map_values": sorted(map_values),
        "missing": missing,
        "ok": len(missing) == 0,
    }


if __name__ == "__main__":
    result = asyncio.run(check_purview_label_sanity())
    if result.get("missing"):
        print(
            "\n[RESULT] %d label(s) missing from Purview tenant:" % len(result["missing"])
        )
        for n in result["missing"]:
            print("  -", n)
        sys.exit(1)
    elif result.get("skipped"):
        print("[RESULT] Check skipped - credentials not configured.")
        sys.exit(0)
    else:
        print("[RESULT] All mapped Purview labels exist in the tenant.")
        sys.exit(0)
