"""Groknet domain model. See docs/design.md.

MVP tables: User, Friendship, Message. Dashboard login: LoginCode.
Teams (stretch, but schema-ready from day one): Team, TeamMember, and the
nullable Message.recipient / Message.team_id columns.
Projects (issue #32): Project, Issue, Artifact.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.crypto import EncryptedStr


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class User(SQLModel, table=True):
    handle: str = Field(primary_key=True)  # unique, lowercase, e.g. "tomas"
    display_name: str  # the human's name, as the bot introduces them
    # Optional facts the human chose to share: age, job, bio, location, anything flat.
    profile: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, default={}))
    email: str = Field(
        index=True, unique=True
    )  # the bot's address, must end with @mail.grokbot.com
    groknet_token: str = Field(index=True, unique=True)  # bearer the bot passes to every tool
    # Both encrypted at rest (issue #43, app/crypto.py); the code sees plain strings.
    # The bot's Grok Bot routine trigger URL; only whoami / GET /api/me return it, to its owner.
    webhook_url: str = Field(sa_column=Column(EncryptedStr, nullable=False))
    # The routine's crsr_ bearer key; never returned by any read endpoint.
    webhook_key: str = Field(sa_column=Column(EncryptedStr, nullable=False))
    created_at: datetime = Field(default_factory=now)


class FriendshipStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"


class Friendship(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    requester: str = Field(foreign_key="user.handle", index=True)
    addressee: str = Field(foreign_key="user.handle", index=True)
    status: FriendshipStatus = Field(default=FriendshipStatus.pending)
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    event_id: str = Field(default_factory=new_id, unique=True)  # idempotency key sent to the bot
    sender: str = Field(foreign_key="user.handle", index=True)
    recipient: str | None = Field(default=None, foreign_key="user.handle", index=True)  # DM
    team_id: str | None = Field(default=None, foreign_key="team.id", index=True)  # team message
    body: str
    mentions: str | None = None  # comma-separated handles, team messages only
    delivered: bool = False
    created_at: datetime = Field(default_factory=now)


class Team(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    created_by: str = Field(foreign_key="user.handle")
    created_at: datetime = Field(default_factory=now)


class TeamMember(SQLModel, table=True):
    team_id: str = Field(foreign_key="team.id", primary_key=True)
    user_handle: str = Field(foreign_key="user.handle", primary_key=True)
    added_at: datetime = Field(default_factory=now)


class LoginCode(SQLModel, table=True):
    """A dashboard login code (issue #12). Only the sha256 of the code is stored."""

    id: str = Field(default_factory=new_id, primary_key=True)
    email: str = Field(index=True)
    code_hash: str
    expires_at: datetime
    used: bool = False
    created_at: datetime = Field(default_factory=now)


class Project(SQLModel, table=True):
    """Belongs to exactly one team; only that team's members write (issue #32)."""

    id: str = Field(default_factory=new_id, primary_key=True)
    team_id: str = Field(foreign_key="team.id", index=True)
    name: str
    description: str = ""
    status: str = "active"  # active | archived
    created_by: str = Field(foreign_key="user.handle")
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Issue(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    title: str
    description: str = ""
    status: str = "open"  # open | in_progress | done
    assignee: str | None = Field(default=None, foreign_key="user.handle")
    mentions: str = ""  # comma-separated member handles, like Message.mentions
    # The assignee's answer: pending | accepted | declined, null while unassigned.
    # Any change of assignee resets both and records who assigned it.
    assignment_status: str | None = None
    assignment_comment: str | None = None
    assigned_by: str | None = Field(default=None, foreign_key="user.handle")
    created_by: str = Field(foreign_key="user.handle")
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Artifact(SQLModel, table=True):
    """Markdown, plain text or a URL on a project, optionally tied to one of its issues."""

    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    issue_id: str | None = Field(default=None, foreign_key="issue.id", index=True)
    title: str
    kind: str  # markdown | text | url
    content: str
    created_by: str = Field(foreign_key="user.handle")
    created_at: datetime = Field(default_factory=now)
