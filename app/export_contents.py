import json
from datetime import datetime, timezone
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

QUERY_CONTENT_BY_ID = """
query GetContentById($id: ID!) {
  content(where: { id: $id }) {
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
    content_id: str | None = None,
) -> Dict[str, Any]:
    """
    透過 Keystone GraphQL 取得全部 contents，逐筆上傳為獨立 JSON 檔案到 GCS。
    """
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if page_size <= 0:
        raise ValueError("page_size 必須大於 0")

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    safe_prefix = _normalize_prefix(prefix)
    export_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = f"{safe_prefix}/{export_ts}" if safe_prefix else export_ts

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
        object_path = f"{base_dir}/{file_stem}-{item_id}.json"

        payload = json.dumps(row, ensure_ascii=False, indent=2)
        blob = bucket.blob(object_path)
        blob.upload_from_string(payload, content_type="application/json; charset=utf-8")

        uploaded_paths.append(object_path)
        total += 1

    target_id = (content_id or "").strip()
    if target_id:
        data = execute_gql(QUERY_CONTENT_BY_ID, {"id": target_id})
        row = data.get("content")
        if not row:
            raise ValueError(f"content id={target_id} 不存在")
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
