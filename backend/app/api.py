"""REST surface.

/api/health              liveness
/api/users, /friendships, /messages, /teams
                         dashboard read API: live DB rows, never credentials
/api/events              SSE stream of everything the backend sends to bots
/api/dev/*               seed data and fake events, only with GROKNET_DEV=1
Mirrors of the MCP tools under /api live in app/rest.py (issue #6).
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlmodel import Session, or_, select

from app import config, dev
from app.auth import current_actor
from app.db import get_session
from app.events import bus
from app.models import Friendship, Message, User
from app.onboarding import ONBOARDING_GUIDE
from app.teams import list_teams as read_teams

router = APIRouter(prefix="/api")
SessionDep = Annotated[Session, Depends(get_session)]
# Reads are for signed-in humans (session cookie) or bots/scripts (bearer token). /health stays open.
reader = [Depends(current_actor)]


@router.get("/onboarding", response_class=PlainTextResponse)
def onboarding() -> str:
    """The bot onboarding guide as plain text, for a bot that fetches a Groknet link."""
    return ONBOARDING_GUIDE


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "groknet"}


@router.get("/users", dependencies=reader)
def list_users(session: SessionDep) -> list[dict]:
    # Never groknet_token, webhook_url or webhook_key.
    return [
        {
            "handle": u.handle,
            "display_name": u.display_name,
            "email": u.email,
            # Optional facts about the human (age, job, bio, location...). The column
            # lands with the register-profile work; until then it is always {}.
            "profile": getattr(u, "profile", None) or {},
            "created_at": u.created_at,
        }
        for u in session.exec(select(User).order_by(User.created_at)).all()
    ]


@router.get("/friendships", dependencies=reader)
def list_friendships(session: SessionDep) -> list[Friendship]:
    return list(session.exec(select(Friendship).order_by(Friendship.created_at)).all())


@router.get("/messages", dependencies=reader)
def list_messages(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    handle: str | None = None,
) -> list[Message]:
    stmt = select(Message)
    if handle:
        stmt = stmt.where(or_(Message.sender == handle, Message.recipient == handle))
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
    return list(session.exec(stmt).all())


@router.get("/teams", dependencies=reader)
def list_teams(session: SessionDep) -> list[dict]:
    return read_teams(session)


@router.get("/events", dependencies=reader)
async def events() -> StreamingResponse:
    q = bus.subscribe()

    async def stream() -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- dev only -----------------------------------------------------------------


def require_dev_mode() -> None:
    # Read at request time (not import time) so it can be toggled in tests.
    if not config.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")


dev_router = APIRouter(prefix="/dev", dependencies=[Depends(require_dev_mode)])


@dev_router.post("/seed")
def dev_seed(session: SessionDep) -> dict[str, int]:
    """Wipe the database and load the demo dataset (4 users, 3 friendships, 10 messages, 1 team)."""
    return dev.seed(session)


@dev_router.post("/emit")
def dev_emit(body: dict[str, Any]) -> dict[str, Any]:
    """Publish one fake event on /api/events. Body: {"type": "<event type>", ...overrides}."""
    event_type = body.get("type")
    if not event_type:
        raise HTTPException(status_code=422, detail="body needs a 'type'")
    overrides = {k: v for k, v in body.items() if k != "type"}
    return dev.emit(event_type, overrides)


router.include_router(dev_router)
