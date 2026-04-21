import os
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List
from urllib.parse import quote

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _normalize_prefix, _to_int
from .keystone_gql import execute_gql

LANGUAGES = ("zh", "en", "vi", "id", "th")

QUERY_PUBLISHED_POSTS_FOR_SITEMAP = """
query PublishedPostsForSitemap($skip: Int!, $take: Int!) {
  posts(
    where: { status: { equals: published } }
    orderBy: [{ published_date: desc }, { createdAt: desc }]
    skip: $skip
    take: $take
  ) {
    id
    published_date
    updatedAt
    createdAt
  }
  postsCount(where: { status: { equals: published } })
}
"""


def _normalize_base_url(base_url: str) -> str:
    value = (base_url or os.getenv("SITE_BASE_URL") or "").strip()
    if not value:
        raise ValueError("base_url 未提供，且 SITE_BASE_URL 環境變數未設定")
    return value.rstrip("/")


def _post_lastmod(post: Dict[str, Any]) -> str:
    for key in ("updatedAt", "published_date", "createdAt"):
        value = str(post.get(key) or "").strip()
        if value:
            return value
    return datetime.now(timezone.utc).isoformat()


def _post_url(base_url: str, url_template: str, lang: str, post: Dict[str, Any]) -> str:
    post_id = quote(str(post.get("id") or "").strip(), safe="")
    path = (url_template or "/{lang}/posts/{id}").format(lang=lang, id=post_id)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _build_sitemap_xml(posts: List[Dict[str, Any]], base_url: str, url_template: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]

    for post in posts:
        alternates = {
            lang: _post_url(base_url, url_template, lang, post)
            for lang in LANGUAGES
        }
        lastmod = escape(_post_lastmod(post))
        for lang in LANGUAGES:
            lines.append("  <url>")
            lines.append(f"    <loc>{escape(alternates[lang])}</loc>")
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
            for alt_lang, href in alternates.items():
                lines.append(
                    f'    <xhtml:link rel="alternate" hreflang="{alt_lang}" href="{escape(href)}" />'
                )
            lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{escape(alternates["zh"])}" />')
            lines.append("  </url>")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _fetch_published_posts(page_size: int) -> tuple[List[Dict[str, Any]], int]:
    posts: List[Dict[str, Any]] = []
    total_count = 0
    skip = 0

    while True:
        data = execute_gql(
            QUERY_PUBLISHED_POSTS_FOR_SITEMAP,
            {"skip": skip, "take": page_size},
        )
        page_posts = data.get("posts") or []
        total_count = _to_int(data.get("postsCount"))
        if not page_posts:
            break
        posts.extend(page_posts)
        skip += len(page_posts)
        if len(page_posts) < page_size:
            break

    return posts, total_count


def export_posts_sitemap_to_gcs(
    *,
    prefix: str = "exports/sitemaps",
    base_url: str = "",
    url_template: str = "/{lang}/posts/{id}",
    page_size: int = 200,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if page_size <= 0:
        raise ValueError("page_size 必須大於 0")

    normalized_base_url = _normalize_base_url(base_url)
    posts, total_count = _fetch_published_posts(page_size)
    sitemap_xml = _build_sitemap_xml(posts, normalized_base_url, url_template)

    base_dir = _normalize_prefix(prefix)
    object_path = f"{base_dir}/sitemap.xml" if base_dir else "sitemap.xml"

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_path)
    blob.upload_from_string(sitemap_xml, content_type="application/xml; charset=utf-8")

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": [object_path],
        "posts_count": len(posts),
        "posts_total_count": total_count,
        "url_count": len(posts) * len(LANGUAGES),
        "base_url": normalized_base_url,
        "url_template": url_template,
    }
