from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _upload_json
from .keystone_gql import execute_gql

_FOOTER_QUERY_VARIANTS: List[Tuple[str, Dict[str, Any]]] = [
(
    """
query GetFooterContents($orderBy: [ContentOrderByInput!]! = [{ sortOrder: asc }]) {
  contents(orderBy: $orderBy) {
    id
    slug
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    order: sortOrder
    status
  }
  contentsCount
}
""",
    {"orderBy": [{"sortOrder": "asc"}]},
),
(
    """
query GetFooterContents($orderBy: [ContentOrderByInput!]! = [{ sortOrder: asc }]) {
  contents(orderBy: $orderBy) {
    id
    slug: identifier
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    order: sortOrder
    status
  }
  contentsCount
}
""",
    {"orderBy": [{"sortOrder": "asc"}]},
),
(
    """
query GetFooterContents($orderBy: [ContentOrderByInput!]! = [{ order: asc }]) {
  contents(orderBy: $orderBy) {
    id
    slug: identifier
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    order
    status
  }
  contentsCount
}
""",
    {"orderBy": [{"order": "asc"}]},
),
]

_EDITOR_CHOICES_QUERY = """
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

query GetEditorChoices(
  $orderBy: [EditorChoiceOrderByInput!]! = [{ sortOrder: asc }]
  $take: Int
  $skip: Int! = 0
) {
  editorChoices(orderBy: $orderBy, take: $take, skip: $skip) {
    id
    sortOrder
    post {
      id
      status
      title
      title_zh
      title_en
      title_vi
      title_id
      title_th
      content
      createdAt
      heroImages {
        ...PhotoFields
      }
      commentsCount
      reactionsCount
      author {
        id
        name
        nickname
      }
    }
  }
  editorChoicesCount
}
"""

_POP_POLLS_QUERY = """
query GetPolls(
  $where: PollWhereInput! = {}
  $orderBy: [PollOrderByInput!]! = [{ createdAt: desc }]
  $take: Int
  $skip: Int! = 0
) {
  polls(where: $where, orderBy: $orderBy, take: $take, skip: $skip) {
    id
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    expiresAt
    totalVotes
    post {
      id
    }
    options {
      id
      text
      text_zh
      text_en
      text_vi
      text_id
      text_th
      voteCount
    }
  }
  pollsCount(where: $where)
}
"""


def _execute_first_supported_query(
    query_attempts: List[Tuple[str, Optional[Dict[str, Any]]]],
) -> Tuple[Dict[str, Any], int]:
    """
    依序嘗試多個等價 query，避免不同 Keystone schema 欄位命名造成整體匯出失敗。
    """
    last_err: Optional[RuntimeError] = None
    for idx, (q, variables) in enumerate(query_attempts, start=1):
        try:
            return execute_gql(q, variables), idx
        except RuntimeError as exc:
            last_err = exc
            if "Cannot query field" in str(exc):
                continue
            raise
    if last_err is not None:
        raise RuntimeError(f"Footer query 無可用欄位組合: {last_err}") from last_err
    raise RuntimeError("Footer query 設定錯誤：未提供 query")


def _upload_home_payloads(
    *,
    prefix: str,
    payloads: Dict[str, Dict[str, Any]],
    cache_control_seconds: Optional[int] = None,
) -> List[str]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    uploaded_paths: List[str] = []
    for filename, payload in payloads.items():
        object_path = f"{base_dir}/{filename}" if base_dir else filename
        _upload_json(bucket, object_path, payload, cache_control_seconds)
        uploaded_paths.append(object_path)
    return uploaded_paths


def _build_home_payloads(*, include: Set[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    now_iso = datetime.now(timezone.utc).isoformat()
    payloads: Dict[str, Dict[str, Any]] = {}
    meta: Dict[str, Any] = {"generated_at": now_iso}

    if "footer" in include:
        footer_data, footer_query_variant = _execute_first_supported_query(_FOOTER_QUERY_VARIANTS)
        payloads["footer.json"] = footer_data
        meta["footer_query_variant"] = footer_query_variant

    if "editor-choices" in include:
        editor_choices_data = execute_gql(
            _EDITOR_CHOICES_QUERY,
            {"orderBy": [{"sortOrder": "asc"}], "take": 40, "skip": 0},
        )
        payloads["editor-choices.json"] = editor_choices_data
        meta["editor_choices_take"] = 40

    if "pop-polls" in include:
        pop_polls_data = execute_gql(
            _POP_POLLS_QUERY,
            {
                "take": 3,
                "skip": 0,
                "where": {"expiresAt": {"gt": now_iso}},
                "orderBy": [{"totalVotes": "desc"}],
            },
        )
        payloads["pop-polls.json"] = pop_polls_data
        meta["pop_polls_take"] = 3

    return payloads, meta


def export_home_sections_to_gcs(
    *,
    prefix: str = "exports/home-sections",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")

    base_dir = _normalize_prefix(prefix)
    payloads, meta = _build_home_payloads(
        include={"footer", "editor-choices", "pop-polls"},
    )
    uploaded_paths = _upload_home_payloads(
        prefix=prefix,
        payloads=payloads,
        cache_control_seconds=cache_control_seconds,
    )

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        **meta,
    }


def export_home_editor_choices_to_gcs(
    *,
    prefix: str = "exports/home-sections",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    payloads, meta = _build_home_payloads(include={"editor-choices"})
    uploaded_paths = _upload_home_payloads(
        prefix=prefix,
        payloads=payloads,
        cache_control_seconds=cache_control_seconds,
    )
    return {
        "bucket": get_settings().gcs_bucket,
        "prefix": _normalize_prefix(prefix),
        "files": uploaded_paths,
        **meta,
    }


def export_home_pop_polls_to_gcs(
    *,
    prefix: str = "exports/home-sections",
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    payloads, meta = _build_home_payloads(include={"pop-polls"})
    uploaded_paths = _upload_home_payloads(
        prefix=prefix,
        payloads=payloads,
        cache_control_seconds=cache_control_seconds,
    )
    return {
        "bucket": get_settings().gcs_bucket,
        "prefix": _normalize_prefix(prefix),
        "files": uploaded_paths,
        **meta,
    }
