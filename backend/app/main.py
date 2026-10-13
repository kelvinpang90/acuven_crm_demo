from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.middleware.logging import AccessLogMiddleware
from app.routers import (
    analytics,
    auth,
    autocount,
    contacts,
    dashboard,
    deals,
    internal_chatbot,
    internal_whatsapp,
    messages,
    pipeline,
    projects,
    routing,
    sales_targets,
    tasks,
    users,
    webhooks,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    # Start email poller
    # Email poller is disabled; the import stays so re-enabling is just uncommenting the two calls
    # start_email_poller()
    from app.tasks.autocount_poller import start_autocount_poller, stop_autocount_poller
    from app.tasks.email_poller import start_email_poller, stop_email_poller  # noqa: F401

    start_autocount_poller()
    yield
    # Shutdown
    # stop_email_poller()
    stop_autocount_poller()
    await engine.dispose()


app = FastAPI(title="CRM API", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Access logging
app.add_middleware(AccessLogMiddleware)

# Routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(contacts.router, prefix="/api/contacts", tags=["contacts"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(routing.router, prefix="/api/routing/rules", tags=["routing"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["Webhooks"])
app.include_router(messages.router, prefix="/api/messages", tags=["messages"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(sales_targets.router, prefix="/api/sales-targets", tags=["sales_targets"])
app.include_router(deals.router, prefix="/api/deals", tags=["deals"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(autocount.router, prefix="/api/autocount", tags=["autocount"])
# No /api prefix: path must match whatsapp_gateway's demos_registry internal_base_url exactly.
app.include_router(internal_whatsapp.router, prefix="/internal/whatsapp", tags=["internal"])
# No /api prefix either: the path ai_chatbot_demo pushes to (docs/chatbot-events.md section 4).
app.include_router(internal_chatbot.router, prefix="/internal/chatbot", tags=["internal"])


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "CRM API", "git_sha": settings.git_sha}
