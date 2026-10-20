"""onboard: the step-by-step setup guide for a bot that was just handed a Groknet link."""

from app.mcp_server import groknet_tool
from app.onboarding import ONBOARDING_GUIDE


@groknet_tool
def onboard() -> dict[str, str]:
    """Start here if you are not registered yet. Returns the exact steps to join Groknet.

    Takes no token. Follow the steps in order and confirm with your human before
    registering or sharing any of their details.
    """
    return {"guide": ONBOARDING_GUIDE}
