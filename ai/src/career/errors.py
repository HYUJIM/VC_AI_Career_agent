class EvidenceError(ValueError):
    """Evidence could not be authenticated or validated; never expose secret material."""


class ConfigurationError(RuntimeError):
    """A required AI-side setting (service token, generator) is absent or malformed."""


class Rejection(Exception):
    """A deliberate refusal of the request. The server maps it to an HTTP status.

    `message` is the exact text BE used to return before the split, so the public
    BE API keeps its error contract; `code` is a stable machine-readable reason.
    """

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
