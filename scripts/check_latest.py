import asyncio
from app.db.database import async_session
from app.models.db_models import DocumentRecord, ClassificationRecord
from sqlalchemy import select

async def main():
    async with async_session() as s:
        res = await s.execute(select(DocumentRecord).order_by(DocumentRecord.created_at.desc()).limit(1))
        doc = res.scalars().first()
        if not doc:
            print("No docs found")
            return
            
        print(f"Doc: {doc.document_id}")
        
        c_res = await s.execute(select(ClassificationRecord).where(ClassificationRecord.document_id == doc.id).order_by(ClassificationRecord.created_at.desc()))
        c = c_res.scalars().first()
        if c:
            print(f"  status: {c.classification_status}")
            print(f"  retention_rule: {c.retention_rule}")
            print(f"  confidence: {c.confidence_score}")
        else:
            print("  No classification record")

asyncio.run(main())
