import os
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from google.cloud import storage

from .config import get_settings
from .export_topic_posts import _apply_cache_control, _normalize_prefix, _to_int
from .keystone_gql import execute_gql

LANGUAGES = ("zh", "en", "vi", "id", "th")
# The frontend's browsable list pages. Every route here must stay in step with
# app/[lang]/(feed)/ — the app serves no sitemap of its own, so a route missing
# from this tuple is a route missing from the sitemap.
STATIC_PAGE_PATH_TEMPLATES = (
    "/{lang}",
    "/{lang}/topics",
    "/{lang}/editors-pick",
    "/{lang}/life-guide",
    "/{lang}/rti-choice",
    "/{lang}/events",
)
BASE_URL_ENV_VARS = (
    "SITE_BASE_URL",
    "PUBLIC_SITE_URL",
    "FRONTEND_BASE_URL",
    "BASE_URL",
)

# A post that backs an event 307-redirects to /{lang}/events/{slug}, so listing it
# here would fill the sitemap with URLs that never answer 200. The event itself is
# listed instead, by QUERY_EVENTS_FOR_SITEMAP.
QUERY_PUBLISHED_POSTS_FOR_SITEMAP = """
query PublishedPostsForSitemap($skip: Int!, $take: Int!) {
  posts(
    where: { status: { equals: published }, events: { none: {} } }
    orderBy: [{ published_date: desc }, { createdAt: desc }]
    skip: $skip
    take: $take
  ) {
    id
    published_date
    updatedAt
    createdAt
  }
  postsCount(where: { status: { equals: published }, events: { none: {} } })
}
"""

# Event detail pages are server-rendered and carry their own title, description and
# Event JSON-LD, but nothing linked to them and nothing listed them, so they were
# invisible to a crawler. An event with no published post 404s, hence the filter.
QUERY_EVENTS_FOR_SITEMAP = """
query EventsForSitemap($skip: Int!, $take: Int!) {
  events(
    where: { post: { status: { equals: published } } }
    orderBy: [{ startAt: desc }]
    skip: $skip
    take: $take
  ) {
    slug
    updatedAt
    post {
      updatedAt
    }
  }
  eventsCount(where: { post: { status: { equals: published } } })
}
"""

# An inactive topic has no browsable page (the post cards hide its chip), so only
# active ones belong here.
QUERY_TOPICS_FOR_SITEMAP = """
query TopicsForSitemap($skip: Int!, $take: Int!) {
  topics(
    where: { state: { equals: active } }
    orderBy: [{ sortOrder: asc }, { id: asc }]
    skip: $skip
    take: $take
  ) {
    slug
    updatedAt
  }
  topicsCount(where: { state: { equals: active } })
}
"""

QUERY_PUBLISHED_CONTENTS_FOR_SITEMAP = """
query PublishedContentsForSitemap($skip: Int!, $take: Int!) {
  contents(
    where: { status: { equals: published } }
    orderBy: [{ updatedAt: desc }]
    skip: $skip
    take: $take
  ) {
    identifier
    updatedAt
  }
  contentsCount(where: { status: { equals: published } })
}
"""


def _normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip()
    if not value:
        for env_name in BASE_URL_ENV_VARS:
            value = (os.getenv(env_name) or "").strip()
            if value:
                break
    if not value:
        env_names = " / ".join(BASE_URL_ENV_VARS)
        raise ValueError(f"base_url 未提供，且環境變數 {env_names} 皆未設定")
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


def _content_url(base_url: str, content_url_template: str, lang: str, content: Dict[str, Any]) -> str:
    identifier = quote(str(content.get("identifier") or "").strip(), safe="")
    path = (content_url_template or "/{lang}/content/{identifier}").format(
        lang=lang,
        identifier=identifier,
    )
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _static_page_url(base_url: str, path_template: str, lang: str) -> str:
    path = path_template.format(lang=lang)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _slug_url(base_url: str, url_template: str, lang: str, item: Dict[str, Any]) -> str:
    slug = quote(str(item.get("slug") or "").strip(), safe="")
    path = url_template.format(lang=lang, slug=slug)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _latest_lastmod(lastmods: List[str], fallback: str) -> str:
    """Newest of the given timestamps, compared as dates rather than as strings —
    Keystone mixes 'Z' and '+00:00' and those don't sort lexicographically."""
    parsed = [(dt, raw) for raw in lastmods if (dt := _parse_iso(raw)) is not None]
    if not parsed:
        return fallback
    return max(parsed, key=lambda pair: pair[0])[1]


def _event_lastmod(event: Dict[str, Any]) -> str:
    """An event page renders its post, so either changing is a change to the page."""
    post = event.get("post") or {}
    return _latest_lastmod(
        [str(event.get("updatedAt") or ""), str(post.get("updatedAt") or "")],
        datetime.now(timezone.utc).isoformat(),
    )


def _post_url_entries(post: Dict[str, Any], base_url: str, url_template: str) -> List[Tuple[str, str, Dict[str, str]]]:
    alternates = {
        lang: _post_url(base_url, url_template, lang, post)
        for lang in LANGUAGES
    }
    lastmod = _post_lastmod(post)
    return [(alternates[lang], lastmod, alternates) for lang in LANGUAGES]


def _content_url_entries(
    content: Dict[str, Any],
    base_url: str,
    content_url_template: str,
) -> List[Tuple[str, str, Dict[str, str]]]:
    alternates = {
        lang: _content_url(base_url, content_url_template, lang, content)
        for lang in LANGUAGES
    }
    lastmod = str(content.get("updatedAt") or datetime.now(timezone.utc).isoformat())
    return [(alternates[lang], lastmod, alternates) for lang in LANGUAGES]


def _static_page_url_entries(
    path_template: str,
    base_url: str,
    lastmod: str,
) -> List[Tuple[str, str, Dict[str, str]]]:
    alternates = {
        lang: _static_page_url(base_url, path_template, lang)
        for lang in LANGUAGES
    }
    return [(alternates[lang], lastmod, alternates) for lang in LANGUAGES]


def _chunk_sitemap_entries(
    entries_by_item: List[List[Tuple[str, str, Dict[str, str]]]],
    max_urls_per_file: int,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    chunks: List[List[Tuple[str, str, Dict[str, str]]]] = []
    current: List[Tuple[str, str, Dict[str, str]]] = []

    for entries in entries_by_item:
        if current and len(current) + len(entries) > max_urls_per_file:
            chunks.append(current)
            current = []
        current.extend(entries)

    if current:
        chunks.append(current)
    return chunks


def _build_post_entries_by_item(
    posts: List[Dict[str, Any]],
    base_url: str,
    url_template: str,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    return [
        _post_url_entries(post, base_url, url_template)
        for post in posts
    ]


def _build_static_page_entries_by_item(
    base_url: str,
    lastmod: str,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    return [
        _static_page_url_entries(path_template, base_url, lastmod)
        for path_template in STATIC_PAGE_PATH_TEMPLATES
    ]


def _slug_url_entries(
    item: Dict[str, Any],
    base_url: str,
    url_template: str,
    lastmod: str,
) -> List[Tuple[str, str, Dict[str, str]]]:
    alternates = {
        lang: _slug_url(base_url, url_template, lang, item)
        for lang in LANGUAGES
    }
    return [(alternates[lang], lastmod, alternates) for lang in LANGUAGES]


def _build_event_entries_by_item(
    events: List[Dict[str, Any]],
    base_url: str,
    event_url_template: str,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    return [
        _slug_url_entries(event, base_url, event_url_template, _event_lastmod(event))
        for event in events
    ]


def _build_topic_entries_by_item(
    topics: List[Dict[str, Any]],
    base_url: str,
    topic_url_template: str,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    fallback = datetime.now(timezone.utc).isoformat()
    return [
        _slug_url_entries(
            topic,
            base_url,
            topic_url_template,
            _latest_lastmod([str(topic.get("updatedAt") or "")], fallback),
        )
        for topic in topics
    ]


def _build_content_entries_by_item(
    contents: List[Dict[str, Any]],
    base_url: str,
    content_url_template: str,
) -> List[List[Tuple[str, str, Dict[str, str]]]]:
    return [
        _content_url_entries(content, base_url, content_url_template)
        for content in contents
    ]


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


def _fetch_all(
    query: str,
    list_key: str,
    count_key: str,
    page_size: int,
    keep=lambda item: True,
) -> tuple[List[Dict[str, Any]], int]:
    """Page through a list query until it runs dry. `page_size` is the page size, not
    a cap: every published item ends up in the sitemap."""
    items: List[Dict[str, Any]] = []
    total_count = 0
    skip = 0

    while True:
        data = execute_gql(query, {"skip": skip, "take": page_size})
        page = data.get(list_key) or []
        total_count = _to_int(data.get(count_key))
        if not page:
            break
        items.extend(item for item in page if keep(item))
        skip += len(page)
        if len(page) < page_size:
            break

    return items, total_count


def _has_slug(item: Dict[str, Any]) -> bool:
    return bool(str(item.get("slug") or "").strip())


def _fetch_published_posts(page_size: int) -> tuple[List[Dict[str, Any]], int]:
    return _fetch_all(QUERY_PUBLISHED_POSTS_FOR_SITEMAP, "posts", "postsCount", page_size)


def _fetch_published_contents(page_size: int) -> tuple[List[Dict[str, Any]], int]:
    return _fetch_all(
        QUERY_PUBLISHED_CONTENTS_FOR_SITEMAP,
        "contents",
        "contentsCount",
        page_size,
        keep=lambda c: bool(str(c.get("identifier") or "").strip()),
    )


def _fetch_events(page_size: int) -> tuple[List[Dict[str, Any]], int]:
    return _fetch_all(QUERY_EVENTS_FOR_SITEMAP, "events", "eventsCount", page_size, keep=_has_slug)


def _fetch_topics(page_size: int) -> tuple[List[Dict[str, Any]], int]:
    return _fetch_all(QUERY_TOPICS_FOR_SITEMAP, "topics", "topicsCount", page_size, keep=_has_slug)


def export_posts_sitemap_to_gcs(
    *,
    prefix: str = "exports/sitemaps",
    base_url: str = "",
    url_template: str = "/{lang}/posts/{id}",
    content_url_template: str = "/{lang}/content/{identifier}",
    event_url_template: str = "/{lang}/events/{slug}",
    topic_url_template: str = "/{lang}/topics/{slug}",
    page_size: int = 200,
    max_urls_per_file: int = 50000,
    cache_control_seconds: Optional[int] = None,
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
    contents, contents_total_count = _fetch_published_contents(page_size)
    events, events_total_count = _fetch_events(page_size)
    topics, topics_total_count = _fetch_topics(page_size)

    post_chunks = _chunk_sitemap_entries(
        _build_post_entries_by_item(posts, normalized_base_url, url_template),
        max_urls_per_file,
    )
    content_chunks = _chunk_sitemap_entries(
        _build_content_entries_by_item(contents, normalized_base_url, content_url_template),
        max_urls_per_file,
    )
    event_chunks = _chunk_sitemap_entries(
        _build_event_entries_by_item(events, normalized_base_url, event_url_template),
        max_urls_per_file,
    )
    topic_chunks = _chunk_sitemap_entries(
        _build_topic_entries_by_item(topics, normalized_base_url, topic_url_template),
        max_urls_per_file,
    )

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    now_iso = datetime.now(timezone.utc).isoformat()
    uploaded_paths: List[str] = []
    sitemap_index_entries: List[Tuple[str, str]] = []

    def _upload_sitemap_chunks(kind: str, chunks: List[List[Tuple[str, str, Dict[str, str]]]]) -> None:
        for idx, chunk in enumerate(chunks, start=1):
            filename = f"{kind}-sitemap-{idx}.xml"
            object_path = f"{base_dir}/{filename}" if base_dir else filename
            sitemap_xml = _build_sitemap_xml(chunk)
            blob = bucket.blob(object_path)
            _apply_cache_control(blob, cache_control_seconds)
            blob.upload_from_string(
                sitemap_xml,
                content_type="application/xml; charset=utf-8",
            )
            uploaded_paths.append(object_path)
            sitemap_index_entries.append((f"{normalized_base_url}/{object_path}", now_iso))

    # The listing pages are feeds of posts, so they change when a post does. Stamping
    # them with the export time instead — as this did — told Google every one of them
    # was freshly updated on every run, which is a claim it learns to discount.
    static_pages_lastmod = _latest_lastmod([_post_lastmod(post) for post in posts], now_iso)

    page_chunks = _chunk_sitemap_entries(
        _build_static_page_entries_by_item(normalized_base_url, static_pages_lastmod),
        max_urls_per_file,
    )
    _upload_sitemap_chunks("pages", page_chunks)
    _upload_sitemap_chunks("posts", post_chunks)
    _upload_sitemap_chunks("events", event_chunks)
    _upload_sitemap_chunks("topics", topic_chunks)
    _upload_sitemap_chunks("contents", content_chunks)

    index_path = f"{base_dir}/sitemap.xml" if base_dir else "sitemap.xml"
    sitemap_index_xml = _build_sitemap_index_xml(sitemap_index_entries)
    blob = bucket.blob(index_path)
    _apply_cache_control(blob, cache_control_seconds)
    blob.upload_from_string(
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
        "contents_count": len(contents),
        "contents_total_count": contents_total_count,
        "events_count": len(events),
        "events_total_count": events_total_count,
        "topics_count": len(topics),
        "topics_total_count": topics_total_count,
        "static_pages_count": len(STATIC_PAGE_PATH_TEMPLATES),
        "static_page_url_count": len(STATIC_PAGE_PATH_TEMPLATES) * len(LANGUAGES),
        "url_count": (
            len(posts) + len(contents) + len(events) + len(topics) + len(STATIC_PAGE_PATH_TEMPLATES)
        )
        * len(LANGUAGES),
        "sitemap_files_count": (
            len(page_chunks) + len(post_chunks) + len(event_chunks) + len(topic_chunks) + len(content_chunks)
        ),
        "page_sitemap_files_count": len(page_chunks),
        "post_sitemap_files_count": len(post_chunks),
        "event_sitemap_files_count": len(event_chunks),
        "topic_sitemap_files_count": len(topic_chunks),
        "content_sitemap_files_count": len(content_chunks),
        "max_urls_per_file": max_urls_per_file,
        "base_url": normalized_base_url,
        "url_template": url_template,
        "content_url_template": content_url_template,
        "event_url_template": event_url_template,
        "topic_url_template": topic_url_template,
    }
