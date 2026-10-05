import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def check():
    raw = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "").replace("postgresql://", "")
    base = raw.split("?")[0]
    conn = await asyncpg.connect(dsn=f"postgresql://{base}", ssl="require")
    
    cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='classifications' ORDER BY ordinal_position"
    )
    print("ALL classification columns:")
    for r in cols:
        print(f"  {r['column_name']:40s} {r['data_type']}")
    
    print()
    
    sync_cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='sharepoint_sync_logs' ORDER BY ordinal_position"
    )
    print("sharepoint_sync_logs columns:")
    for r in sync_cols:
        print(f"  {r['column_name']:40s} {r['data_type']}")
    
    v = await conn.fetch("SELECT version_num FROM alembic_version")
    print(f"\nAlembic head: {[r['version_num'] for r in v]}")
    await conn.close()

asyncio.run(check())
