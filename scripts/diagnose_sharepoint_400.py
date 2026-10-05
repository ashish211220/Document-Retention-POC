import asyncio
import os
import sys
import logging
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.services.graph_auth_service import graph_auth_service
from app.config import SHAREPOINT_SOURCE_DRIVE_ID, SHAREPOINT_SITE_ID, SHAREPOINT_RETENTION_LIST_ID
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

async def run():
    token = graph_auth_service.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Try step 2: update metadata columns
    item_id = "01R4ENU5PEVWSTZX3GBNC3WYV4M2QULE7S" # From the logs
    
    # Payload similar to what sharepoint_sync.py sends
    update_data = {
        "RetentionCategory": "General",
        "DocumentType": "Endowment report workpapers (includes endowment performance review)",
        "RetentionCode": "FE",
        "RetentionRule": "FE+3",
        "RetentionEndDate": "2008-03-09T00:00:00Z",
        "ConfidenceScore": 0.63,
        "TeamOwner": "OAR",
        "ClassificationStatus": "Eligible for Review"
    }
    url = f"{GRAPH_BASE}/drives/{SHAREPOINT_SOURCE_DRIVE_ID}/items/{item_id}/listItem/fields"
    
    print(f"PATCH {url}")
    async with httpx.AsyncClient() as client:
        resp = await client.patch(url, headers=headers, json=update_data)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")
        
    # Also test list lookup
    filter_url = f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_RETENTION_LIST_ID}/items?$filter=fields/DocumentID eq '{item_id}'&$expand=fields&$select=id,fields"
    print(f"\nGET {filter_url}")
    async with httpx.AsyncClient() as client:
        resp = await client.get(filter_url, headers={"Authorization": f"Bearer {token}"})
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")

if __name__ == "__main__":
    asyncio.run(run())
