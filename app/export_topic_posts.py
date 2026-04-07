import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from google.cloud import storage

from .config import get_settings
from .keystone_gql import execute_gql, get_thread_local_gql_client


def _normalize_prefix(prefix: str) -> str:
    return (prefix or "").strip().strip("/")


def _topic_slug_for_path(topic: Dict[str, Any]) -> str:
    """GCS 檔名前綴：優先 topic.slug，空則 topic-{id 前綴}；避免路徑字元。"""
    slug = str(topic.get("slug") or "").strip()
    tid = str(topic.get("id") or "").strip()
    if slug:
        return slug.replace("/", "_").replace("\\", "_")
    if tid:
        return f"topic-{tid[:16]}"
    return "topic-unknown"


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


QUERY_TOPICS_META = """
query ListTopicsMeta {
  topics(orderBy: { sortOrder: asc }) {
    id
    name
    slug
    sortOrder
  }
}
"""


def _build_posts_page_query(status_enum_token: str) -> str:
    """單一 topic 分頁取 posts；每頁 take 不超過 Keystone graphql.maximumTake（常見 100）。"""
    return f"""
query TopicPostsPage($tid: ID!, $skip: Int!, $take: Int!) {{
  posts(
    where: {{
      topics: {{ some: {{ id: {{ equals: $tid }} }} }}
      status: {{ equals: {status_enum_token} }}
    }}
    orderBy: {{ createdAt: desc }}
    skip: $skip
    take: $take
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
    topics {{ id slug name }}
  }}
}}
"""


def _max_take_per_request() -> int:
    raw = (os.getenv("GQL_POST_MAX_TAKE") or "100").strip()
    try:
        return max(1, min(int(raw), 1000))
    except ValueError:
        return 100


def _paginate_posts_for_topic(
    topic_id: str,
    scan_limit: int,
    status_token: str,
    posts_page_query: str,
    max_take: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    skip = 0
    client = get_thread_local_gql_client()
    while len(out) < scan_limit:
        take = min(max_take, scan_limit - len(out))
        data = execute_gql(
            posts_page_query,
            {"tid": topic_id, "skip": skip, "take": take},
            client=client,
        )
        rows = data.get("posts") or []
        if not rows:
            break
        out.extend(rows)
        skip += len(rows)
        if len(rows) < take:
            break
    return out[:scan_limit]


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
    topics_list = p.get("topics") or []
    first_topic = topics_list[0] if topics_list else None
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
        "topic": first_topic,
        "topics": topics_list,
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
    每個 topic 各寫入三個 JSON（``{prefix}/{slug}-latest.json``、``-pop.json``、``-polls.json``），
    每次執行覆寫。slug 取自 Topic.slug，無 slug 時以 ``topic-{id}`` 前綴。

    - latest：依建立時間新到舊取 N 則
    - pop：依留言數熱門取 N 則
    - polls：該 topic 內含投票（與 polls 關聯）的文章，依掃描順序取 N 則
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
    max_take = _max_take_per_request()
    posts_page_query = _build_posts_page_query(status_token)

    data = execute_gql(QUERY_TOPICS_META, None)
    topics = data.get("topics") or []
    poll_post_ids = _collect_poll_post_ids()

    topic_id_to_posts: Dict[str, List[Dict[str, Any]]] = {}
    if topics:
        max_workers = min(16, max(1, len(topics)))

        def _job(t: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
            tid = str(t.get("id") or "").strip()
            if not tid:
                return "", []
            posts = _paginate_posts_for_topic(
                tid, scan_limit, status_token, posts_page_query, max_take
            )
            return tid, posts

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_job, t) for t in topics]
            for fut in as_completed(futures):
                tid, posts = fut.result()
                if tid:
                    topic_id_to_posts[tid] = posts

    base_dir = _normalize_prefix(prefix)

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    def _under_base(name: str) -> str:
        return f"{base_dir}/{name}" if base_dir else name

    generated_at = datetime.now(timezone.utc).isoformat()
    uploaded_paths: List[str] = []
    used_file_stems: Set[str] = set()

    for t in topics:
        tid = str(t.get("id") or "").strip()
        posts = topic_id_to_posts.get(tid, []) if tid else []
        shaped = [_shape_post(p) for p in posts]

        latest = shaped[:per_topic_limit]
        hot = sorted(
            shaped,
            key=lambda x: x.get("commentsCount") or 0,
            reverse=True,
        )[:per_topic_limit]
        with_poll = [
            p for p in shaped if str(p.get("id") or "") in poll_post_ids
        ][:per_topic_limit]

        topic_meta = {
            "id": t.get("id"),
            "name": t.get("name"),
            "slug": t.get("slug"),
            "sortOrder": t.get("sortOrder"),
        }

        stem = _topic_slug_for_path(t)
        if stem in used_file_stems and tid:
            stem = f"{stem}-{tid[:8]}"
        used_file_stems.add(stem)
        common_payload = {
            "generatedAt": generated_at,
            "perTopicLimit": per_topic_limit,
            "postState": status_token,
            "topic": topic_meta,
        }

        triples = (
            ("latest", latest),
            ("pop", hot),
            ("polls", with_poll),
        )
        for suffix, post_list in triples:
            object_name = f"{stem}-{suffix}.json"
            object_path = _under_base(object_name)
            _upload_json(
                bucket,
                object_path,
                {**common_payload, "posts": post_list},
            )
            uploaded_paths.append(object_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "topics_count": len(topics),
        "per_topic_limit": per_topic_limit,
        "post_state": status_token,
    }
