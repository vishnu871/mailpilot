from fastapi import FastAPI
from sqlalchemy import text

from app.api.employees import router as employees_router
from app.api.meetings import router as meetings_router
from app.api.realtime import router as realtime_router
from app.core.config import settings
from app.db.database import engine

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Backend API for employee activity, "
        "task management, messaging, "
        "meetings, and real-time communication."
    ),
)

# API routers
app.include_router(employees_router)
app.include_router(realtime_router)
app.include_router(meetings_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "employee-task-management-portal",
        "version": "0.1.0",
    }


@app.get("/health/database")
def database_health_check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "status": "ok",
        "database": "connected",
    }