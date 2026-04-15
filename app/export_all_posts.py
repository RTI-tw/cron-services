from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import (
    _POST_CARD_SELECTION,
    _hot_score,
    _hot_threshold,
    _max_take_per_request,
    _merge_boost_first,
    _normalize_prefix,
    _pop_take_limit,
    _rank_hot_posts,
    _resolve_post_status,
    _to_int,
    _upload_json,
)
from .keystone_gql import execute_gql


def _where_all_posts_status(status_token: str, *, with_poll: bool = False, with_since: bool = False) -> str:
    parts = [f"status: {{ equals: {status_token} }}"]
    if with_since:
        parts.append("createdAt: { gte: $since }")
    if with_poll:
        parts.append("NOT: [{ poll: null }]")
    return "\n      ".join(parts)


def _build_query_all_posts_latest(status_token: str) -> str:
    w = _where_all_posts_status(status_token)
    return f"""
query AllPostsLatest($take: Int!) {{
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


def _build_query_all_posts_polls(status_token: str) -> str:
    w = _where_all_posts_status(status_token, with_poll=True)
    return f"""
query AllPostsPolls($take: Int!) {{
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


def _build_query_all_posts_hot_window(status_token: str) -> str:
    w = _where_all_posts_status(status_token, with_since=True)
    return f"""
query AllPostsHotWindow($take: Int!, $since: DateTime!) {{
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


def _build_query_all_posts_boost(status_token: str) -> str:
    w = _where_all_posts_status(status_token)
    return f"""
query AllPostsBoost($take: Int!) {{
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


def _all_posts_payload(generated_at: str, posts_count: int, posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generatedAt": generated_at,
        "collection": {
            "key": "all-posts",
            "label": "所有文章",
        },
        "postsCount": posts_count,
        "posts": posts,
    }


def export_all_posts_to_gcs(
    *,
    prefix: str = "exports/all-posts",
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
    hot_scan_take = min(max(pop_take, limit * scan_multiplier), max_take)
    threshold = _hot_threshold()

    q_latest = _build_query_all_posts_latest(status_token)
    q_polls = _build_query_all_posts_polls(status_token)
    q_hot_window = _build_query_all_posts_hot_window(status_token)
    q_boost = _build_query_all_posts_boost(status_token)

    now = datetime.now(timezone.utc)
    since_3d = (now - timedelta(days=3)).isoformat()
    since_14d = (now - timedelta(days=14)).isoformat()
    generated_at = now.isoformat()

    latest_data = execute_gql(q_latest, {"take": take})
    latest_posts = latest_data.get("posts") or []
    latest_count = _to_int(latest_data.get("postsCount"))

    polls_data = execute_gql(q_polls, {"take": take})
    polls_posts = polls_data.get("posts") or []
    polls_count = _to_int(polls_data.get("postsCount"))

    hot_3d = execute_gql(q_hot_window, {"take": hot_scan_take, "since": since_3d})
    hot_3d_posts = hot_3d.get("posts") or []
    hot_3d_count = _to_int(hot_3d.get("postsCount"))

    boost_data = execute_gql(q_boost, {"take": pop_take})
    boost_posts = boost_data.get("posts") or []

    ranked_3d = _rank_hot_posts(hot_3d_posts) if hot_3d_posts else []
    eligible_3d = [p for p in ranked_3d if _hot_score(p) >= threshold]

    if eligible_3d:
        pop_posts = _merge_boost_first(boost_posts, eligible_3d, pop_take)
        pop_count = hot_3d_count
    else:
        hot_14d = execute_gql(q_hot_window, {"take": hot_scan_take, "since": since_14d})
        hot_14d_posts = hot_14d.get("posts") or []
        hot_14d_count = _to_int(hot_14d.get("postsCount"))
        ranked_14d = _rank_hot_posts(hot_14d_posts) if hot_14d_posts else []
        has_interaction_14d = any(_hot_score(p) > 0 for p in ranked_14d)
        if ranked_14d and has_interaction_14d:
            pop_posts = _merge_boost_first(boost_posts, ranked_14d, pop_take)
            pop_count = hot_14d_count
        else:
            pop_posts = _merge_boost_first(boost_posts, latest_posts[:10], pop_take)
            pop_count = latest_count

    payloads = {
        "latest": _all_posts_payload(generated_at, latest_count, latest_posts),
        "polls": _all_posts_payload(generated_at, polls_count, polls_posts),
        "pop": _all_posts_payload(generated_at, pop_count, pop_posts),
    }
    if not pop_posts:
        payloads["pop"]["emptyMessage"] = "尚無貼文"

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    uploaded_paths: List[str] = []
    for suffix in ("latest", "polls", "pop"):
        object_path = f"{base_dir}/all-posts-{suffix}.json" if base_dir else f"all-posts-{suffix}.json"
        _upload_json(bucket, object_path, payloads[suffix])
        uploaded_paths.append(object_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "collection": "all-posts",
        "limit": limit,
        "take_used": take,
        "pop_take_used": pop_take,
        "hot_scan_take": hot_scan_take,
        "hot_score_threshold": threshold,
        "post_state": status_token,
    }
