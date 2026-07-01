import os
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .keystone_gql import execute_gql

MESSAGE_SERVICES_ENV_VARS = ("MESSAGE_SERVICES_URL", "MESSAGE_SERVICES_BASE_URL")
SUPPORTED_TARGETS = ("posts", "comments", "polls", "pollOptions")
DEFAULT_SYNC_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RUNTIME_SECONDS = 170.0
CONNECT_TIMEOUT_SECONDS = 30.0

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
        elif target in ("poll", "polls"):
            key = "polls"
        elif target in ("polloption", "polloptions", "poll_option", "poll_options"):
            key = "pollOptions"
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


QUERY_POLLS_MISSING_TRANSLATION = """
query PollsMissingTranslation(
  $where: PollWhereInput! = {}
  $orderBy: [PollOrderByInput!]! = [{ createdAt: asc }]
  $take: Int
  $skip: Int! = 0
) {
  polls(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    title
    createdAt
    updatedAt
  }
  pollsCount(where: $where)
}
"""

QUERY_POLL_OPTIONS_MISSING_TRANSLATION = """
query PollOptionsMissingTranslation(
  $where: PollOptionWhereInput! = {}
  $orderBy: [PollOptionOrderByInput!]! = [{ createdAt: asc }]
  $take: Int
  $skip: Int! = 0
) {
  pollOptions(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    text
    createdAt
    updatedAt
  }
  pollOptionsCount(where: $where)
}
"""


def _build_poll_where() -> Dict[str, Any]:
    # 有原文標題、但至少一個翻譯欄位仍為空（= 尚未翻譯）。
    return {
        "title": {"not": {"equals": ""}},
        "OR": [
            {"title_zh": {"equals": ""}},
            {"title_en": {"equals": ""}},
            {"title_vi": {"equals": ""}},
            {"title_id": {"equals": ""}},
            {"title_th": {"equals": ""}},
        ],
    }


def _build_poll_option_where() -> Dict[str, Any]:
    return {
        "text": {"not": {"equals": ""}},
        "OR": [
            {"text_zh": {"equals": ""}},
            {"text_en": {"equals": ""}},
            {"text_vi": {"equals": ""}},
            {"text_id": {"equals": ""}},
            {"text_th": {"equals": ""}},
        ],
    }


def _fetch_polls_missing_translation(*, take: int) -> Tuple[List[Dict[str, Any]], int]:
    data = execute_gql(
        QUERY_POLLS_MISSING_TRANSLATION,
        {
            "where": _build_poll_where(),
            "take": take,
            "skip": 0,
            "orderBy": [{"createdAt": "asc"}],
        },
    )
    return data.get("polls") or [], _to_int(data.get("pollsCount"))


def _fetch_poll_options_missing_translation(
    *, take: int
) -> Tuple[List[Dict[str, Any]], int]:
    data = execute_gql(
        QUERY_POLL_OPTIONS_MISSING_TRANSLATION,
        {
            "where": _build_poll_option_where(),
            "take": take,
            "skip": 0,
            "orderBy": [{"createdAt": "asc"}],
        },
    )
    return data.get("pollOptions") or [], _to_int(data.get("pollOptionsCount"))


def _poll_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "poll",
        "id": str(row.get("id") or ""),
        "source_text": str(row.get("title") or ""),
    }


def _poll_option_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "pollOption",
        "id": str(row.get("id") or ""),
        "source_text": str(row.get("text") or ""),
    }


def _poll_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "titleLength": len(str(row.get("title") or "")),
        "createdAt": row.get("createdAt"),
        "updatedAt": row.get("updatedAt"),
    }


def _poll_option_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "textLength": len(str(row.get("text") or "")),
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
        elif key == "polls":
            summarized_items = [_poll_summary(row) for row in items]
        elif key == "pollOptions":
            summarized_items = [_poll_option_summary(row) for row in items]
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
    try:
        resp = client.post(f"{base_url}/hooks/sync-translations", json=payload)
    except httpx.RequestError as e:
        return {
            "id": payload.get("id"),
            "type": payload.get("type"),
            "ok": False,
            "status_code": None,
            "error_type": type(e).__name__,
            "error": str(e) or type(e).__name__,
        }

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
    sync_timeout_seconds: float = DEFAULT_SYNC_TIMEOUT_SECONDS,
    max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
) -> Dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit 必須大於 0")
    if sync_timeout_seconds <= 0:
        raise ValueError("sync_timeout_seconds 必須大於 0")
    if max_runtime_seconds <= 0:
        raise ValueError("max_runtime_seconds 必須大於 0")

    started_at = monotonic()
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

    if "polls" in normalized_targets:
        polls, total = _fetch_polls_missing_translation(take=limit)
        found["polls"] = {
            "totalCount": total,
            "selectedCount": len(polls),
            "items": polls,
        }

    if "pollOptions" in normalized_targets:
        poll_options, total = _fetch_poll_options_missing_translation(take=limit)
        found["pollOptions"] = {
            "totalCount": total,
            "selectedCount": len(poll_options),
            "items": poll_options,
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
            "stoppedEarly": False,
            "stopReason": None,
            "skippedCount": 0,
            "results": [],
        }

    results: List[Dict[str, Any]] = []
    payloads: List[Dict[str, Any]] = []
    for row in found.get("posts", {}).get("items", []):
        payloads.append(_post_payload(row))
    for row in found.get("comments", {}).get("items", []):
        payloads.append(_comment_payload(row))
    for row in found.get("polls", {}).get("items", []):
        payloads.append(_poll_payload(row))
    for row in found.get("pollOptions", {}).get("items", []):
        payloads.append(_poll_option_payload(row))

    stopped_early = False
    stop_reason = None
    timeout = httpx.Timeout(
        sync_timeout_seconds,
        connect=min(CONNECT_TIMEOUT_SECONDS, sync_timeout_seconds),
    )
    with httpx.Client(timeout=timeout) as client:
        for payload in payloads:
            elapsed_seconds = monotonic() - started_at
            if elapsed_seconds + sync_timeout_seconds > max_runtime_seconds:
                stopped_early = True
                stop_reason = (
                    "max_runtime_seconds budget would be exceeded before the next "
                    "sync request"
                )
                break
            results.append(
                _call_sync_translations(
                    client,
                    base_url=resolved_message_services_url,
                    payload=payload,
                )
            )

    success_count = sum(1 for r in results if r.get("ok") is True)
    failure_count = len(results) - success_count
    skipped_count = len(payloads) - len(results)
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
        "stoppedEarly": stopped_early,
        "stopReason": stop_reason,
        "skippedCount": skipped_count,
        "results": results,
    }
