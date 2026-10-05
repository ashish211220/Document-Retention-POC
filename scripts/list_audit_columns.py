import asyncio
import httpx
from app.services.graph_auth_service import graph_auth_service
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_AUDITLOG_LIST_ID

async def main():
    token = graph_auth_service.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_AUDITLOG_LIST_ID}/columns", headers=headers)
        for col in r.json().get('value', []):
            if not col.get('readOnly') and not col.get('hidden'):
                type_info = col.get("text", col.get("number", col.get("dateTime", col.get("boolean", "Unknown"))))
                print(f"{col['name']}: {type_info}")

if __name__ == "__main__":
    asyncio.run(main())
