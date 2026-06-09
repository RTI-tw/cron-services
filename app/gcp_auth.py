"""Authenticate to a locked (``--invoker-iam-check``) Cloud Run backend.

Mints a Google-signed ID token whose audience is the backend service root URL and
returns it as an ``X-Serverless-Authorization`` header. Using that header (rather
than ``Authorization``) means any app-level ``Authorization`` the caller sends is
forwarded to the backend untouched — and, since this service sends none, the
backend sees exactly the same request as before once the token is added.

Returns ``{}`` on failure (e.g. a still-public backend, or local runs without
credentials) so callers can merge the result into request headers
unconditionally.
"""

import base64
import json
import logging
import os
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import google.auth.transport.requests
import google.oauth2.id_token

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}  # audience -> (token, exp_epoch_seconds)


def _audience(endpoint: str) -> str:
    """Backend service root URL (scheme://host); the ID token's ``aud`` claim."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def _decode_exp(token: str) -> Optional[float]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


def invoker_auth_headers(endpoint: str) -> dict[str, str]:
    """Header that authenticates this service to a locked Cloud Run backend."""
    override = (os.getenv("KEYSTONE_INVOKER_ID_TOKEN") or "").strip()
    if override:
        return {"X-Serverless-Authorization": f"Bearer {override}"}

    if not endpoint:
        return {}

    audience = _audience(endpoint)
    now = time.time()
    with _lock:
        cached = _cache.get(audience)
        if cached and cached[1] - 60 > now:
            return {"X-Serverless-Authorization": f"Bearer {cached[0]}"}

    try:
        request = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(request, audience)
    except Exception as exc:  # never break the request path on a token-fetch failure
        logger.warning("[invoker-auth] failed to fetch ID token for %s: %s", audience, exc)
        return {}

    exp = _decode_exp(token) or (now + 55 * 60)
    with _lock:
        _cache[audience] = (token, exp)
    return {"X-Serverless-Authorization": f"Bearer {token}"}
