from datetime import datetime, timezone
from typing import Any, Dict, Optional

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _upload_json
from .keystone_gql import execute_gql

# Two fields the resolver returns are deliberately not selected here.
#
# `availabilityStatus` is derived from `now`, not stored. Exporting it would
# freeze the 已截止 / 尚未開始 transitions at export time, so an event whose
# registration closed four minutes ago would still advertise 立即報名 on the
# homepage. The timestamps and capacity below are everything needed to recompute
# it, and the frontend does so with the viewer's clock.
#
# `isRegistered` is per-member. This exporter runs as a service account, so the
# value would be `false` for every reader. The frontend overlays it from
# myEventRegistrations once someone signs in.
QUERY_EVENT_PREVIEWS = """
fragment EventPreviewExportFields on EventPreviewItemResult {
  id
  slug
  label
  title
  title_zh
  title_en
  title_vi
  title_id
  title_th
  startAt
  endAt
  registrationStartAt
  registrationEndAt
  capacity
  registrationCount
  firstImage {
    id
    urlOriginal
    altText
  }
}

query GetEventPreviewsForExport {
  eventPreviews {
    hot {
      ...EventPreviewExportFields
    }
    more {
      ...EventPreviewExportFields
    }
    past {
      ...EventPreviewExportFields
    }
  }
}
"""

SECTIONS = ("hot", "more", "past")


def _build_events_payload(
    previews: Dict[str, Any],
    generated_at: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"generatedAt": generated_at}
    for section in SECTIONS:
        payload[section] = previews.get(section) or []
    payload["eventsCount"] = sum(len(payload[section]) for section in SECTIONS)
    return payload


def export_events_to_gcs(
    *,
    prefix: str = "exports/events",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    輸出 previews.json：活動預覽卡（hot / more / past 三段），每次覆寫。

    不含 availabilityStatus 與 isRegistered，兩者由前端推導 / 疊加。
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    now_iso = datetime.now(timezone.utc).isoformat()
    data = execute_gql(QUERY_EVENT_PREVIEWS)
    payload = _build_events_payload(data.get("eventPreviews") or {}, now_iso)

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/previews.json" if base_dir else "previews.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload, cache_control_seconds)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "events_count": payload["eventsCount"],
        "sections": {section: len(payload[section]) for section in SECTIONS},
        "now": now_iso,
    }
