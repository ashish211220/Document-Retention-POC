import os
import sys
import json
import logging

# Ensure app path is in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.azure.search import create_index_if_not_exists, delete_index, upload_taxonomy_documents
from app.services.metadata_service import TAXONOMY_PATH
from app.utils.logger import get_logger

logger = get_logger(__name__)

def run_reindex():
    logger.info("Starting taxonomy re-indexing process...")

    # We no longer delete the index automatically to preserve versioning.
    # The active index is controlled by AZURE_SEARCH_INDEX_NAME in config.py

    logger.info("Creating new index with VectorSearch support...")
    create_index_if_not_exists()

    logger.info(f"Loading taxonomy from {TAXONOMY_PATH}")
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    logger.info(f"Found {len(records)} records to process.")
    
    # Upload and generate embeddings
    logger.info("Uploading documents and generating embeddings... This may take a few minutes depending on OpenAI rate limits.")
    uploaded_count = upload_taxonomy_documents(records)

    logger.info(f"Re-indexing complete. Successfully processed and uploaded {uploaded_count} records.")

if __name__ == "__main__":
    run_reindex()
