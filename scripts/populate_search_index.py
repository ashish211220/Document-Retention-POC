"""
One-time script to populate the Azure AI Search index with all 327 retention taxonomy records.

Usage:
    python scripts/populate_search_index.py

Run this once after provisioning Azure AI Search, and again any time
the retention_taxonomy.json is updated.
"""
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from app.azure.search import create_index_if_not_exists, upload_taxonomy_documents, get_index_stats
from app.utils.logger import get_logger

logger = get_logger("populate_search_index")

TAXONOMY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "retention_taxonomy.json")


def main():
    logger.info("=== Azure AI Search Index Population ===")

    # 1. Ensure index exists
    logger.info("Step 1: Verifying / creating search index...")
    created = create_index_if_not_exists()
    if created:
        logger.info("New index created.")
    else:
        logger.info("Using existing index.")

    # 2. Load taxonomy records
    logger.info("Step 2: Loading retention taxonomy JSON...")
    if not os.path.exists(TAXONOMY_PATH):
        logger.error(f"Taxonomy file not found: {TAXONOMY_PATH}")
        sys.exit(1)

    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)
    logger.info(f"Loaded {len(records)} records from taxonomy JSON.")

    # 3. Upload to Azure Search
    logger.info("Step 3: Uploading documents to Azure AI Search...")
    total = upload_taxonomy_documents(records)
    logger.info(f"Upload complete. {total}/{len(records)} documents uploaded successfully.")

    # 4. Verify stats
    logger.info("Step 4: Verifying index statistics...")
    stats = get_index_stats()
    logger.info(f"Index stats: {stats['document_count']} documents | {stats['storage_size_bytes']} bytes")

    logger.info("=== Population Complete ===")


if __name__ == "__main__":
    main()
