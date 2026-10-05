# Document Retention POC - Code Review Guide

This guide is designed to help you explain the architecture, data flow, and key design decisions of the Document Retention POC during a code review. It provides a clear, high-level map of how the code moves from document upload to final classification and synchronization.

## 1. High-Level Architecture
The system is an asynchronous background processor built on **FastAPI**. It does not rely on manual HTTP triggers (Swagger); instead, it polls SharePoint automatically, processes documents using Azure AI services, logs state to PostgreSQL, and syncs the results back to SharePoint structures.

### Key Technologies
* **Framework:** FastAPI (Python)
* **Concurrency:** `asyncio` (for polling and non-blocking API calls)
* **Database:** PostgreSQL (via SQLAlchemy)
* **AI/Search:** Azure Document Intelligence, Azure OpenAI, Azure AI Search
* **Integration:** Microsoft Graph API

---

## 2. Step-by-Step Execution Flow

When explaining the code, walk the reviewers through this exact sequence:

### Phase 1: The Trigger (`app/main.py` & `app/sync/scheduler.py`)
1. **Application Startup:** `app/main.py` uses a FastAPI `lifespan` context manager to start the background scheduler when the server boots.
2. **Polling Loop:** `app/sync/scheduler.py` runs an infinite loop that queries the `POC_Source_Documents` SharePoint Document Library.
3. **Idempotency & Skip Logic:** For each file found, it checks the local PostgreSQL database (via `ClassificationRecord`):
   * If it doesn't exist $\rightarrow$ **Process as New**
   * If it exists but failed previously $\rightarrow$ **Retry/Resume**
   * If it exists and hasn't changed $\rightarrow$ **Skip**

### Phase 2: The AI Processing Pipeline (`app/services/`)
Once a document is selected for processing, it runs through a strict, sequential AI pipeline:

1. **Download:** The file binary is downloaded from SharePoint.
2. **OCR & Extraction (`document_intelligence_service.py`):** The file is sent to Azure Document Intelligence to extract raw text, layout, and create a normalized `DocumentProfile`.
3. **Understanding (`document_understanding_service.py`):** The extracted text is passed to Azure OpenAI to comprehend the semantic meaning, intent, and context of the document.
4. **Classification (`classification_service.py` & `metadata_service.py`):** 
   * **Search:** The system queries **Azure AI Search** to retrieve the top 5 closest matches from the approved Retention Taxonomy (`data/retention_taxonomy.json`).
   * **Selection:** **Azure OpenAI** evaluates the top 5 candidates against the document's understanding profile and selects the single most accurate retention code.
5. **Validation (`validation_service.py`):** The final AI selection is strictly validated against the internal taxonomy rules to ensure compliance.

### Phase 3: Synchronization & Logging (`app/sync/sharepoint_sync.py`)
After a successful classification, the system must push the decisions back to SharePoint. This happens in a strict, 4-step sequence to ensure data integrity:

1. **Apply Purview Label:** Calls the dedicated Graph API `/retentionLabel` endpoint to lock the file as a record in Purview.
2. **Update Document Metadata:** Patches custom metadata columns (like Category and Team Owner) directly on the source document.
3. **Update Retention List:** Creates or updates a master record row in the `POC_Documents_Retention` SharePoint List for Power Apps consumption.
4. **Audit Log:** Appends a strictly additive row to the `POC_classification_auditlog` SharePoint List for compliance tracking.

---

## 3. Key Design Decisions to Highlight in Review

Reviewers love to hear *why* you built things a certain way. Mention these points to sound like a senior engineer:

* **No APScheduler Dependency:** We intentionally used `asyncio` tasks paired with FastAPI's `lifespan` for the polling scheduler. This keeps the dependency tree minimal and leverages built-in Python concurrency.
* **Resilience via Partial Failures:** The sync process (`sharepoint_sync.py`) logs every single step (1 through 4) to the database independently. If the Graph API throttles the system on Step 3, the scheduler knows to resume from Step 3 on the next loop, rather than re-running the expensive AI classification.
* **Separation of Concerns:** The code strictly separates AI Logic (`app/services/`), State Management (`app/db/`), and Integration (`app/sync/`).
* **Built-in Backoff Strategies:** We wrapped all external API calls (Graph, OpenAI, AI Search) in an exponential backoff wrapper (`_with_backoff`) to gracefully handle 429 Rate Limits and 503 Service Unavailable errors.

## 4. Suggested Folder Walkthrough for the Review
If you share your screen, open folders in this order:
1. `app/main.py` (Show the entry point)
2. `app/sync/scheduler.py` (Show the polling and skip logic)
3. `app/services/classification_service.py` (Show the AI decision orchestration)
4. `app/sync/sharepoint_sync.py` (Show how it writes safely back to Microsoft 365)
