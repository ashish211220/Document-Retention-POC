import os
import requests
from dotenv import load_dotenv

load_dotenv()

endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY")

if not endpoint or not key:
    print("Error: Missing endpoint or key in .env")
    exit(1)

# Ensure endpoint does not have trailing slash for clean URL building
endpoint = endpoint.rstrip("/")

# Endpoint to list custom models (acts as a simple ping/auth check)
# Document Intelligence v4.0 GA (api-version=2024-11-30)
url = f"{endpoint}/documentintelligence/documentModels?api-version=2024-11-30"

headers = {
    "Ocp-Apim-Subscription-Key": key
}

try:
    print(f"Connecting to: {endpoint}")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        print("SUCCESS: Connected to Azure Document Intelligence successfully!")
        print("Response received from Azure.")
    else:
        print(f"FAILED: Connection to Azure failed with status {response.status_code}.")
        print(f"Response: {response.text}")
except Exception as e:
    print(f"ERROR: Could not connect to Azure. Exception: {e}")
