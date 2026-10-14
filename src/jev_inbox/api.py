"""Read-only, bearer-authenticated integration surface for Grok Bot."""

import secrets
import sqlite3
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .store import read_database

Status = Literal["applied", "assessment", "interview", "offer", "rejected", "update"]


class Application(BaseModel):
    id: str
    subject: str
    status: Status
    first_received_ms: int
    last_received_ms: int
    last_message_id: str
    message_count: int


class ApplicationPage(BaseModel):
    items: list[Application]
    limit: int
    offset: int


class Summary(BaseModel):
    total: int
    by_status: dict[str, int]
    last_sync: str | None


def create_app(db_path, api_token):
    if len(api_token) < 32 or not api_token.isascii():
        raise ValueError("JEV_API_TOKEN must be at least 32 ASCII characters.")
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if not credentials or not secrets.compare_digest(
            credentials.credentials.encode("utf-8"), api_token.encode("ascii")
        ):
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )

    app = FastAPI(
        title="JEV Inbox",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(authorize)],
    )

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(sqlite3.Error)
    async def database_unavailable(request, error):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable; run sync."})

    @app.get("/health")
    def health():
        with read_database(db_path) as db:
            db.execute("SELECT 1 FROM state LIMIT 1").fetchone()
        return {"status": "ok"}

    @app.get("/v1/summary", response_model=Summary)
    def summary():
        with read_database(db_path) as db:
            # One read snapshot for internally consistent counts and sync timestamp.
            db.execute("BEGIN")
            counts = dict(db.execute("SELECT status, COUNT(*) FROM applications GROUP BY status"))
            last = db.execute("SELECT value FROM state WHERE key='last_sync'").fetchone()
        return {
            "total": sum(counts.values()),
            "by_status": counts,
            "last_sync": last[0] if last else None,
        }

    @app.get("/v1/applications", response_model=ApplicationPage)
    def applications(
        status: Status | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    ):
        with read_database(db_path) as db:
            rows = db.execute(
                "SELECT * FROM applications WHERE (? IS NULL OR status=?) "
                "ORDER BY last_received_ms DESC, id DESC LIMIT ? OFFSET ?",
                (status, status, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}

    @app.get("/v1/applications/{application_id}", response_model=Application)
    def application(application_id: str):
        with read_database(db_path) as db:
            row = db.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Application not found")
        return dict(row)

    @app.get("/openapi.json", include_in_schema=False)
    def schema():
        return app.openapi()

    return app
