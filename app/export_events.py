from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

QUERY_HOMEPAGE_EVENTS = """
query GetHomepageEventsForExport($now: DateTime!) {
  events(
    where: {
      isPromoted: { equals: true }
      post: { status: { equals: published } }
      OR: [{ endAt: { equals: null } }, { endAt: { gte: $now } }]
    }
  ) {
    id
    slug
    label
    isPromoted
    startAt
    endAt
    registrationStartAt
    registrationEndAt
    capacity
    registrationCount: registrationsCount(
      where: { status: { in: [registered, checkedIn] } }
    )
    post {
      title
      title_zh
      title_en
      title_vi
      title_id
      title_th
      heroImages(orderBy: { sortOrder: asc }, take: 1) {
        id
        urlOriginal
        altText
        file {
          url
        }
      }
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


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pick_homepage_event(
    events: List[Dict[str, Any]],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    dated_events = [
        (event, start_at)
        for event in events
        if event.get("isPromoted") is True
        and (start_at := _parse_datetime(event.get("startAt"))) is not None
    ]
    if not dated_events:
        return None
    return min(
        dated_events,
        key=lambda item: (abs((item[1] - now).total_seconds()), str(item[0].get("id") or "")),
    )[0]


def _build_homepage_payload(
    events: List[Dict[str, Any]],
    generated_at: str,
) -> Dict[str, Any]:
    now = _parse_datetime(generated_at) or datetime.now(timezone.utc)
    selected = _pick_homepage_event(events, now)
    if selected is None:
        return {"generatedAt": generated_at, "event": None}

    event = dict(selected)
    post = event.pop("post", None) or {}
    images = post.get("heroImages") or []
    first_image = None
    if images:
        first_image = dict(images[0])
        file_data = first_image.pop("file", None) or {}
        first_image["urlOriginal"] = first_image.get("urlOriginal") or file_data.get("url")

    event.update(
        {
            "title": post.get("title"),
            "title_zh": post.get("title_zh"),
            "title_en": post.get("title_en"),
            "title_vi": post.get("title_vi"),
            "title_id": post.get("title_id"),
            "title_th": post.get("title_th"),
            "firstImage": first_image,
        }
    )
    return {"generatedAt": generated_at, "event": event}


def export_events_to_gcs(
    *,
    prefix: str = "exports/events",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    輸出 previews.json 與 homepage.json，每次覆寫。

    不含 availabilityStatus 與 isRegistered，兩者由前端推導 / 疊加。
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    now_iso = datetime.now(timezone.utc).isoformat()
    data = execute_gql(QUERY_EVENT_PREVIEWS)
    payload = _build_events_payload(data.get("eventPreviews") or {}, now_iso)
    homepage_data = execute_gql(QUERY_HOMEPAGE_EVENTS, {"now": now_iso})
    homepage_payload = _build_homepage_payload(homepage_data.get("events") or [], now_iso)

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/previews.json" if base_dir else "previews.json"
    homepage_object_path = f"{base_dir}/homepage.json" if base_dir else "homepage.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload, cache_control_seconds)
    _upload_json(bucket, homepage_object_path, homepage_payload, cache_control_seconds)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path, homepage_object_path],
        "events_count": payload["eventsCount"],
        "homepage_event_id": (homepage_payload.get("event") or {}).get("id"),
        "sections": {section: len(payload[section]) for section in SECTIONS},
        "now": now_iso,
    }
