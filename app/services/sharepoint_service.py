import httpx
from typing import List, Dict, Any
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_LIST_ID, PURVIEW_LABEL_NAME_MAP

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

class SharePointService:
    def __init__(self):
        self.site_id = SHAREPOINT_SITE_ID
        self.list_id = SHAREPOINT_LIST_ID

    async def get_documents(self, token: str) -> List[Dict[str, Any]]:
        """
        Lists files in the configured SharePoint List.
        """
        if not self.site_id or not self.list_id:
            raise ValueError("SHAREPOINT_SITE_ID and SHAREPOINT_LIST_ID must be configured.")
            
        # Expand driveItem to get file details
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items?$expand=driveItem"
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            # Filter for items that actually have a file attached
            return [item for item in data.get("value", []) if "driveItem" in item and "file" in item["driveItem"]]

    async def download_document(self, token: str, item_id: str) -> bytes:
        """
        Downloads the content of a specific document by its list item ID.
        """
        if not self.site_id or not self.list_id:
            raise ValueError("SHAREPOINT_SITE_ID and SHAREPOINT_LIST_ID must be configured.")
            
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items/{item_id}/driveItem/content"
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            # Graph API often redirects to a download URL for content
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            return response.content

    async def update_metadata_and_label(self, token: str, item_id: str, metadata: Dict[str, Any], retention_code: str) -> Dict[str, Any]:
        """
        Updates the SharePoint list item fields with new metadata and the retention label.
        """
        if not self.site_id or not self.list_id:
            raise ValueError("SHAREPOINT_SITE_ID and SHAREPOINT_LIST_ID must be configured.")
            
        url = f"{GRAPH_API_BASE}/sites/{self.site_id}/lists/{self.list_id}/items/{item_id}/retentionLabel"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # You mentioned you only create the base label (e.g., "AL", "AV") in Purview
        payload = {
            "retentionLabel": {
                "name": retention_code
            }
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            
            try:
                return response.json()
            except Exception:
                return {"status": "success"}

sharepoint_service = SharePointService()
