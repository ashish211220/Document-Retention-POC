import asyncio
import httpx
from app.services.graph_auth_service import graph_auth_service
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_RETENTION_LIST_ID, SHAREPOINT_AUDITLOG_LIST_ID, SHAREPOINT_SOURCE_DRIVE_ID

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

async def main():
    token = graph_auth_service.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient(timeout=30) as client:
        # Get Drive ID list columns (Drive maps to a list)
        print("\n--- POC_Source_Documents Columns ---")
        url1 = f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/drives/{SHAREPOINT_SOURCE_DRIVE_ID}/list/columns"
        resp1 = await client.get(url1, headers=headers)
        if resp1.status_code == 200:
            for col in resp1.json().get("value", []):
                if not col.get("readOnly") and not col.get("hidden"):
                    print(f"- {col['name']} (Type: {col.get('text', col.get('dateTime', col.get('number', col.get('boolean', col))))})")
        else:
            print(f"Error {resp1.status_code}: {resp1.text}")

        print("\n--- POC_Documents_Retention Columns ---")
        url2 = f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_RETENTION_LIST_ID}/columns"
        resp2 = await client.get(url2, headers=headers)
        if resp2.status_code == 200:
            for col in resp2.json().get("value", []):
                if not col.get("readOnly") and not col.get("hidden"):
                    print(f"- {col['name']} (Type: {col.get('text', col.get('dateTime', col.get('number', col.get('boolean', col))))})")
        else:
            print(f"Error {resp2.status_code}: {resp2.text}")

if __name__ == "__main__":
    asyncio.run(main())
