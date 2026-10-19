"""Registration and token identity. Used by the MCP tools and the REST mirror."""

import re
import secrets

from sqlmodel import Session, select

from app.errors import AuthError, RegistrationError
from app.events import bus
from app.models import User

ALLOWED_EMAIL_DOMAIN = "@mail.grokbot.com"
HANDLE_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


def require_user(session: Session, groknet_token: str) -> User:
    """Resolve the caller from the token every tool carries. Shared by all tools."""
    user = session.exec(select(User).where(User.groknet_token == groknet_token)).first()
    if user is None:
        raise AuthError("Unknown groknet_token. Register first, or re-register to get a new one.")
    return user


def register_user(
    session: Session,
    *,
    handle: str,
    display_name: str,
    email: str,
    webhook_url: str,
    webhook_key: str,
) -> User:
    if not HANDLE_RE.match(handle):
        raise RegistrationError(
            f"handle must be 2-32 chars of lowercase letters, digits, _ or -, got {handle!r}."
        )
    email = email.strip().lower()
    if not email.endswith(ALLOWED_EMAIL_DOMAIN) or email == ALLOWED_EMAIL_DOMAIN:
        raise RegistrationError(
            f"Only Grok Bot addresses ending in {ALLOWED_EMAIL_DOMAIN} can join Groknet, got {email!r}."
        )
    existing = session.get(User, handle)
    if existing is not None:
        # Re-adding the template must never lock anyone out: same handle + same email
        # replaces the inbox credentials and rotates the token.
        if existing.email != email:
            raise RegistrationError(
                f"handle {handle!r} is already registered with a different email."
            )
        existing.display_name = display_name
        existing.webhook_url = webhook_url
        existing.webhook_key = webhook_key
        existing.groknet_token = secrets.token_urlsafe(32)
        user, event_type = existing, "user_reregistered"
    else:
        if session.exec(select(User).where(User.email == email)).first() is not None:
            raise RegistrationError(f"email {email!r} is already registered under another handle.")
        user = User(
            handle=handle,
            display_name=display_name,
            email=email,
            groknet_token=secrets.token_urlsafe(32),
            webhook_url=webhook_url,
            webhook_key=webhook_key,
        )
        session.add(user)
        event_type = "user_registered"
    session.commit()
    session.refresh(user)
    bus.publish({"type": event_type, "handle": user.handle, "display_name": user.display_name})
    return user
