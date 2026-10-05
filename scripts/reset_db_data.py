"""
Reset all classification/document data in Azure PostgreSQL.
Keeps the schema intact (tables, columns, indexes) — only clears the rows.
Preserves alembic_version so migrations stay consistent.

Run with:
    venv\Scripts\python.exe scripts\reset_db_data.py
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

TABLES_TO_TRUNCATE = [
    "sharepoint_sync_logs",   # child of classifications (FK cascade)
    "classifications",
    "documents",
    "audit_logs",
    "review_records",
    "processing_history",
]

async def reset():
    raw = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "").replace("postgresql://", "")
    base = raw.split("?")[0]
    conn = await asyncpg.connect(dsn=f"postgresql://{base}", ssl="require")

    print("Truncating tables (CASCADE)...")
    for table in TABLES_TO_TRUNCATE:
        try:
            await conn.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
            print(f"  ✓ {table}")
        except asyncpg.UndefinedTableError:
            print(f"  - {table} (not found, skipping)")
        except Exception as e:
            print(f"  ✗ {table}: {e}")

    # Verify
    for table in TABLES_TO_TRUNCATE:
        try:
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
            print(f"  {table}: {count} rows")
        except Exception:
            pass

    print("\nDone. Database is clean.")
    await conn.close()

asyncio.run(reset())
