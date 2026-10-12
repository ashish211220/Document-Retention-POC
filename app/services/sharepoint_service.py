import httpx
from typing import List, Dict, Any
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_LIST_ID

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


class SharePointService:
    def __init__(self):
        self.site_id = SHAREPOINT_SITE_ID
        self.list_id = SHAREPOINT_LIST_ID

    async def get_documents(self, token: str) -> List[Dict[str, Any]]:
        if not self.site_id or not self.list_id:
            raise ValueError(
                "SHAREPOINT_SITE_ID and SHAREPOINT_LIST_ID must be configured."
            )

        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items?$expand=driveItem"
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return [
                item
                for item in data.get("value", [])
                if "driveItem" in item and "file" in item["driveItem"]
            ]


sharepoint_service = SharePointService()
