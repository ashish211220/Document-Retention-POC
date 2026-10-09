"""
scripts/debug_reviewed_items.py
Prints the raw Graph API fields for all "Reviewed" retention list items.
Run this to find the exact internal column names SharePoint uses.

Usage:
    python scripts/debug_reviewed_items.py
"""
import asyncio, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(override=True)

import httpx
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_RETENTION_LIST_ID
from app.services.graph_auth_service import graph_auth_service

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def run():
    if graph_auth_service is None:
        print("ERROR: SharePoint auth not configured.")
        return

    token = graph_auth_service.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    url = (
        f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_RETENTION_LIST_ID}/items"
        f"?$expand=fields&$select=id,fields,lastModifiedDateTime,lastModifiedBy&$top=100"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        items = resp.json().get("value", [])

    reviewed = [
        i for i in items
        if i.get("fields", {}).get("DocumentTagged") == "Reviewed"
    ]

    print(f"\nFound {len(reviewed)} item(s) with DocumentTagged = 'Reviewed'\n")

    for item in reviewed:
        fields = item.get("fields", {})
        modified_by = item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "?")
        print(f"{'='*60}")
        print(f"  List Item ID       : {item.get('id')}")
        print(f"  lastModifiedDateTime: {item.get('lastModifiedDateTime')}")
        print(f"  lastModifiedBy     : {modified_by}")
        print(f"\n  ALL FIELD KEYS returned by Graph:")
        for k, v in sorted(fields.items()):
            print(f"    {k!r:40} = {v!r}")
        print()


if __name__ == "__main__":
    asyncio.run(run())
