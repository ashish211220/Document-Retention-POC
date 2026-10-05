# Document Retention AI POC

This Proof of Concept (POC) is an AI-powered Document Classification and Retention Management System designed to integrate seamlessly with Microsoft SharePoint. It automatically classifies uploaded documents against a corporate retention taxonomy and applies Microsoft Purview retention labels.

## Architecture & Workflow

The application operates as a background service that automatically polls SharePoint and processes documents via an AI pipeline:

1. **SharePoint Polling (Idempotent Sync):** 
   A background scheduler polls a designated SharePoint Document Library. It identifies newly uploaded or human-modified documents, ignoring its own metadata syncs to prevent infinite loops.
2. **Universal OCR:** 
   Extracts text from various file formats (PDF, Word, Excel, Images) using **Azure Document Intelligence**.
3. **Semantic Understanding:** 
   Sends the raw text to **Azure OpenAI (GPT-4.x)** to generate a contextual summary and keywords.
4. **Hybrid AI Search (RAG):** 
   Embeds the summary and queries an **Azure AI Search** vector index to find the Top 5 most relevant retention rules from the master `retention_taxonomy.json`.
5. **Smart Classification & Guardrails:**
   GPT-4 selects the final rule and assigns a Confidence Score (0.0 to 1.0).
   - If **Confidence >= 70%**: Auto-Tagged, and Purview label applied.
   - If **Confidence < 70%**: Review Pending (human-in-the-loop required).
6. **SharePoint Write-Back (4-Step Sync):**
   - Applies the Purview Label.
   - Patches metadata back to the Source Document.
   - Upserts the document tracking record to a central Retention List.
   - Writes an immutable record to the Audit Log.

## Technologies Used

* **Backend:** Python, FastAPI, SQLAlchemy
* **Database:** Azure PostgreSQL
* **Cloud AI:** Azure OpenAI (GPT-4, text-embedding-3-small), Azure Document Intelligence (prebuilt-layout)
* **Search:** Azure AI Search (Hybrid Vector + Keyword search)
* **Integrations:** Microsoft Graph API (SharePoint & Purview)
* **Frontend Dashboard:** Vanilla JS/HTML/CSS for human review

## Setup Instructions

### 1. Prerequisites
* Python 3.11+
* PostgreSQL database
* Azure Services (OpenAI, Document Intelligence, AI Search)
* Microsoft Entra ID App Registration (with SharePoint/Graph API permissions)

### 2. Installation
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file based on `.env.example`. You will need:
* Azure endpoints and API keys.
* PostgreSQL connection string.
* Microsoft Graph Client ID, Secret, and Tenant ID.
* SharePoint IDs (Site ID, Drive ID, List IDs).

*Note: Never commit your `.env` file to version control.*

### 4. Running the Application
Start the FastAPI server (which automatically provisions the DB, loads the taxonomy, and starts the background sync scheduler):

```bash
python app/main.py
# OR
uvicorn app.main:app --reload
```

The application and dashboard will be available at: `http://127.0.0.1:8000/`

## Code Structure

- `app/main.py`: Entry point and startup logic.
- `app/sync/scheduler.py`: Background polling and orchestration loop.
- `app/sync/sharepoint_sync.py`: Microsoft Graph API integrations.
- `app/services/`: AI pipelines, business logic, retention date calculators, and validation.
- `app/models/`: Database schema and Pydantic validation models.
- `data/retention_taxonomy.json`: Master rulebook for retention categories.
