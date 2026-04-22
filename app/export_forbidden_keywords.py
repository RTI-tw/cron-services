from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _upload_json
from .keystone_gql import execute_gql

QUERY_FORBIDDEN_KEYWORDS = """
query ForbiddenKeywordsForJson(
  $where: ForbiddenKeywordWhereInput! = { isEnabled: { equals: true } }
  $orderBy: [ForbiddenKeywordOrderByInput!]! = [{ updatedAt: desc }, { id: asc }]
) {
  forbiddenKeywords(where: $where, orderBy: $orderBy) {
    id
    word
    language
    word_zh
    word_en
    word_vi
    word_id
    word_th
    exemptions
    updatedAt
  }
  forbiddenKeywordsCount(where: $where)
}
"""


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_exemptions(value: Any) -> List[str]:
    return [part.strip() for part in _normalize_text(value).split(",") if part.strip()]


def _build_keywords_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    keywords = []
    for item in items:
        keywords.append(
            {
                "id": str(item.get("id") or ""),
                "word": _normalize_text(item.get("word")),
                "language": item.get("language"),
                "translations": {
                    "zh": _normalize_text(item.get("word_zh")),
                    "en": _normalize_text(item.get("word_en")),
                    "vi": _normalize_text(item.get("word_vi")),
                    "id": _normalize_text(item.get("word_id")),
                    "th": _normalize_text(item.get("word_th")),
                },
                "exemptions": _parse_exemptions(item.get("exemptions")),
                "updatedAt": item.get("updatedAt"),
            }
        )

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "total": len(keywords),
        "keywords": keywords,
    }


def export_forbidden_keywords_to_gcs(
    *,
    prefix: str = "exports/forbidden-keywords",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    data = execute_gql(
        QUERY_FORBIDDEN_KEYWORDS,
        {
            "where": {"isEnabled": {"equals": True}},
            "orderBy": [{"updatedAt": "desc"}, {"id": "asc"}],
        },
    )
    items = data.get("forbiddenKeywords") or []
    payload = _build_keywords_payload(items)

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/keywords.json" if base_dir else "keywords.json"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    _upload_json(bucket, object_path, payload, cache_control_seconds)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "keywords_count": payload["total"],
        "source_count": data.get("forbiddenKeywordsCount"),
    }
