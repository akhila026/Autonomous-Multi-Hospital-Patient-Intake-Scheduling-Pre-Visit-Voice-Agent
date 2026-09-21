import os
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import engine, Base
from backend.seed import seed_database
from backend.routers import (
    auth, hospitals, doctors, patients, ai, appointments, mock_ehr, questionnaires, reminders, admin, scheduling, voice, workflows, dashboard, analytics
)
from backend.workflows.handlers import register_event_handlers

# Initialize database schema
Base.metadata.create_all(bind=engine)

# Auto seed if newly created
seed_database()

# Register core domain event handlers & observers
register_event_handlers()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Resilient Healthcare AI Platform Prototype with Voice Triage and Idempotent Mock EHR Synchronization"
)

# Add correlation ID middleware for audit and operation tracing
from backend.audit.correlation import CorrelationIdMiddleware
app.add_middleware(CorrelationIdMiddleware)

# CORS configuration
allowed_origins_raw = getattr(settings, "ALLOWED_ORIGINS", "*")
if not allowed_origins_raw or allowed_origins_raw.strip() == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(hospitals.router, prefix=settings.API_V1_STR)
app.include_router(doctors.router, prefix=settings.API_V1_STR)
app.include_router(patients.router, prefix=settings.API_V1_STR)
app.include_router(ai.router, prefix=settings.API_V1_STR)
app.include_router(appointments.router, prefix=settings.API_V1_STR)
app.include_router(scheduling.router, prefix=settings.API_V1_STR)
app.include_router(mock_ehr.router, prefix=settings.API_V1_STR)
app.include_router(mock_ehr.router, prefix="/api")
app.include_router(mock_ehr.router, prefix="/api/v1")
app.include_router(mock_ehr.router)
app.include_router(questionnaires.router, prefix=settings.API_V1_STR)
app.include_router(reminders.router, prefix=settings.API_V1_STR)
app.include_router(admin.router, prefix=settings.API_V1_STR)
app.include_router(voice.router, prefix=settings.API_V1_STR)
app.include_router(voice.router, prefix="/api/v1")
app.include_router(workflows.router, prefix=settings.API_V1_STR)
app.include_router(workflows.router, prefix="/api")
app.include_router(dashboard.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix="/api")
app.include_router(analytics.router, prefix=settings.API_V1_STR)
app.include_router(analytics.router, prefix="/api")

# Also mount direct WebSocket alias at /ws/voice/{session_id}
@app.websocket("/ws/voice/{session_id}")
async def ws_voice_alias(websocket: WebSocket, session_id: str):
    from backend.routers.voice import voice_websocket_endpoint
    await voice_websocket_endpoint(websocket, session_id)

# Static files for frontend
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

if os.path.exists(frontend_dir):
    css_dir = os.path.join(frontend_dir, "css")
    js_dir = os.path.join(frontend_dir, "js")
    if os.path.exists(css_dir):
        app.mount("/css", StaticFiles(directory=css_dir), name="css")
    if os.path.exists(js_dir):
        app.mount("/js", StaticFiles(directory=js_dir), name="js")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION
    }
