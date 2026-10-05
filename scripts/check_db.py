import asyncio
from app.db.database import async_session
from app.models.db_models import ClassificationRecord
from sqlalchemy import select

async def main():
    async with async_session() as session:
        res = await session.execute(select(ClassificationRecord).order_by(ClassificationRecord.created_at.desc()).limit(1))
        rec = res.scalar()
        if rec:
            print(f'Rule: {rec.retention_rule}, Code: {rec.retention_code}, Label: {rec.retention_label}')
        else:
            print("No records found.")
asyncio.run(main())
