import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .keystone_gql import execute_gql

MESSAGE_SERVICES_ENV_VARS = ("MESSAGE_SERVICES_URL", "MESSAGE_SERVICES_BASE_URL")
SUPPORTED_TARGETS = ("posts", "comments")

QUERY_POSTS_MISSING_TRANSLATION = """
query PostsMissingTranslation(
  $where: PostWhereInput! = {}
  $orderBy: [PostOrderByInput!]! = [{ createdAt: asc }]
  $take: Int
  $skip: Int! = 0
) {
  posts(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    title
    content
    status
    spamScore
    createdAt
    updatedAt
  }
  postsCount(where: $where)
}
"""

QUERY_COMMENTS_MISSING_TRANSLATION = """
query CommentsMissingTranslation(
  $where: CommentWhereInput! = {}
  $orderBy: [CommentOrderByInput!]! = [{ createdAt: asc }]
  $take: Int
  $skip: Int! = 0
) {
  comments(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    content
    status
    spamScore
    pauseAutoTranslation
    createdAt
    updatedAt
  }
  commentsCount(where: $where)
}
"""


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_targets(value: str) -> List[str]:
    raw_targets = [x.strip().lower() for x in (value or "").split(",") if x.strip()]
    if not raw_targets:
        return list(SUPPORTED_TARGETS)

    normalized: List[str] = []
    for target in raw_targets:
        if target in ("post", "posts"):
            key = "posts"
        elif target in ("comment", "comments"):
            key = "comments"
        else:
            raise ValueError(f"不支援的 target: {target}")
        if key not in normalized:
            normalized.append(key)
    return normalized


def _parse_statuses(value: str) -> Optional[List[str]]:
    text = (value or "").strip()
    if not text or text.lower() == "all":
        return None
    statuses = [x.strip() for x in text.split(",") if x.strip()]
    return statuses or None


def _message_services_url(explicit_url: str = "") -> str:
    value = (explicit_url or "").strip()
    if not value:
        for env_name in MESSAGE_SERVICES_ENV_VARS:
            value = (os.getenv(env_name) or "").strip()
            if value:
                break
    if not value:
        env_names = " / ".join(MESSAGE_SERVICES_ENV_VARS)
        raise ValueError(f"message_services_url 未提供，且環境變數 {env_names} 皆未設定")
    return value.rstrip("/")


def _build_post_where(statuses: Optional[Sequence[str]]) -> Dict[str, Any]:
    where: Dict[str, Any] = {
        "spamScore": {"equals": None},
        "OR": [
            {"title": {"not": {"equals": ""}}},
            {"content": {"not": {"equals": ""}}},
        ],
    }
    if statuses:
        where["status"] = {"in": list(statuses)}
    return where


def _build_comment_where(statuses: Optional[Sequence[str]]) -> Dict[str, Any]:
    where: Dict[str, Any] = {
        "spamScore": {"equals": None},
        "content": {"not": {"equals": ""}},
        "pauseAutoTranslation": {"equals": False},
    }
    if statuses:
        where["status"] = {"in": list(statuses)}
    return where


def _fetch_posts_missing_translation(
    *,
    take: int,
    statuses: Optional[Sequence[str]],
) -> Tuple[List[Dict[str, Any]], int]:
    data = execute_gql(
        QUERY_POSTS_MISSING_TRANSLATION,
        {
            "where": _build_post_where(statuses),
            "take": take,
            "skip": 0,
            "orderBy": [{"createdAt": "asc"}],
        },
    )
    return data.get("posts") or [], _to_int(data.get("postsCount"))


def _fetch_comments_missing_translation(
    *,
    take: int,
    statuses: Optional[Sequence[str]],
) -> Tuple[List[Dict[str, Any]], int]:
    data = execute_gql(
        QUERY_COMMENTS_MISSING_TRANSLATION,
        {
            "where": _build_comment_where(statuses),
            "take": take,
            "skip": 0,
            "orderBy": [{"createdAt": "asc"}],
        },
    )
    return data.get("comments") or [], _to_int(data.get("commentsCount"))


def _post_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "post",
        "id": str(row.get("id") or ""),
        "source_text": str(row.get("content") or ""),
        "source_title": str(row.get("title") or ""),
    }
    status = row.get("status")
    if status:
        payload["status"] = str(status)
    return payload


def _comment_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "comment",
        "id": str(row.get("id") or ""),
        "source_text": str(row.get("content") or ""),
    }
    status = row.get("status")
    if status:
        payload["status"] = str(status)
    return payload


def _post_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "spamScore": row.get("spamScore"),
        "titleLength": len(str(row.get("title") or "")),
        "contentLength": len(str(row.get("content") or "")),
        "createdAt": row.get("createdAt"),
        "updatedAt": row.get("updatedAt"),
    }


def _comment_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "spamScore": row.get("spamScore"),
        "contentLength": len(str(row.get("content") or "")),
        "pauseAutoTranslation": row.get("pauseAutoTranslation"),
        "createdAt": row.get("createdAt"),
        "updatedAt": row.get("updatedAt"),
    }


def _summarize_found(found: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summarized: Dict[str, Dict[str, Any]] = {}
    for key, value in found.items():
        items = value.get("items") or []
        if key == "posts":
            summarized_items = [_post_summary(row) for row in items]
        elif key == "comments":
            summarized_items = [_comment_summary(row) for row in items]
        else:
            summarized_items = []
        summarized[key] = {
            "totalCount": value["totalCount"],
            "selectedCount": value["selectedCount"],
            "items": summarized_items,
        }
    return summarized


def _call_sync_translations(
    client: httpx.Client,
    *,
    base_url: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    resp = client.post(f"{base_url}/hooks/sync-translations", json=payload)
    body_preview = resp.text[:2000]
    ok = 200 <= resp.status_code < 300
    result: Dict[str, Any] = {
        "id": payload.get("id"),
        "type": payload.get("type"),
        "ok": ok,
        "status_code": resp.status_code,
    }
    if not ok:
        result["error"] = body_preview
    else:
        try:
            result["response"] = resp.json()
        except ValueError:
            result["response"] = body_preview
    return result


def retry_missing_translations(
    *,
    targets: str = "posts,comments",
    limit: int = 100,
    dry_run: bool = True,
    message_services_url: str = "",
    post_statuses: str = "published,pending,draft",
    comment_statuses: str = "published",
) -> Dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit 必須大於 0")

    normalized_targets = _normalize_targets(targets)
    post_status_filter = _parse_statuses(post_statuses)
    comment_status_filter = _parse_statuses(comment_statuses)
    resolved_message_services_url = (
        "" if dry_run else _message_services_url(message_services_url)
    )

    found: Dict[str, Dict[str, Any]] = {}
    if "posts" in normalized_targets:
        posts, total = _fetch_posts_missing_translation(
            take=limit,
            statuses=post_status_filter,
        )
        found["posts"] = {
            "totalCount": total,
            "selectedCount": len(posts),
            "items": posts,
        }

    if "comments" in normalized_targets:
        comments, total = _fetch_comments_missing_translation(
            take=limit,
            statuses=comment_status_filter,
        )
        found["comments"] = {
            "totalCount": total,
            "selectedCount": len(comments),
            "items": comments,
        }

    if dry_run:
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "dryRun": True,
            "targets": normalized_targets,
            "limit": limit,
            "postStatuses": post_status_filter or "all",
            "commentStatuses": comment_status_filter or "all",
            "messageServicesUrl": None,
            "found": _summarize_found(found),
            "attemptedCount": 0,
            "successCount": 0,
            "failureCount": 0,
            "results": [],
        }

    results: List[Dict[str, Any]] = []
    timeout = httpx.Timeout(180.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        for row in found.get("posts", {}).get("items", []):
            results.append(
                _call_sync_translations(
                    client,
                    base_url=resolved_message_services_url,
                    payload=_post_payload(row),
                )
            )
        for row in found.get("comments", {}).get("items", []):
            results.append(
                _call_sync_translations(
                    client,
                    base_url=resolved_message_services_url,
                    payload=_comment_payload(row),
                )
            )

    success_count = sum(1 for r in results if r.get("ok") is True)
    failure_count = len(results) - success_count
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dryRun": False,
        "targets": normalized_targets,
        "limit": limit,
        "postStatuses": post_status_filter or "all",
        "commentStatuses": comment_status_filter or "all",
        "messageServicesUrl": resolved_message_services_url,
        "found": {
            key: {
                "totalCount": value["totalCount"],
                "selectedCount": value["selectedCount"],
            }
            for key, value in found.items()
        },
        "attemptedCount": len(results),
        "successCount": success_count,
        "failureCount": failure_count,
        "results": results,
    }
