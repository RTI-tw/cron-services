from datetime import datetime, timezone
from typing import Any, Dict, Optional

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _upload_json
from .keystone_gql import execute_gql

QUERY_ACTIVE_ADS = """
fragment PhotoFields on Photo {
  id
  name
  resized {
    original
    w480
    w800
    w1200
  }
  resizedWebp {
    original
    w480
    w800
    w1200
  }
  urlOriginal
  altText
}

query GetActiveAds($where: AdWhereInput! = {}, $take: Int) {
  ads(where: $where, take: $take) {
    id
    title
    status
    startAt
    endAt
    image {
      ...PhotoFields
    }
    videoUrl
    linkUrl
  }
}
"""


def export_active_ads_to_gcs(
    *,
    prefix: str = "exports/ads",
    take: int = 1,
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if take <= 0:
        raise ValueError("take 必須大於 0")

    now_iso = datetime.now(timezone.utc).isoformat()
    data = execute_gql(
        QUERY_ACTIVE_ADS,
        {
            "where": {
                "status": {"equals": "active"},
                "startAt": {"lte": now_iso},
                "endAt": {"gte": now_iso},
            },
            "take": take,
        },
    )
    payload = {"ads": data.get("ads") or []}

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/ads.json" if base_dir else "ads.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload, cache_control_seconds)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "ads_count": len(payload["ads"]),
        "take": take,
        "now": now_iso,
    }
