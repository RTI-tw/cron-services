from typing import Any, Dict, Optional

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _upload_json
from .keystone_gql import execute_gql

QUERY_SIDEBAR_TOPICS = """
query GetTopics(
  $where: TopicWhereInput! = {}
  $orderBy: [TopicOrderByInput!]! = [{ sortOrder: asc }]
  $take: Int
  $skip: Int! = 0
) {
  topics(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    name
    name_zh
    name_en
    name_vi
    name_id
    name_th
    slug
    sortOrder
    description
    postsCount
    todayPostsCount
  }
  topicsCount(where: $where)
}
"""


def export_sidebar_topics_to_gcs(
    *,
    prefix: str = "exports/sidebar-topics",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    data = execute_gql(
        QUERY_SIDEBAR_TOPICS,
        {
            "where": {"state": {"equals": "active"}},
            "orderBy": [{"sortOrder": "asc"}],
            "skip": 0,
        },
    )

    payload = {
        "topics": data.get("topics") or [],
        "topicsCount": data.get("topicsCount") or 0,
    }

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/topics.json" if base_dir else "topics.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload, cache_control_seconds)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "topics_count": payload["topicsCount"],
    }
