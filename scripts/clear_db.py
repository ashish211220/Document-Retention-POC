import asyncio
import sys
import os

# Add the parent directory to the path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine
from app.config import DATABASE_URL
from app.models.db_models import Base

async def clear_database():
    url = DATABASE_URL
    if url and url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        
    if not url:
        print("DATABASE_URL is not set.")
        return

    print("Connecting to database...")
    engine = create_async_engine(url, echo=False)
    
    async with engine.begin() as conn:
        print("Dropping all tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Recreating all tables...")
        await conn.run_sync(Base.metadata.create_all)
        
    print("Database cleared successfully!")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(clear_database())
