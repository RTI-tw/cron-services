from typing import Any, Optional


_GCS_PUBLIC_URL_PREFIX = "https://storage.googleapis.com/"


def rewrite_gcs_public_urls(
    value: Any,
    *,
    bucket_name: Optional[str],
    web_url_base: Optional[str],
) -> Any:
    bucket = (bucket_name or "").strip().strip("/")
    target_base = (web_url_base or "").strip().rstrip("/")
    if not bucket or not target_base:
        return value

    source_prefix = f"{_GCS_PUBLIC_URL_PREFIX}{bucket}/"

    def rewrite_nested(item: Any) -> Any:
        if isinstance(item, str):
            if item.startswith(source_prefix):
                return f"{target_base}/{item[len(source_prefix):]}"
            return item
        if isinstance(item, list):
            return [rewrite_nested(child) for child in item]
        if isinstance(item, dict):
            return {key: rewrite_nested(child) for key, child in item.items()}
        return item

    return rewrite_nested(value)
