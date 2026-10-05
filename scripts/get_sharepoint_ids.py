import asyncio
import os
import sys

# Add the project root to the Python path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.graph_auth_service import graph_auth_service
from app.config import SHAREPOINT_SITE_ID, SHAREPOINT_CLIENT_SECRET
import httpx

async def get_sharepoint_ids():
    if not SHAREPOINT_CLIENT_SECRET or SHAREPOINT_CLIENT_SECRET == "<PASTE_YOUR_COPIED_SECRET_VALUE_HERE>":
        print("❌ ERROR: SHAREPOINT_CLIENT_SECRET is missing in your .env file.")
        print("Please paste your app's client secret into the .env file and run this script again.")
        return

    print("🔑 Authenticating with Microsoft Graph...")
    try:
        token = graph_auth_service.get_access_token()
    except Exception as e:
        print(f"❌ Failed to authenticate: {e}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient() as client:
        # 1. Get Drives (Document Libraries)
        print("\n📂 Fetching Document Libraries (Drives)...")
        drives_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drives"
        resp = await client.get(drives_url, headers=headers)
        if resp.status_code == 200:
            drives = resp.json().get("value", [])
            for d in drives:
                print(f"   - Name: {d.get('name')} | ID: {d.get('id')}")
        else:
            print(f"   ❌ Failed to fetch drives: {resp.status_code} - {resp.text}")

        # 2. Get Lists
        print("\n📋 Fetching SharePoint Lists...")
        lists_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists"
        resp = await client.get(lists_url, headers=headers)
        if resp.status_code == 200:
            lists = resp.json().get("value", [])
            for lst in lists:
                # We often skip system lists (those hidden or uninteresting), but let's show all user lists
                name = lst.get('displayName')
                list_id = lst.get('id')
                print(f"   - Name: {name} | ID: {list_id}")
        else:
            print(f"   ❌ Failed to fetch lists: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    asyncio.run(get_sharepoint_ids())
