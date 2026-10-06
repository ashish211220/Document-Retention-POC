<<<<<<< HEAD
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
=======
# Introduction 
TODO: Give a short introduction of your project. Let this section explain the objectives or the motivation behind this project. 

# Getting Started
TODO: Guide users through getting your code up and running on their own system. In this section you can talk about:
1.	Installation process
2.	Software dependencies
3.	Latest releases
4.	API references

# Build and Test
TODO: Describe and show how to build your code and run the tests. 

# Contribute
TODO: Explain how other users and developers can contribute to make your code better. 

If you want to learn more about creating good readme files then refer the following [guidelines](https://docs.microsoft.com/en-us/azure/devops/repos/git/create-a-readme?view=azure-devops). You can also seek inspiration from the below readme files:
- [ASP.NET Core](https://github.com/aspnet/Home)
- [Visual Studio Code](https://github.com/Microsoft/vscode)
- [Chakra Core](https://github.com/Microsoft/ChakraCore)
>>>>>>> 271995c8c52d2c3e636bad95c63ecb94b44273d1
