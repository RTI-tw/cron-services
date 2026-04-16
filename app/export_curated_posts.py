from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import (
    _POST_CARD_SELECTION,
    _append_count_fields,
    _fetch_posts_in_pages,
    _hot_score,
    _hot_threshold,
    _max_take_per_request,
    _merge_boost_first_with_reason,
    _normalize_prefix,
    _pop_take_limit,
    _prepare_posts_for_export,
    _rank_hot_posts,
    _resolve_post_status,
    _to_int,
    _upload_json,
)
from .keystone_gql import execute_gql

_CURATED_GROUPS: List[Tuple[str, str, str]] = [
    ("editor-choice", "編輯精選", "isEditorChoice"),
    ("life-guide", "生活須知", "isLifeGuide"),
]


def _where_curated_status(status_token: str, flag_field: str, *, with_poll: bool = False, with_since: bool = False) -> str:
    parts = [
        f"status: {{ equals: {status_token} }}",
        f"{flag_field}: {{ equals: true }}",
    ]
    if with_since:
        parts.append("createdAt: { gte: $since }")
    if with_poll:
        parts.append("NOT: [{ poll: null }]")
    return "\n      ".join(parts)


def _build_query_curated_latest(status_token: str, flag_field: str) -> str:
    w = _where_curated_status(status_token, flag_field)
    return f"""
query CuratedLatest($take: Int!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_curated_polls(status_token: str, flag_field: str) -> str:
    w = _where_curated_status(status_token, flag_field, with_poll=True)
    return f"""
query CuratedPolls($take: Int!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_curated_hot_window(status_token: str, flag_field: str) -> str:
    w = _where_curated_status(status_token, flag_field, with_since=True)
    return f"""
query CuratedHotWindow($take: Int!, $skip: Int!, $since: DateTime!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ createdAt: desc }}]
    skip: $skip
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_curated_boost(status_token: str, flag_field: str) -> str:
    w = _where_curated_status(status_token, flag_field)
    return f"""
query CuratedBoost($take: Int!) {{
  posts(
    where: {{
      {w}
      isBoost: {{ equals: true }}
    }}
    orderBy: [{{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
      isBoost: {{ equals: true }}
  }})
}}
"""


def _curated_payload(
    generated_at: str,
    key: str,
    label: str,
    flag_field: str,
    posts_count: int,
    posts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return _append_count_fields({
        "generatedAt": generated_at,
        "collection": {
            "key": key,
            "label": label,
            "flag": flag_field,
        },
        "postsCount": posts_count,
        "posts": _prepare_posts_for_export(posts),
    }, posts_count)


def _build_curated_group_result(
    *,
    key: str,
    label: str,
    flag_field: str,
    take: int,
    pop_take: int,
    hot_scan_take: int,
    threshold: int,
    status_token: str,
    since_3d: str,
    since_14d: str,
    generated_at: str,
) -> Dict[str, Dict[str, Any]]:
    q_latest = _build_query_curated_latest(status_token, flag_field)
    q_polls = _build_query_curated_polls(status_token, flag_field)
    q_hot_window = _build_query_curated_hot_window(status_token, flag_field)
    q_boost = _build_query_curated_boost(status_token, flag_field)

    latest_data = execute_gql(q_latest, {"take": take})
    latest_posts = latest_data.get("posts") or []
    latest_count = _to_int(latest_data.get("postsCount"))

    polls_data = execute_gql(q_polls, {"take": take})
    polls_posts = polls_data.get("posts") or []
    polls_count = _to_int(polls_data.get("postsCount"))

    hot_3d_posts, hot_3d_count = _fetch_posts_in_pages(
        q_hot_window,
        {"since": since_3d},
        total_limit=hot_scan_take,
    )

    boost_data = execute_gql(q_boost, {"take": pop_take})
    boost_posts = boost_data.get("posts") or []

    ranked_3d = _rank_hot_posts(hot_3d_posts) if hot_3d_posts else []
    eligible_3d = [p for p in ranked_3d if _hot_score(p) >= threshold]

    if eligible_3d:
        pop_posts = _merge_boost_first_with_reason(
            boost_posts,
            eligible_3d,
            pop_take,
            default_reason="3d-score",
        )
        pop_count = hot_3d_count
    else:
        hot_14d_posts, hot_14d_count = _fetch_posts_in_pages(
            q_hot_window,
            {"since": since_14d},
            total_limit=hot_scan_take,
        )
        ranked_14d = _rank_hot_posts(hot_14d_posts) if hot_14d_posts else []
        has_interaction_14d = any(_hot_score(p) > 0 for p in ranked_14d)
        if ranked_14d and has_interaction_14d:
            pop_posts = _merge_boost_first_with_reason(
                boost_posts,
                ranked_14d,
                pop_take,
                default_reason="14d-score",
            )
            pop_count = hot_14d_count
        else:
            pop_posts = _merge_boost_first_with_reason(
                boost_posts,
                latest_posts[:10],
                pop_take,
                default_reason="latest-fallback",
            )
            pop_count = latest_count

    out = {
        "latest": _curated_payload(generated_at, key, label, flag_field, latest_count, latest_posts),
        "polls": _curated_payload(generated_at, key, label, flag_field, polls_count, polls_posts),
        "pop": _curated_payload(generated_at, key, label, flag_field, pop_count, pop_posts),
    }
    if not pop_posts:
        out["pop"]["emptyMessage"] = "尚無貼文"
    return out


def export_curated_posts_to_gcs(
    *,
    prefix: str = "exports/curated-posts",
    limit: int = 10,
    post_state: str = "active",
    scan_multiplier: int = 10,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if limit <= 0:
        raise ValueError("limit 必須大於 0")
    if scan_multiplier <= 0:
        raise ValueError("scan_multiplier 必須大於 0")

    status_token = _resolve_post_status(post_state)
    max_take = _max_take_per_request()
    take = min(limit, max_take)
    pop_take = _pop_take_limit(max_take)
    hot_scan_take = max(pop_take, limit * scan_multiplier)
    threshold = _hot_threshold()

    now = datetime.now(timezone.utc)
    since_3d = (now - timedelta(days=3)).isoformat()
    since_14d = (now - timedelta(days=14)).isoformat()
    generated_at = now.isoformat()

    grouped_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for key, label, flag_field in _CURATED_GROUPS:
        grouped_results[key] = _build_curated_group_result(
            key=key,
            label=label,
            flag_field=flag_field,
            take=take,
            pop_take=pop_take,
            hot_scan_take=hot_scan_take,
            threshold=threshold,
            status_token=status_token,
            since_3d=since_3d,
            since_14d=since_14d,
            generated_at=generated_at,
        )

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    uploaded_paths: List[str] = []
    for key, _, _ in _CURATED_GROUPS:
        for suffix in ("latest", "polls", "pop"):
            object_path = f"{base_dir}/{key}-{suffix}.json" if base_dir else f"{key}-{suffix}.json"
            _upload_json(bucket, object_path, grouped_results[key][suffix])
            uploaded_paths.append(object_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "collections": [key for key, _, _ in _CURATED_GROUPS],
        "limit": limit,
        "take_used": take,
        "pop_take_used": pop_take,
        "hot_scan_take": hot_scan_take,
        "hot_score_threshold": threshold,
        "post_state": status_token,
    }
