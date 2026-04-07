import json
from typing import Any, Dict, List

from google.cloud import storage

from .config import get_settings
from .keystone_gql import execute_gql

QUERY_CONTENTS = """
query ListContents($skip: Int!, $take: Int!) {
  contents(skip: $skip, take: $take) {
    id
    identifier
    content
    language
    content_zh
    content_en
    content_vi
    content_th
    content_id
    createdAt
    updatedAt
  }
}
"""

QUERY_CONTENT_BY_IDENTIFIER = """
query GetContentByIdentifier($identifier: String!) {
  content(where: { identifier: { equals: $identifier } }) {
    id
    identifier
    content
    language
    content_zh
    content_en
    content_vi
    content_th
    content_id
    createdAt
    updatedAt
  }
}
"""


def _normalize_prefix(prefix: str) -> str:
    p = (prefix or "").strip().strip("/")
    return p


def export_all_contents_to_gcs(
    *,
    prefix: str = "exports/contents",
    page_size: int = 200,
    content_slug: str | None = None,
) -> Dict[str, Any]:
    """
    透過 Keystone GraphQL 取得全部 contents，逐筆上傳為獨立 JSON 檔案到 GCS。
    物件路徑為 ``{prefix}/{slug}.json``（slug 即 Keystone ``identifier``；若缺則退回 ``{id}.json``），每次執行覆寫同一路徑。
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if page_size <= 0:
        raise ValueError("page_size 必須大於 0")

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    base_dir = _normalize_prefix(prefix)

    uploaded_paths: List[str] = []
    total = 0
    skip = 0

    def upload_row(row: Dict[str, Any]) -> None:
        nonlocal total
        item_id = str(row.get("id") or "").strip()
        if not item_id:
            return

        identifier = str(row.get("identifier") or "").strip()
        file_stem = identifier if identifier else item_id
        # 避免路徑分隔符影響物件路徑
        file_stem = file_stem.replace("/", "_")
        stem = f"{file_stem}.json"
        object_path = f"{base_dir}/{stem}" if base_dir else stem

        payload = json.dumps(row, ensure_ascii=False, indent=2)
        blob = bucket.blob(object_path)
        blob.upload_from_string(payload, content_type="application/json; charset=utf-8")

        uploaded_paths.append(object_path)
        total += 1

    target_slug = (content_slug or "").strip()
    if target_slug:
        data = execute_gql(QUERY_CONTENT_BY_IDENTIFIER, {"identifier": target_slug})
        row = data.get("content")
        if not row:
            raise ValueError(f"content slug (identifier)={target_slug!r} 不存在")
        upload_row(row)
    else:
        while True:
            data = execute_gql(QUERY_CONTENTS, {"skip": skip, "take": page_size})
            rows = data.get("contents") or []
            if not rows:
                break

            for row in rows:
                upload_row(row)

            skip += len(rows)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "total_exported": total,
        "sample_paths": uploaded_paths[:20],
    }
