import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.api import documents
from app.api import sharepoint
from app.services.metadata_service import load_taxonomy

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

from app.db.database import engine, Base
from app.models.db_models import *  # Ensure models are loaded before create_all

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load retention taxonomy knowledge base into memory
    logging.info("Application startup: l    oading retention taxonomy...")
    load_taxonomy()
    
    # Initialize database tables
    if engine is not None:
        logging.info("Initializing database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logging.info("Database tables initialized.")
    else:
        logging.warning("Database engine not configured, skipping table creation.")

    # Start automatic background polling scheduler
    from app.sync.scheduler import start_scheduler
    start_scheduler()
        
    yield

    # Shutdown: gracefully stop the scheduler (let current document finish)
    from app.sync.scheduler import stop_scheduler
    await stop_scheduler()
    logging.info("Application shutdown.")


app = FastAPI(
    title="Document Retention AI POC",
    version="1.0.0",
    description="Proof of Concept for AI-powered Document Classification and Retention Management System",
    lifespan=lifespan
)

# Include routers
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(sharepoint.router, prefix="/api/sharepoint", tags=["SharePoint"])

# CORS — allow all origins for this POC (restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve Human Review UI
app.mount("/static", StaticFiles(directory="app/static", html=True), name="static")

# Convenience redirect: / → Review UI
@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/static/index.html")

@app.get("/health", tags=["Health"])
async def health_check():
    from app.services.metadata_service import get_taxonomy
    taxonomy = get_taxonomy()
    return {
        "status": "healthy",
        "retention_taxonomy_records": taxonomy.total_count
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
