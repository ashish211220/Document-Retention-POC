# AI Context - Document Retention POC

## 📌 Project Overview
An AI-powered Document Retention and Classification Proof of Concept (POC) designed to automate corporate document retention policies using Azure AI and a human-in-the-loop review dashboard.

## 🛠 Tech Stack
- **Backend:** FastAPI (Python)
- **Database:** PostgreSQL (Azure DB) with Alembic migrations
- **AI Services:** Azure Document Intelligence (OCR), Azure OpenAI (Classification/Understanding), Azure AI Search (Taxonomy Lookup)
- **Frontend:** Vanilla HTML/JS (`index.html`) served statically via FastAPI

## 🏗 System Architecture & Data Flow
1. **Upload:** PDF uploaded via Swagger UI (`/api/documents/upload`).
2. **Analysis:** Text extracted by Document Intelligence, analyzed by Azure OpenAI.
3. **Classification:** AI Search matches document to corporate taxonomy retention rule.
4. **Storage:** Metadata (rule, confidence, status) saved to PostgreSQL (Physical PDF is discarded in this POC).
5. **Human Review:** Low confidence or sensitive documents are flagged for review in the web dashboard.

## ✅ Current Project State (Completed Tasks)
- **Taxonomy & Ingestion:** Taxonomy ingestion from JSON is complete. Document upload pipeline works.
- **Database Schema:** `DocumentRecord` and `ClassificationRecord` are fully implemented. Recently added `retention_rule` and `legal_hold` columns via Alembic migration `a1b2c3d4e5f6`.
- **SharePoint Integration:** Active. The system authenticates via MSAL Device Code Flow, downloads document contents via Graph API (`driveItem/content`), and actively applies generated Purview labels back to SharePoint (`POST /retentionLabel`).
- **Human Review Dashboard:** Fully functional at `/static/index.html`.
  - **Features:** Live KPI stats, Case-insensitive search filters (Team Owner, Rule).
  - **Actions:** Retain, Approve Deletion, Reclassify, and Apply/Remove Legal Hold.
  - **Audit:** All actions write to an audit log.
- **Testing:** API tests (`tests/test_review_api.py`) are fully passing using `aiosqlite` in-memory DB.
- **Background Auto-Polling Scheduler:** Fully implemented at `app/sync/scheduler.py`. FastAPI lifespan starts it on startup; it polls `POC_Source_Documents` every `SYNC_POLL_INTERVAL_MINUTES` (default 5) automatically with no Swagger interaction needed.
  - **Skip-Check:** Before processing any document, checks DB for an existing `ClassificationRecord` by `sharepoint_item_id`. Compares `sharepoint_last_modified` with Graph API `lastModifiedDateTime` — unchanged files are silently skipped.
  - **Reclassification:** Modified files are reprocessed in-place (PATCH existing SharePoint list row, no duplicates).
  - **Restart Recovery:** Records stuck in `processing_status='processing'` (from a server crash) are automatically resumed; the existing sync module's idempotency guards skip already-completed steps.
  - **Concurrency Control:** `asyncio.Semaphore(SYNC_BATCH_CONCURRENCY)` limits parallel Azure API calls. `asyncio.Lock()` prevents overlapping poll cycles.
  - **Retry / Max Retries:** Failed documents are retried each poll cycle up to `MAX_PROCESSING_RETRIES` (default 3) then marked `failed_permanent` for human review.
  - **Source-Deleted Guard:** A 404 mid-download marks the record `processing_status='source_deleted'` and stops retrying.
  - **Duplicate Safety Net:** Before creating a new SharePoint list row, queries for an existing row with matching `DocumentID` to prevent race-condition duplicates.
  - **Rate-Limit Backoff:** All external calls go through `_with_backoff()` — exponential backoff starting at 2 s, doubling up to 60 s, capped per document so one throttled file doesn't stall the batch.

## 🔗 Microsoft Purview & SharePoint Integration Details
- **Big Bucket Strategy:** The system uses the "Big Bucket" labeling strategy. Instead of a unique Purview label for every document title (260+), labels are strictly mapped to the **retention rule** (e.g., `FE-4`, `AC-5`, `PM`). This reduced the required Microsoft Purview labels to exactly **42**. Broken/missing rules in the original Excel file are automatically skipped during JSON generation.
- **Label Application:** Once the AI pipeline classifies a SharePoint document, it calls the Microsoft Graph API (`sharepoint_service.update_metadata_and_label`) to stamp the literal rule name as the Purview `retentionLabel` on the file.
- **Disposition Review Configuration:** Labels created in Purview must be set to **"Trigger a disposition review"** upon expiration. This ensures documents are not auto-deleted without human-in-the-loop authorization.
- **Disposition Management Strategy:** When retention periods expire, the review can be handled in two ways:
  1. **Built-in:** Compliance officers review and approve deletions in the native Microsoft Purview Dispositions dashboard.
  2. **Custom API (Future Enhancement):** The app can query pending dispositions using the `RecordsManagement.ReadWrite.All` Graph API permission and surface them directly inside our custom Human Review dashboard.

## 🚀 Next Steps / Future Production Upgrades
- Build automated cleanup cron jobs for documents marked as `approved_deletion`.
- Implement persistent Blob Storage for PDFs (Azure Blob/S3) instead of discarding them.
- Add deduplication logic (SHA-256 hash checks on upload).
- Implement background tasks (Celery/Message Queue) for AI processing.
- Add authentication/authorization (Azure AD).
