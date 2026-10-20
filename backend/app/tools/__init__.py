"""Import every feature module so its tools register on the shared `mcp` instance."""

from app.tools import (
    friendships,  # noqa: F401
    identity,  # noqa: F401
    messaging,  # noqa: F401
    meta,  # noqa: F401
    onboarding,  # noqa: F401
    projects,  # noqa: F401
    teams,  # noqa: F401
)
