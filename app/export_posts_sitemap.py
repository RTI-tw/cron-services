import os
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Tuple
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


def _post_url_entries(post: Dict[str, Any], base_url: str, url_template: str) -> List[Tuple[str, str, Dict[str, str]]]:
    alternates = {
        lang: _post_url(base_url, url_template, lang, post)
        for lang in LANGUAGES
    }
    lastmod = _post_lastmod(post)
    return [(alternates[lang], lastmod, alternates) for lang in LANGUAGES]


def _chunk_post_url_entries(
    posts: List[Dict[str, Any]],
    base_url: str,
    url_template: str,
    max_urls_per_file: int,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    chunks: List[List[Tuple[str, str, Dict[str, str]]]] = []
    current: List[Tuple[str, str, Dict[str, str]]] = []

    for post in posts:
        entries = _post_url_entries(post, base_url, url_template)
        if current and len(current) + len(entries) > max_urls_per_file:
            chunks.append(current)
            current = []
        current.extend(entries)

    if current:
        chunks.append(current)
    return chunks


def _build_sitemap_xml(entries: List[Tuple[str, str, Dict[str, str]]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]

    for loc, lastmod, alternates in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(loc)}</loc>")
        lines.append(f"    <lastmod>{escape(lastmod)}</lastmod>")
        for alt_lang, href in alternates.items():
            lines.append(
                f'    <xhtml:link rel="alternate" hreflang="{alt_lang}" href="{escape(href)}" />'
            )
        lines.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{escape(alternates["zh"])}" />')
        lines.append("  </url>")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def _build_sitemap_index_xml(sitemap_urls: List[Tuple[str, str]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, lastmod in sitemap_urls:
        lines.append("  <sitemap>")
        lines.append(f"    <loc>{escape(loc)}</loc>")
        lines.append(f"    <lastmod>{escape(lastmod)}</lastmod>")
        lines.append("  </sitemap>")
    lines.append("</sitemapindex>")
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
    max_urls_per_file: int = 50000,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if page_size <= 0:
        raise ValueError("page_size 必須大於 0")
    if max_urls_per_file <= 0 or max_urls_per_file > 50000:
        raise ValueError("max_urls_per_file 必須介於 1 到 50000")
    if max_urls_per_file < len(LANGUAGES):
        raise ValueError(f"max_urls_per_file 必須至少為 {len(LANGUAGES)}，才能容納一篇 post 的五語 URL")

    normalized_base_url = _normalize_base_url(base_url)
    posts, total_count = _fetch_published_posts(page_size)
    chunks = _chunk_post_url_entries(
        posts,
        normalized_base_url,
        url_template,
        max_urls_per_file,
    )

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    now_iso = datetime.now(timezone.utc).isoformat()
    uploaded_paths: List[str] = []
    sitemap_index_entries: List[Tuple[str, str]] = []

    for idx, chunk in enumerate(chunks, start=1):
        filename = f"posts-sitemap-{idx}.xml"
        object_path = f"{base_dir}/{filename}" if base_dir else filename
        sitemap_xml = _build_sitemap_xml(chunk)
        bucket.blob(object_path).upload_from_string(
            sitemap_xml,
            content_type="application/xml; charset=utf-8",
        )
        uploaded_paths.append(object_path)
        sitemap_index_entries.append((f"{normalized_base_url}/{object_path}", now_iso))

    index_path = f"{base_dir}/sitemap.xml" if base_dir else "sitemap.xml"
    sitemap_index_xml = _build_sitemap_index_xml(sitemap_index_entries)
    bucket.blob(index_path).upload_from_string(
        sitemap_index_xml,
        content_type="application/xml; charset=utf-8",
    )
    uploaded_paths.insert(0, index_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "posts_count": len(posts),
        "posts_total_count": total_count,
        "url_count": len(posts) * len(LANGUAGES),
        "sitemap_files_count": len(chunks),
        "max_urls_per_file": max_urls_per_file,
        "base_url": normalized_base_url,
        "url_template": url_template,
    }
