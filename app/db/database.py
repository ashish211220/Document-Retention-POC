from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

url = DATABASE_URL
if url and url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = None
async_session = None

if url:
    engine = create_async_engine(url, echo=False)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
else:
    logger.warning(
        "DATABASE_URL is not set. Database integration will not be available."
    )

Base = declarative_base()


async def get_db():
    if async_session is None:
        yield None
        return
    async with async_session() as session:
        yield session
