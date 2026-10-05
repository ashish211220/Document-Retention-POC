import asyncio
import os
import sys
import logging
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.services.graph_auth_service import graph_auth_service
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_RETENTION_LIST_ID, SHAREPOINT_AUDITLOG_LIST_ID

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Define the columns we need for each list/library
TEXT_COLUMN = {"text": {}}
NUMBER_COLUMN = {"number": {}}
DATETIME_COLUMN = {"dateTime": {"displayAs": "default", "format": "dateOnly"}}
URL_COLUMN = {"hyperlinkOrPicture": {"isPicture": False}}

# POC_Source_Documents (We need the List ID for the Drive. We can get it via the drive endpoint)
DOC_COLUMNS = [
    {"name": "RetentionCategory", "displayName": "Retention Category", "text": {}},
    {"name": "DocumentType", "displayName": "Document Type", "text": {}},
    {"name": "RetentionCode", "displayName": "Retention Code", "text": {}},
    {"name": "RetentionRule", "displayName": "Retention Rule", "text": {}},
    {"name": "RetentionEndDate", "displayName": "Retention End Date", "dateTime": {}},
    {"name": "ConfidenceScore", "displayName": "Confidence Score", "number": {}},
    {"name": "TeamOwner", "displayName": "Team Owner", "text": {}},
    {"name": "ClassificationStatus", "displayName": "Classification Status", "text": {}}
]

RETENTION_LIST_COLUMNS = [
    {"name": "DocumentID", "displayName": "DocumentID", "text": {}},
    {"name": "ClassificationStatus", "displayName": "Classification Status", "text": {}},
    {"name": "RetentionCategory", "displayName": "Retention Category", "text": {}},
    {"name": "RetentionRule", "displayName": "Retention Rule", "text": {}},
    {"name": "RetentionCode", "displayName": "Retention Code", "text": {}},
    {"name": "ExpirationDate", "displayName": "Expiration Date", "dateTime": {}},
    {"name": "TeamOwner", "displayName": "Team Owner", "text": {}},
    {"name": "ConfidenceScore", "displayName": "Confidence Score", "number": {}},
    {"name": "DocumentURL", "displayName": "Document URL", "text": {}} # Actually a string is fine if hyperlink fails
]

AUDIT_LOG_COLUMNS = [
    {"name": "DocumentID", "displayName": "DocumentID", "text": {}},
    {"name": "Action", "displayName": "Action", "text": {}},
    {"name": "TriggeredBy", "displayName": "Triggered By", "text": {}},
    {"name": "Details", "displayName": "Details", "text": {"allowMultipleLines": True}},
    {"name": "ClassificationStatus", "displayName": "Classification Status", "text": {}},
    {"name": "ConfidenceScore", "displayName": "Confidence Score", "number": {}},
    {"name": "RetentionCategory", "displayName": "Retention Category", "text": {}},
    {"name": "RetentionCode", "displayName": "Retention Code", "text": {}}
]

async def create_column(client, token, list_id, col_def):
    url = f"{GRAPH_BASE}/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/columns"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    resp = await client.post(url, headers=headers, json=col_def)
    if resp.status_code == 201:
        print(f"  [+] Created column: {col_def['name']}")
    elif resp.status_code == 409 or "already exists" in resp.text:
        print(f"  [*] Column already exists: {col_def['name']}")
    else:
        print(f"  [!] Failed to create {col_def['name']}: {resp.status_code} - {resp.text}")

async def run():
    print("Acquiring token...")
    token = graph_auth_service.get_access_token()
    
    async with httpx.AsyncClient(timeout=30) as client:
        # First, resolve the list ID for the source documents drive
        print("\nResolving list ID for POC_Source_Documents...")
        from app.config import SHAREPOINT_SOURCE_DRIVE_ID
        drive_url = f"{GRAPH_BASE}/drives/{SHAREPOINT_SOURCE_DRIVE_ID}/list"
        resp = await client.get(drive_url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            print(f"Failed to get drive list: {resp.status_code} - {resp.text}")
            return
            
        source_list_id = resp.json()["id"]
        print(f"Source List ID: {source_list_id}")
        
        print("\n--- Creating Columns for POC_Source_Documents ---")
        for col in DOC_COLUMNS:
            await create_column(client, token, source_list_id, col)
            
        print("\n--- Creating Columns for POC_Documents_Retention List ---")
        for col in RETENTION_LIST_COLUMNS:
            await create_column(client, token, SHAREPOINT_RETENTION_LIST_ID, col)
            
        print("\n--- Creating Columns for POC_classification_auditlog List ---")
        for col in AUDIT_LOG_COLUMNS:
            await create_column(client, token, SHAREPOINT_AUDITLOG_LIST_ID, col)
            
    print("\nColumn creation script finished.")

if __name__ == "__main__":
    asyncio.run(run())
