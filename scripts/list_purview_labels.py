"""scripts/list_purview_labels.pyDiagnostic script to verify exact internal Purview retention label names.
It attempts to use the tenant-wide labels API first:
    GET /beta/security/labels/retentionLabels

If that fails (401/403), it can inspect a specific SharePoint document
that has been manually labeled in the UI to see what exact internal 'name'
the Graph API reports.

Usage:
    python scripts/list_purview_labels.py [optional_item_id_manually_labeled]
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
logger = logging.getLogger("list_purview_labels")

from app.config import PURVIEW_LABEL_NAME_MAP, SHAREPOINT_SOURCE_DRIVE_ID

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


async def _check_mapped_names(found_labels: list[dict]):
    print("\n" + "="*60)
    print("MAPPING VERIFICATION")
    print("="*60)
    
    name_lookup = {lbl.get("name"): lbl for lbl in found_labels if lbl.get("name")}
    display_name_lookup = {lbl.get("displayName"): lbl for lbl in found_labels if lbl.get("displayName")}
    
    for code, expected_name in PURVIEW_LABEL_NAME_MAP.items():
        if expected_name in name_lookup:
            print(f"[EXACT MATCH] {code} -> '{expected_name}' exists as an internal name.")
        elif expected_name in display_name_lookup:
            actual_internal = display_name_lookup[expected_name].get("name")
            print(f"[MISMATCH] {code} -> '{expected_name}' is only a DISPLAY name. "
                  f"The actual internal name you must use is: '{actual_internal}'")
        else:
            substring_matches = []
            for lbl in found_labels:
                if expected_name.lower() in (lbl.get("name", "") or "").lower() or \
                   expected_name.lower() in (lbl.get("displayName", "") or "").lower():
                    substring_matches.append(lbl)
            
            if substring_matches:
                print(f"[PARTIAL] {code} -> '{expected_name}' not found exactly. Similar labels:")
                for match in substring_matches:
                    print(f"    - name: '{match.get('name')}', displayName: '{match.get('displayName')}'")
            else:
                print(f"[NOT FOUND] {code} -> '{expected_name}' does not match any name or displayName.")
    print("="*60 + "\n")


async def run_diagnostic(manual_item_id: str = None):
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        logger.error("Missing credentials in environment variables.")
        return

    logger.info("Obtaining token...")
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}

    logger.info(f"Attempting to fetch tenant-wide labels from {LABELS_URL} ...")
    
    found_labels = []
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(LABELS_URL, headers=headers)
        
        if resp.status_code in (401, 403):
            logger.warning(f"Tenant-wide labels API failed with {resp.status_code} (missing RecordsManagement.Read.All permission).")
        else:
            resp.raise_for_status()
            data = resp.json()
            found_labels = data.get("value", [])
            
            print("\n" + "="*60)
            print(f"FOUND {len(found_labels)} TENANT LABELS")
            print("="*60)
            for lbl in found_labels:
                name = lbl.get("name")
                display = lbl.get("displayName")
                
                highlight = ""
                if "forever" in str(name).lower() or "forever" in str(display).lower():
                    highlight = " <---- FOREVER MATCG FOUND"
                
                print(f"name: '{name}' | displayName: '{display}' {highlight}")
            
            await _check_mapped_names(found_labels)

    if manual_item_id:
        logger.info(f"\nFetching specific labeled item: {manual_item_id} ...")
        drive_url = f"https://graph.microsoft.com/v1.0/drives/{SHAREPOINT_SOURCE_DRIVE_ID}/items/{manual_item_id}?$select=id,name,retentionLabel"
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(drive_url, headers=headers)
            
            if resp.status_code == 404:
                logger.error(f"Item {manual_item_id} not found in drive {SHAREPOINT_SOURCE_DRIVE_ID}.")
                return
            
            resp.raise_for_status()
            item_data = resp.json()
            
            print("\n" + "="*60)
            print("ITEM INSPECTION")
            print("="*60)
            print(f"File Name: {item_data.get('name')}")
            retention_label = item_data.get("retentionLabel")
            if retention_label:
                actual_name = retention_label.get('name')
                print(f"APPLIED RETENTION LABEL (Internal Name): '{actual_name}'")
                
                if actual_name != "Forever" and actual_name:
                    print(f"\nNOTE: If this file was manually labeled 'Forever' in the UI,")
                    print(f"the internal API name is actually '{actual_name}'.")
                    print(f"You must update PURVIEW_LABEL_NAME_MAP['PM'] to '{actual_name}'.")
            else:
                print("NO RETENTION LABEL APPLIED TO THIS ITEM.")
            print("="*60 + "\n")

if __name__ == "__main__":
    item_id = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_diagnostic(item_id))
