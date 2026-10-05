import asyncio
import re
from sqlalchemy import select
from app.db.database import get_db, async_session
from app.models.db_models import ClassificationRecord

def _parse_retention_rule(rule: str) -> tuple:
    rule = (rule or "").strip().upper()
    
    month_match = re.search(r"(\d+)\s*MONTHS?", rule)
    if month_match:
        return ("TIME", int(month_match.group(1)), "months")
    
    match = re.match(r"^([A-Z]+)[-+]?(\d+)?$", rule)
    if match:
        code = match.group(1)
        if code == "YE":
            code = "CE"
        period = int(match.group(2)) if match.group(2) else None
        unit = "years" if period else None
        return (code, period, unit)
        
    return (rule, None, None)

async def main():
    print("Starting classification record backfill...")
    async with async_session() as session:
        result = await session.execute(select(ClassificationRecord))
        records = result.scalars().all()
        
        updated_count = 0
        for record in records:
            if record.retention_code is None and record.retention_rule:
                code, period, unit = _parse_retention_rule(record.retention_rule)
                record.retention_code = code
                # period in DB model is string now (we didn't cast to int to avoid sqlite issues), but we treat it as string or int
                record.retention_period = str(period) if period is not None else None
                record.retention_period_unit = unit
                updated_count += 1
                
        if updated_count > 0:
            await session.commit()
            
        print(f"Backfill complete! Updated {updated_count} historical records.")

if __name__ == "__main__":
    asyncio.run(main())
