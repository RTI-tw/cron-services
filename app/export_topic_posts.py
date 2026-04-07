import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

from google.cloud import storage

from .config import get_settings
from .keystone_gql import execute_gql, get_thread_local_gql_client

# 與前端共用：PhotoFields（兩處內嵌）
_PHOTO_FIELDS = """
id
name
resized {
  original
  w480
  w800
  w1200
}
urlOriginal
"""

# 與前端共用：PostCardFields（內嵌 Photo，不用 fragment 語法以便單一 document）
_POST_CARD_SELECTION = f"""
    id
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    content
    content_zh
    content_en
    content_vi
    content_id
    content_th
    language
    status
    createdAt
    updatedAt
    author {{
      id
      name
      nickname
      avatar
      avatar_image {{
{_PHOTO_FIELDS}
      }}
      customId
      isOfficial
    }}
    isEditorChoice
    isLifeGuide
    topics {{
      id
      name
      name_zh
      name_en
      name_vi
      name_id
      name_th
      slug
    }}
    topicsCount
    heroImages(orderBy: {{ sortOrder: asc }}) {{
{_PHOTO_FIELDS}
    }}
    poll {{
      id
    }}
    commentsCount
    reactionsCount
    reactions(take: 5) {{
      id
      type
    }}
"""


def _normalize_prefix(prefix: str) -> str:
    return (prefix or "").strip().strip("/")


def _topic_slug_for_path(topic: Dict[str, Any]) -> str:
    """GCS 檔名前綴：優先 topic.slug，空則 topic-{id 前綴}。"""
    slug = str(topic.get("slug") or "").strip()
    tid = str(topic.get("id") or "").strip()
    if slug:
        return slug.replace("/", "_").replace("\\", "_")
    if tid:
        return f"topic-{tid[:16]}"
    return "topic-unknown"


def _resolve_post_status(status: str) -> str:
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


def _max_take_per_request() -> int:
    raw = (os.getenv("GQL_POST_MAX_TAKE") or "100").strip()
    try:
        return max(1, min(int(raw), 1000))
    except ValueError:
        return 100


def _where_topic_by_slug_status(status_token: str) -> str:
    """posts / postsCount 共用的 where（依 slug + status）。"""
    return f"""
      status: {{ equals: "{status_token}" }}
      topics: {{ some: {{ slug: {{ equals: $slug }} }} }}"""


def _where_topic_polls_status(status_token: str) -> str:
    return f"""
      status: {{ equals: "{status_token}" }}
      topics: {{ some: {{ slug: {{ equals: $slug }} }} }}
      NOT: [{{ poll: null }}]"""


def _build_query_topic_popular(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token)
    return f"""
query TopicPopular($slug: String!, $take: Int!) {{
  posts(
    where: {{
{w}
    }}
    orderBy: [{{ commentCount: desc }}, {{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(
    where: {{
{w}
    }}
  )
}}
"""


def _build_query_topic_latest(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token)
    return f"""
query TopicLatest($slug: String!, $take: Int!) {{
  posts(
    where: {{
{w}
    }}
    orderBy: [{{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(
    where: {{
{w}
    }}
  )
}}
"""


def _build_query_topic_polls(status_token: str) -> str:
    w = _where_topic_polls_status(status_token)
    return f"""
query TopicPolls($slug: String!, $take: Int!) {{
  posts(
    where: {{
{w}
    }}
    orderBy: [{{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(
    where: {{
{w}
    }}
  )
}}
"""


def _topic_payload_from_gql(
    data: Dict[str, Any],
    *,
    generated_at: str,
    topic_row: Dict[str, Any],
) -> Dict[str, Any]:
    posts = data.get("posts") or []
    raw_count = data.get("postsCount")
    if raw_count is None:
        posts_count = len(posts) if isinstance(posts, list) else 0
    else:
        posts_count = int(raw_count)
    return {
        "generatedAt": generated_at,
        "topic": {
            "id": topic_row.get("id"),
            "name": topic_row.get("name"),
            "slug": topic_row.get("slug"),
        },
        "postsCount": posts_count,
        "posts": posts,
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
    每個 topic（需有 slug）依前端相同 GQL 寫入三個 JSON：

    - ``{{slug}}-latest.json``：TopicLatest
    - ``{{slug}}-pop.json``：TopicPopular（commentCount 熱門）
    - ``{{slug}}-polls.json``：TopicPolls（有 poll）

    ``posts`` 為 GQL 原始結構，不做欄位轉換。JSON 含 ``generatedAt``、``topic``、``postsCount``、``posts``。

    ``scan_multiplier`` 保留參數相容舊呼叫端，目前不影響 take（與前端對齊，take = ``per_topic_limit`` 並受 ``GQL_POST_MAX_TAKE`` 上限）。
    """
    _ = scan_multiplier  # 保留 API，與前端單次 take 對齊故不使用

    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if per_topic_limit <= 0:
        raise ValueError("per_topic_limit 必須大於 0")
    if scan_multiplier <= 0:
        raise ValueError("scan_multiplier 必須大於 0")

    status_token = _resolve_post_status(post_state)
    take = min(per_topic_limit, _max_take_per_request())

    q_popular = _build_query_topic_popular(status_token)
    q_latest = _build_query_topic_latest(status_token)
    q_polls = _build_query_topic_polls(status_token)

    data_topics = execute_gql(QUERY_TOPICS_META, None)
    topics = data_topics.get("topics") or []

    jobs: List[Tuple[str, Dict[str, Any], str, str]] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    for t in topics:
        slug = str(t.get("slug") or "").strip()
        if not slug:
            continue
        jobs.append((slug, t, "latest", q_latest))
        jobs.append((slug, t, "pop", q_popular))
        jobs.append((slug, t, "polls", q_polls))

    results: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if jobs:
        max_workers = min(24, max(4, len(jobs)))

        def _run(job: Tuple[str, Dict[str, Any], str, str]) -> Tuple[str, str, Dict[str, Any]]:
            slug, topic_row, kind, q = job
            client = get_thread_local_gql_client()
            gql_data = execute_gql(
                q, {"slug": slug, "take": take}, client=client
            )
            payload = _topic_payload_from_gql(
                gql_data, generated_at=generated_at, topic_row=topic_row
            )
            return slug, kind, payload

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_run, j) for j in jobs]
            for fut in as_completed(futs):
                slug, kind, payload = fut.result()
                results[(slug, kind)] = payload

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    def _under_base(name: str) -> str:
        return f"{base_dir}/{name}" if base_dir else name

    uploaded_paths: List[str] = []
    used_file_stems: Set[str] = set()

    for t in topics:
        slug = str(t.get("slug") or "").strip()
        if not slug:
            continue
        tid = str(t.get("id") or "").strip()
        stem = _topic_slug_for_path(t)
        if stem in used_file_stems and tid:
            stem = f"{stem}-{tid[:8]}"
        used_file_stems.add(stem)

        for kind, suffix in (("latest", "latest"), ("pop", "pop"), ("polls", "polls")):
            payload = results.get((slug, kind))
            if payload is None:
                continue
            object_name = f"{stem}-{suffix}.json"
            object_path = _under_base(object_name)
            _upload_json(bucket, object_path, payload)
            uploaded_paths.append(object_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "topics_count": len(topics),
        "topics_exported_with_slug": len({j[0] for j in jobs}) if jobs else 0,
        "per_topic_limit": per_topic_limit,
        "take_used": take,
        "post_state": status_token,
    }
