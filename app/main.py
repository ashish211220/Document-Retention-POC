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



@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application startup: validating configuration...")
    from app.config import validate_config

    validate_config()

    logging.info("Application startup: loading retention taxonomy...")
    load_taxonomy()

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

# CORS: Only enabled when specific origins are configured; wildcard with credentials disabled
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if CORS_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )



@app.get("/health", tags=["Health"])
async def health_check():
    from app.services.metadata_service import get_taxonomy

    taxonomy = get_taxonomy()
    return {"status": "healthy", "retention_taxonomy_records": taxonomy.total_count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
