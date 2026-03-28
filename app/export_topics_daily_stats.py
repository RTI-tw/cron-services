"""
匯出各 topic 在指定時區「當日」的已發佈新文章數，寫成單一 JSON 上傳 GCS。

說明：Keystone Topic 目前無 isActive 欄位，此處「所有 topic」與 export_topic_posts 一致；
     文章「active」語意對應 Post.status = published。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _resolve_post_status, _upload_json
from .keystone_gql import execute_gql


def _build_topics_daily_query(status_enum_token: str) -> str:
    return f"""
query TopicsDailyPostCounts($start: DateTime!, $end: DateTime!) {{
  topics(orderBy: {{ sortOrder: asc }}) {{
    id
    name
    slug
    sortOrder
    language
    name_zh
    name_en
    name_vi
    name_id
    name_th
    description
    posts(
      where: {{
        status: {{ equals: {status_enum_token} }}
        createdAt: {{ gte: $start, lt: $end }}
      }}
    ) {{
      id
    }}
  }}
}}
"""


def _local_day_bounds(
    tz_name: str,
    day: date,
) -> tuple[datetime, datetime]:
    """回傳 [day_start, day_end_exclusive)，皆為 aware datetime。"""
    try:
        tz = ZoneInfo(tz_name)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"無效的時區: {tz_name}") from e
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def export_topics_daily_stats_to_gcs(
    *,
    prefix: str = "exports/topic-daily-stats",
    timezone_name: str = "Asia/Taipei",
    local_date_str: str | None = None,
    post_state: str = "active",
) -> Dict[str, Any]:
    """
    產出單一檔案 topics-daily.json：每個 topic 含當日新文章數（依 Post.createdAt 與 status）。
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    status_token = _resolve_post_status(post_state)

    if local_date_str:
        try:
            target_day = date.fromisoformat(local_date_str.strip())
        except ValueError as e:
            raise ValueError("local_date 須為 YYYY-MM-DD") from e
    else:
        tz = ZoneInfo(timezone_name)
        target_day = datetime.now(tz).date()

    day_start, day_end_excl = _local_day_bounds(timezone_name, target_day)
    start_iso = day_start.isoformat()
    end_iso = day_end_excl.isoformat()

    query = _build_topics_daily_query(status_token)
    data = execute_gql(
        query,
        {"start": start_iso, "end": end_iso},
    )
    topics_raw: List[Dict[str, Any]] = data.get("topics") or []

    topics_out: List[Dict[str, Any]] = []
    for t in topics_raw:
        posts = t.get("posts") or []
        topic_row = {
            "id": t.get("id"),
            "name": t.get("name"),
            "slug": t.get("slug"),
            "sortOrder": t.get("sortOrder"),
            "language": t.get("language"),
            "name_zh": t.get("name_zh"),
            "name_en": t.get("name_en"),
            "name_vi": t.get("name_vi"),
            "name_id": t.get("name_id"),
            "name_th": t.get("name_th"),
            "description": t.get("description"),
            "postsCreatedTodayCount": len(posts),
        }
        topics_out.append(topic_row)

    export_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = _normalize_prefix(prefix)
    base_dir = f"{safe_prefix}/{export_ts}" if safe_prefix else export_ts
    object_path = f"{base_dir}/topics-daily.json"

    payload: Dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "timezone": timezone_name,
        "localDate": target_day.isoformat(),
        "window": {
            "start": start_iso,
            "endExclusive": end_iso,
        },
        "postState": status_token,
        "topicsCount": len(topics_out),
        "topics": topics_out,
    }

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "topics_count": len(topics_out),
        "local_date": target_day.isoformat(),
        "timezone": timezone_name,
        "post_state": status_token,
    }
