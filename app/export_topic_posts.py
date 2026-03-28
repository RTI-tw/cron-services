import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from google.cloud import storage

from .config import get_settings
from .keystone_gql import execute_gql


def _normalize_prefix(prefix: str) -> str:
    return (prefix or "").strip().strip("/")


def _resolve_post_status(status: str) -> str:
    """
    使用者語意的 active 對應到 Keystone Post.status 的 published。
    """
    s = (status or "").strip().lower()
    if s in ("active", "published"):
        return "published"
    if s in ("draft", "archived", "hidden"):
        return s
    raise ValueError(f"不支援的 post 狀態: {status}")


def _build_topics_posts_query(status_enum_token: str, scan_limit: int) -> str:
    return f"""
query ListTopicsWithPosts {{
  topics(orderBy: {{ sortOrder: asc }}) {{
    id
    name
    slug
    sortOrder
    posts(
      where: {{ status: {{ equals: {status_enum_token} }} }}
      orderBy: {{ createdAt: desc }}
      take: {scan_limit}
    ) {{
      id
      title
      content
      language
      content_zh
      content_en
      content_vi
      content_th
      content_id
      spamScore
      status
      createdAt
      updatedAt
      comments {{ id }}
      topic {{ id slug name }}
    }}
  }}
}}
"""


QUERY_POLLS_POST_IDS = """
query ListPollsPostIds($take: Int!, $skip: Int!) {
  polls(take: $take, skip: $skip) {
    id
    post { id }
  }
}
"""


def _collect_poll_post_ids(batch_size: int = 200) -> Set[str]:
    ids: Set[str] = set()
    skip = 0
    while True:
        data = execute_gql(QUERY_POLLS_POST_IDS, {"take": batch_size, "skip": skip})
        rows = data.get("polls") or []
        if not rows:
            break
        for row in rows:
            post = row.get("post") or {}
            post_id = str(post.get("id") or "").strip()
            if post_id:
                ids.add(post_id)
        skip += len(rows)
    return ids


def _shape_post(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "content": p.get("content"),
        "language": p.get("language"),
        "content_zh": p.get("content_zh"),
        "content_en": p.get("content_en"),
        "content_vi": p.get("content_vi"),
        "content_th": p.get("content_th"),
        "content_id": p.get("content_id"),
        "spamScore": p.get("spamScore"),
        "status": p.get("status"),
        "createdAt": p.get("createdAt"),
        "updatedAt": p.get("updatedAt"),
        "commentsCount": len((p.get("comments") or [])),
        "topic": p.get("topic"),
    }


def _upload_json(bucket: storage.Bucket, path: str, payload: Dict[str, Any]) -> None:
    blob = bucket.blob(path)
    blob.upload_from_string(
        json.dumps(payload, ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )


def export_topic_posts_to_gcs(
    *,
    prefix: str = "exports/topic-posts",
    per_topic_limit: int = 10,
    post_state: str = "active",
    scan_multiplier: int = 10,
) -> Dict[str, Any]:
    """
    產出三份檔案：
    1) latest.json: 每個 topic 的最新 post（依 createdAt desc）
    2) hot.json: 每個 topic 的熱門 post（留言數最多）
    3) with-poll.json: 每個 topic 中有投票內容的 post（依 createdAt desc）
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if per_topic_limit <= 0:
        raise ValueError("per_topic_limit 必須大於 0")
    if scan_multiplier <= 0:
        raise ValueError("scan_multiplier 必須大於 0")

    status_token = _resolve_post_status(post_state)
    scan_limit = per_topic_limit * scan_multiplier

    query = _build_topics_posts_query(status_token, scan_limit)
    data = execute_gql(query, None)
    topics = data.get("topics") or []
    poll_post_ids = _collect_poll_post_ids()

    latest_by_topic: List[Dict[str, Any]] = []
    hot_by_topic: List[Dict[str, Any]] = []
    with_poll_by_topic: List[Dict[str, Any]] = []

    for t in topics:
        posts = t.get("posts") or []
        shaped = [_shape_post(p) for p in posts]

        latest = shaped[:per_topic_limit]
        hot = sorted(shaped, key=lambda x: x.get("commentsCount") or 0, reverse=True)[:per_topic_limit]
        with_poll = [p for p in shaped if str(p.get("id") or "") in poll_post_ids][:per_topic_limit]

        topic_meta = {
            "id": t.get("id"),
            "name": t.get("name"),
            "slug": t.get("slug"),
            "sortOrder": t.get("sortOrder"),
        }

        latest_by_topic.append({"topic": topic_meta, "posts": latest})
        hot_by_topic.append({"topic": topic_meta, "posts": hot})
        with_poll_by_topic.append({"topic": topic_meta, "posts": with_poll})

    export_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = _normalize_prefix(prefix)
    base_dir = f"{safe_prefix}/{export_ts}" if safe_prefix else export_ts

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    latest_path = f"{base_dir}/latest.json"
    hot_path = f"{base_dir}/hot.json"
    with_poll_path = f"{base_dir}/with-poll.json"

    common_meta = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "perTopicLimit": per_topic_limit,
        "postState": status_token,
    }
    _upload_json(
        bucket,
        latest_path,
        {**common_meta, "topicsCount": len(latest_by_topic), "data": latest_by_topic},
    )
    _upload_json(
        bucket,
        hot_path,
        {**common_meta, "topicsCount": len(hot_by_topic), "data": hot_by_topic},
    )
    _upload_json(
        bucket,
        with_poll_path,
        {**common_meta, "topicsCount": len(with_poll_by_topic), "data": with_poll_by_topic},
    )

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [latest_path, hot_path, with_poll_path],
        "topics_count": len(topics),
        "per_topic_limit": per_topic_limit,
        "post_state": status_token,
    }
