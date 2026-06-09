import json
import os
import threading
from typing import Any, Dict, Optional

import httpx

from .gcp_auth import invoker_auth_headers

_client: Optional[httpx.Client] = None
_thread_local = threading.local()


def _client_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (os.getenv("KEYSTONE_AUTH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_client() -> httpx.Client:
    global _client
    endpoint = (os.getenv("KEYSTONE_GQL_ENDPOINT") or "").strip()
    if not endpoint:
        raise RuntimeError("KEYSTONE_GQL_ENDPOINT 環境變數未設定")

    if _client is None:
        _client = httpx.Client(
            base_url=endpoint,
            headers=_client_headers(),
            timeout=httpx.Timeout(120.0, connect=30.0),
        )
    return _client


def get_thread_local_gql_client() -> httpx.Client:
    """
    供 ThreadPoolExecutor 等情境使用：每個執行緒各自一個 Client，避免共用 httpx 連線的競態。
    """
    endpoint = (os.getenv("KEYSTONE_GQL_ENDPOINT") or "").strip()
    if not endpoint:
        raise RuntimeError("KEYSTONE_GQL_ENDPOINT 環境變數未設定")
    c = getattr(_thread_local, "client", None)
    if c is None:
        c = httpx.Client(
            base_url=endpoint,
            headers=_client_headers(),
            timeout=httpx.Timeout(120.0, connect=30.0),
        )
        _thread_local.client = c
    return c


def execute_gql(
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    c = client or _get_client()
    # Per-request so the (cached) Google ID token can refresh on expiry. Sent as
    # X-Serverless-Authorization to authenticate to the locked GQL Cloud Run service.
    endpoint = (os.getenv("KEYSTONE_GQL_ENDPOINT") or "").strip()
    resp = c.post(
        "",
        json={"query": query, "variables": variables or {}},
        headers=invoker_auth_headers(endpoint),
    )
    try:
        payload = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"GraphQL 回應非 JSON (HTTP {resp.status_code}): {resp.text[:2000]}"
        ) from e

    if resp.status_code >= 400:
        err_detail = payload if isinstance(payload, dict) else resp.text
        raise RuntimeError(f"GraphQL HTTP {resp.status_code}: {err_detail}")

    if "errors" in payload:
        raise RuntimeError(f"GraphQL error: {payload['errors']}")

    return payload["data"]
