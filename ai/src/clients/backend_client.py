"""Read the FastAPI BE careers endpoint; no issuer or verification mutations."""
import math
from urllib.parse import quote, urlsplit

import httpx


def validate_subject_did(value):
    if not isinstance(value, str) or not value.startswith("did:") or not value[4:]:
        raise ValueError("A non-empty subject DID is required")
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Subject DID must not contain whitespace")
    # The BE route accepts one path segment, not a DID URL.
    if any(c in value for c in "/?#%"):
        raise ValueError("Use a subject DID, not a DID URL")
    return value


class BackendClient:
    def __init__(self, base_url, *, timeout=30, transport=None):
        url = urlsplit(base_url)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/")):
            raise ValueError("BE base URL must be an HTTP(S) origin, e.g. http://127.0.0.1:3000")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout,
                                   transport=transport, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.client.close()

    def careers(self, subject_did):
        did = validate_subject_did(subject_did)
        response = self.client.get(f"/api/v1/subjects/{quote(did, safe='')}/careers")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("BE returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise ValueError("BE careers response must be a JSON object")
        return payload
