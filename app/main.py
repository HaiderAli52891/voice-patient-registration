"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.dashboard import router as dashboard_router
from app.api.patients import calls_router, router as patients_router
from app.config import settings
from app.database import init_db
from app.logging_config import configure_logging
from app.voice.twilio_routes import router as twilio_router
from app.voice.vapi_routes import router as vapi_router

configure_logging()
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    log.info(
        "startup env=%s db=%s base_url=%s",
        settings.environment,
        settings.database_url.split("@")[-1],  # never log credentials
        settings.public_base_url,
    )
    yield
    log.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Voice AI patient registration: Twilio telephony, an LLM intake agent, "
        "a persistent database, and a REST API over the same service layer."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # read-only demo API; tighten for real deployments
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients_router)
app.include_router(calls_router)
app.include_router(twilio_router)
app.include_router(vapi_router)
app.include_router(dashboard_router)


# --- Uniform error envelope ---------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    """422 with field-level messages in the standard envelope."""
    fields: dict[str, str] = {}
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query")]
        key = ".".join(loc) or "body"
        msg = err.get("msg", "Invalid value")
        # Our domain validators raise ValueError(dict) — unpack that dict so
        # the client sees one message per field.
        ctx_error = err.get("ctx", {}).get("error")
        if isinstance(ctx_error, dict):
            fields.update({k: str(v) for k, v in ctx_error.items()})
        elif hasattr(ctx_error, "args") and ctx_error and isinstance(
            getattr(ctx_error, "args", [None])[0], dict
        ):
            fields.update({k: str(v) for k, v in ctx_error.args[0].items()})
        else:
            fields[key] = msg.replace("Value error, ", "")
    return JSONResponse(
        status_code=422,
        content={"data": None, "error": {"code": "validation_error", "fields": fields}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    # The dashboard is HTML; don't hand it a JSON body for a 401 challenge.
    if request.url.path.startswith("/dashboard") and exc.status_code == 401:
        return JSONResponse(
            status_code=401,
            content={"data": None, "error": "Not authorised"},
            headers=exc.headers or {},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("unhandled path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"data": None, "error": "Internal server error"},
    )


# --- Meta ----------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@app.get("/health", tags=["meta"], summary="Liveness and database check")
def health():
    from sqlalchemy import text

    from app.database import SessionLocal

    db_ok = True
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        log.exception("health.db_check_failed")
        db_ok = False

    return {
        "data": {
            "status": "ok" if db_ok else "degraded",
            "database": "up" if db_ok else "down",
            "llm_configured": bool(settings.openai_api_key),
            "telephony_configured": bool(settings.twilio_phone_number),
        },
        "error": None,
    }
