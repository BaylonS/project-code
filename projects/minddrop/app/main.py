from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import db
from .llm import enrich_chunk


BASE_DIR = Path(__file__).resolve().parent
APP_NAME = os.getenv("APP_NAME", "MindDrop")

app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("APP_SECRET", "dev-only-change-me"))
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class AuthPayload(BaseModel):
    email: str
    password: str
    display_name: str = ""
    invite_code: str = ""


class ChunkPayload(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    source_url: str = ""
    source_type: str = "manual"
    visibility: str = "private"
    tags: list[str] = []


class IntakePayload(ChunkPayload):
    user_email: str = ""


class ChunkPatchPayload(BaseModel):
    pinned: bool | None = None
    col_span: int | None = Field(default=None, ge=1, le=4)
    height_px: int | None = Field(default=None, ge=0, le=1200)


class ChecklistPayload(BaseModel):
    line: int = Field(ge=0)
    checked: bool


class ReorderPayload(BaseModel):
    ids: list[int]


class TagColorPayload(BaseModel):
    color: str


@app.on_event("startup")
def startup() -> None:
    db.init_db()


def row_user(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
    }


def current_user(request: Request) -> dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not signed in")
    with db.db() as conn:
        user = db.get_user_by_id(conn, int(user_id))
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in")
    return row_user(user)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request, "app_name": APP_NAME})


@app.get("/webhook-helper", response_class=HTMLResponse)
def webhook_helper(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("webhook_helper.html", {"request": request, "app_name": APP_NAME})


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


@app.post("/api/signup")
def signup(payload: AuthPayload, request: Request) -> dict[str, Any]:
    allow_signup = os.getenv("ALLOW_SIGNUP", "true").lower() == "true"
    invite_code = os.getenv("INVITE_CODE", "")
    if not allow_signup:
        raise HTTPException(status_code=403, detail="Signup is disabled")
    if invite_code and payload.invite_code != invite_code:
        raise HTTPException(status_code=403, detail="Invite code is wrong")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    with db.db() as conn:
        if db.get_user_by_email(conn, payload.email):
            raise HTTPException(status_code=409, detail="Email already exists")
        user = db.create_user(conn, payload.email, payload.password, payload.display_name)
    request.session["user_id"] = user["id"]
    return {"user": row_user(user)}


@app.post("/api/login")
def login(payload: AuthPayload, request: Request) -> dict[str, Any]:
    with db.db() as conn:
        user = db.get_user_by_email(conn, payload.email)
        if not user or not db.verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Email or password is wrong")
    request.session["user_id"] = user["id"]
    return {"user": row_user(user)}


@app.post("/api/logout")
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/chunks")
def chunks(
    q: str = "",
    tag: str = "",
    scope: str = "all",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    with db.db() as conn:
        items = db.list_chunks(conn, user["id"], q=q.strip(), tag=tag.strip(), scope=scope)
    return {"chunks": items}


@app.post("/api/chunks")
async def create_chunk(payload: ChunkPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    enriched = await enrich_chunk(payload.body, payload.source_url)
    tags = list(payload.tags or []) + list(enriched.get("tags", []) or [])
    with db.db() as conn:
        item = db.create_chunk(
            conn,
            user_id=user["id"],
            body=payload.body,
            title=str(enriched.get("title") or "Untitled"),
            summary=str(enriched.get("summary") or ""),
            tags=tags,
            source_url=payload.source_url,
            source_type=payload.source_type,
            visibility=payload.visibility,
        )
    return {"chunk": item}


@app.get("/api/tags")
def tags(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db.db() as conn:
        values = db.all_tags(conn, user["id"])
    return {"tags": values}


@app.get("/api/tag-colors")
def tag_colors(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db.db() as conn:
        colors = db.tag_colors(conn, user["id"])
    return {"colors": colors}


@app.patch("/api/tag-colors/{tag}")
def set_tag_color(tag: str, payload: TagColorPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    try:
        with db.db() as conn:
            db.set_tag_color(conn, user["id"], tag, payload.color)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True}


@app.get("/api/random")
def random_item(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db.db() as conn:
        item = db.random_chunk(conn, user["id"])
    return {"chunk": item}


@app.delete("/api/chunks/{chunk_id}")
def delete_chunk(chunk_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    with db.db() as conn:
        deleted = db.delete_chunk(conn, user["id"], chunk_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"ok": True}


@app.patch("/api/chunks/{chunk_id}")
def patch_chunk(chunk_id: int, payload: ChunkPatchPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db.db() as conn:
        item = db.update_chunk_flags(
            conn,
            user["id"],
            chunk_id,
            pinned=payload.pinned,
            col_span=payload.col_span,
            height_px=payload.height_px,
        )
    if not item:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"chunk": item}


@app.patch("/api/chunks/{chunk_id}/checklist")
def patch_checklist(chunk_id: int, payload: ChecklistPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with db.db() as conn:
        item = db.update_checklist(conn, user["id"], chunk_id, payload.line, payload.checked)
    if not item:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"chunk": item}


@app.post("/api/chunks/reorder")
def reorder_chunks(payload: ReorderPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    with db.db() as conn:
        db.reorder_chunks(conn, user["id"], payload.ids[:200])
    return {"ok": True}


@app.post("/api/intake")
async def intake(payload: IntakePayload, authorization: str = Header(default="")) -> JSONResponse:
    expected = os.getenv("INTAKE_TOKEN", "")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Bad intake token")
    with db.db() as conn:
        user = db.get_user_by_email(conn, payload.user_email) if payload.user_email else None
        if not user:
            user = conn.execute("SELECT * FROM users ORDER BY is_admin DESC, id ASC LIMIT 1").fetchone()
        if not user:
            raise HTTPException(status_code=400, detail="Create a user before using intake")
    enriched = await enrich_chunk(payload.body, payload.source_url)
    tags = list(payload.tags or []) + list(enriched.get("tags", []) or [])
    with db.db() as conn:
        item = db.create_chunk(
            conn,
            user_id=user["id"],
            body=payload.body,
            title=str(enriched.get("title") or "Untitled"),
            summary=str(enriched.get("summary") or ""),
            tags=tags,
            source_url=payload.source_url,
            source_type=payload.source_type or "api",
            visibility=payload.visibility,
        )
    return JSONResponse({"chunk": item}, status_code=201)
