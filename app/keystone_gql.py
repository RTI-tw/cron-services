import os
from typing import Any, Dict, Optional

import httpx

_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _client
    endpoint = (os.getenv("KEYSTONE_GQL_ENDPOINT") or "").strip()
    if not endpoint:
        raise RuntimeError("KEYSTONE_GQL_ENDPOINT 環境變數未設定")

    if _client is None:
        headers = {"Content-Type": "application/json"}
        token = (os.getenv("KEYSTONE_AUTH_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        _client = httpx.Client(base_url=endpoint, headers=headers, timeout=60.0)
    return _client


def execute_gql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    client = _get_client()
    resp = client.post("", json={"query": query, "variables": variables or {}})
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]
