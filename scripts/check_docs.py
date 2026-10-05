import asyncio
from app.db.database import async_session
from sqlalchemy import select
from app.models.db_models import DocumentRecord, ClassificationRecord

async def main():
    async with async_session() as session:
        stmt = select(DocumentRecord, ClassificationRecord).outerjoin(ClassificationRecord, DocumentRecord.id == ClassificationRecord.document_id).order_by(DocumentRecord.created_at.desc()).limit(5)
        res = await session.execute(stmt)
        for doc, cls in res:
            print(f"Doc: {doc.name}")
            print(f"  status: {cls.status if cls else None}")
            print(f"  retention_rule: {cls.retention_rule if cls else None}")
            print(f"  retention_code: {cls.retention_code if cls else None}")
            print(f"  confidence: {cls.confidence_score if cls else None}")
            print("---")

if __name__ == '__main__':
    asyncio.run(main())
