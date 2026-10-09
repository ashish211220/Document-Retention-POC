import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import sharepoint
from app.services.metadata_service import load_taxonomy

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from app.db.database import engine, Base
from app.models.db_models import *  # Ensure models are loaded before create_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: loading retention taxonomy...")
    load_taxonomy()

    if engine is not None:
        logging.info("Initializing database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logging.info("Database tables initialized.")
    else:
        logging.warning("Database engine not configured, skipping table creation.")

    from app.sync.scheduler import start_scheduler

    start_scheduler()

    yield

    from app.sync.scheduler import stop_scheduler

    await stop_scheduler()
    logging.info("Application shutdown.")


app = FastAPI(
    title="Document Retention AI POC",
    version="1.0.0",
    description="Proof of Concept for AI-powered Document Classification and Retention Management System",
    lifespan=lifespan,
)

app.include_router(sharepoint.router, prefix="/api/sharepoint", tags=["SharePoint"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/health", tags=["Health"])
async def health_check():
    from app.services.metadata_service import load_taxonomy

    taxonomy = load_taxonomy()
    return {"status": "healthy", "retention_taxonomy_records": taxonomy.total_count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
