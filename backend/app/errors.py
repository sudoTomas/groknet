class GroknetError(Exception):
    """A rejected request whose message is safe and useful to show to the caller (bot or REST)."""


class RegistrationError(GroknetError):
    pass


class AuthError(GroknetError):
    pass
