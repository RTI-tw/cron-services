import html
import os
import re
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from xml.etree import ElementTree

import httpx

from .keystone_gql import execute_gql


RSS_KEYWORDS_QUERY = """
query RssKeywords {
  rssKeywords(
    where: { isEnabled: { equals: true } }
    orderBy: [{ updatedAt: desc }, { id: asc }]
    take: 1000
  ) {
    id
    keyword
    language
  }
}
"""

RSS_TOPIC_MAPPINGS_QUERY = """
query RssTopicMappings {
  rssTopicMappings(
    where: { isEnabled: { equals: true } }
    orderBy: [{ updatedAt: desc }, { id: asc }]
    take: 1000
  ) {
    id
    rssTopic
    topic {
      id
      name
    }
  }
}
"""

EXISTING_POST_QUERY = """
query ExistingPost($rssSourceUrl: String!, $link: String!, $canonicalLink: String!, $legacyNeedle: String!) {
  posts(
    where: {
      OR: [
        { rssSourceUrl: { equals: $rssSourceUrl } }
        { content: { contains: $link } }
        { content: { contains: $canonicalLink } }
        { content: { contains: $legacyNeedle } }
      ]
    }
    take: 1
  ) {
    id
    title
    content
    status
    rssSourceUrl
    isRtiChoice
    topics {
      id
    }
  }
}
"""

CREATE_POST_MUTATION = """
mutation CreatePost($data: PostCreateInput!) {
  createPost(data: $data) {
    id
    title
    status
    published_date
  }
}
"""

UPDATE_POST_MUTATION = """
mutation UpdatePost($where: PostWhereUniqueInput!, $data: PostUpdateInput!) {
  updatePost(where: $where, data: $data) {
    id
    title
    status
    published_date
  }
}
"""


@dataclass
class RssItem:
    title: str
    link: str
    description: str
    published_at: Optional[str]
    categories: List[str]


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _text(node: Optional[ElementTree.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _find_child(item: ElementTree.Element, names: Sequence[str]) -> Optional[ElementTree.Element]:
    for child in list(item):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names:
            return child
    return None


def _normalize_datetime(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _parse_rss(xml_text: str, max_items: int) -> List[RssItem]:
    root = ElementTree.fromstring(xml_text)
    raw_items = root.findall(".//item")
    if not raw_items:
        raw_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    items: List[RssItem] = []
    for raw in raw_items[:max_items]:
        title = _strip_html(_text(_find_child(raw, ("title",))))
        description = _strip_html(
            _text(_find_child(raw, ("description", "summary", "content")))
        )
        link = _text(_find_child(raw, ("link",)))
        link_node = _find_child(raw, ("link",))
        if not link and link_node is not None:
            link = (link_node.attrib.get("href") or "").strip()
        published_at = _normalize_datetime(
            _text(_find_child(raw, ("pubdate", "published", "updated")))
        )
        categories = [
            _strip_html(_text(child))
            for child in list(raw)
            if child.tag.rsplit("}", 1)[-1].lower() == "category"
            and _strip_html(_text(child))
        ]
        if title:
            items.append(
                RssItem(
                    title=title,
                    link=link,
                    description=description,
                    published_at=published_at,
                    categories=categories,
                )
            )
    return items


def _allowed_rss_hosts() -> set[str]:
    raw = (os.getenv("RTI_RSS_ALLOWED_HOSTS") or "www.rti.org.tw,rti.org.tw").strip()
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _load_rss_url(rss_url: str, *, allow_override: bool) -> str:
    if rss_url and not allow_override:
        raise ValueError("正式寫入時不可由 query 覆蓋 rss_url，請使用 RTI_RSS_FEED_URL")

    url = (rss_url or os.getenv("RTI_RSS_FEED_URL") or "").strip()
    if not url:
        raise ValueError("請提供 rss_url，或設定 RTI_RSS_FEED_URL 環境變數")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("rss_url 必須是有效的 http(s) URL")
    allowed_hosts = _allowed_rss_hosts()
    if parsed.hostname is None or parsed.hostname.lower() not in allowed_hosts:
        raise ValueError(
            "rss_url host 不在允許清單，請設定 RTI_RSS_ALLOWED_HOSTS"
        )
    return url


def _load_author_member_id(author_member_id: str) -> str:
    return (author_member_id or os.getenv("RTI_RSS_AUTHOR_MEMBER_ID") or "").strip()


def _validate_publish_status(publish_status: str) -> str:
    status = (publish_status or "pending").strip()
    allowed = {"published", "draft", "pending", "reject", "archived", "hidden"}
    if status not in allowed:
        raise ValueError(
            "publish_status 必須是 published、draft、pending、reject、archived 或 hidden"
        )
    return status


def _fetch_rss_items(rss_url: str, max_items: int) -> List[RssItem]:
    user_agent = (
        os.getenv("RTI_RSS_USER_AGENT")
        or "Mozilla/5.0 (compatible; RtiTalkRSSImporter/1.0; +https://www.rti.org.tw/)"
    ).strip()
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
    }
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        resp = client.get(rss_url)
        resp.raise_for_status()
    return _parse_rss(resp.text, max_items=max_items)


def _fetch_keywords() -> List[Dict[str, Any]]:
    data = execute_gql(RSS_KEYWORDS_QUERY)
    rows = data.get("rssKeywords") or []
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("keyword") or "").strip()
    ]


def _fetch_topic_mappings() -> List[Dict[str, Any]]:
    data = execute_gql(RSS_TOPIC_MAPPINGS_QUERY)
    rows = data.get("rssTopicMappings") or []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("rssTopic") or "").strip()
        and isinstance(row.get("topic"), dict)
        and str(row["topic"].get("id") or "").strip()
    ]


def _mapped_topic(
    item: RssItem,
    mappings: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    by_rss_topic = {
        str(row.get("rssTopic") or "").strip().casefold(): row["topic"]
        for row in mappings
    }
    for category in item.categories:
        topic = by_rss_topic.get(category.strip().casefold())
        if topic:
            return {
                "id": str(topic.get("id") or "").strip(),
                "name": str(topic.get("name") or "").strip(),
            }
    return None


def _matched_keywords(item: RssItem, keywords: Sequence[Dict[str, Any]]) -> List[str]:
    haystack = f"{item.title}\n{item.description}".casefold()
    matched: List[str] = []
    for row in keywords:
        keyword = str(row.get("keyword") or "").strip()
        if keyword and keyword.casefold() in haystack:
            matched.append(keyword)
    return matched


def _trim_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title).strip()
    if len(normalized) <= 80:
        return normalized
    return normalized[:79].rstrip() + "…"


def _canonical_rti_news_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (url or "").strip()

    query = parse_qs(parsed.query, keep_blank_values=True)
    uid = (query.get("uid") or [""])[0]
    pid = (query.get("pid") or [""])[0]
    if parsed.hostname in {"www.rti.org.tw", "rti.org.tw"} and uid and pid:
        canonical_query = urlencode({"uid": uid, "pid": pid})
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                canonical_query,
                "",
            )
        )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            parsed.query,
            "",
        )
    )


def _legacy_link_needle(url: str) -> str:
    parsed = urlparse((url or "").strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    uid = (query.get("uid") or [""])[0]
    pid = (query.get("pid") or [""])[0]
    if uid and pid:
        return f"uid={uid}&pid={pid}"
    return (url or "").strip()


def _build_post_content(item: RssItem, matched: Sequence[str]) -> str:
    parts = []
    if item.description:
        parts.append(item.description)
    canonical_link = _canonical_rti_news_url(item.link)
    if canonical_link:
        parts.append(f"來源連結：{canonical_link}")
    return "\n\n".join(parts).strip() or item.title


def _existing_post(item: RssItem) -> Optional[Dict[str, Any]]:
    if not item.link:
        return None
    canonical_link = _canonical_rti_news_url(item.link)
    data = execute_gql(
        EXISTING_POST_QUERY,
        {
            "rssSourceUrl": canonical_link,
            "link": item.link,
            "canonicalLink": canonical_link,
            "legacyNeedle": _legacy_link_needle(item.link),
        },
    )
    posts = data.get("posts") or []
    return posts[0] if posts else None


def _build_post_data(
    item: RssItem,
    matched: Sequence[str],
    publish_status: str,
    author_member_id: str,
    topic_id: str = "",
    *,
    for_update: bool = False,
) -> Dict[str, Any]:
    title = _trim_title(item.title)
    data: Dict[str, Any] = {
        "title": title,
        "content": _build_post_content(item, matched),
        "rssSourceUrl": _canonical_rti_news_url(item.link),
        "language": "zh",
        "status": publish_status,
        "isRtiChoice": True,
    }
    if item.published_at:
        data["published_date"] = item.published_at
    if author_member_id:
        data["author"] = {"connect": {"id": author_member_id}}
    if topic_id:
        data["topics"] = {"connect": {"id": topic_id}}
    elif for_update:
        data["topics"] = {"disconnect": True}
    return data


def _create_post(
    item: RssItem,
    matched: Sequence[str],
    publish_status: str,
    author_member_id: str,
    topic_id: str,
) -> Dict[str, Any]:
    data = _build_post_data(
        item,
        matched,
        publish_status,
        author_member_id,
        topic_id,
    )
    created = execute_gql(CREATE_POST_MUTATION, {"data": data})
    return created["createPost"]


def _update_post(
    existing_post: Dict[str, Any],
    item: RssItem,
    matched: Sequence[str],
    publish_status: str,
    author_member_id: str,
    topic_id: str,
) -> Dict[str, Any]:
    post_id = str(existing_post.get("id") or "").strip()
    if not post_id:
        raise RuntimeError("既有 Post 缺少 id，無法更新")
    data = _build_post_data(
        item,
        matched,
        publish_status,
        author_member_id,
        topic_id,
        for_update=True,
    )
    updated = execute_gql(
        UPDATE_POST_MUTATION,
        {"where": {"id": post_id}, "data": data},
    )
    return updated["updatePost"]


def _post_content_is_unchanged(
    existing_post: Dict[str, Any],
    item: RssItem,
    matched: Sequence[str],
    topic_id: str,
) -> bool:
    existing_topic = existing_post.get("topics")
    existing_topic_id = (
        str(existing_topic.get("id") or "").strip()
        if isinstance(existing_topic, dict)
        else ""
    )
    return (
        str(existing_post.get("title") or "") == _trim_title(item.title)
        and str(existing_post.get("content") or "") == _build_post_content(item, matched)
        and existing_post.get("isRtiChoice") is True
        and existing_topic_id == topic_id
    )


def import_rti_rss_posts(
    *,
    rss_url: str = "",
    max_items: int = 50,
    dry_run: bool = True,
    publish_status: str = "pending",
    author_member_id: str = "",
) -> Dict[str, Any]:
    url = _load_rss_url(rss_url, allow_override=dry_run)
    status = _validate_publish_status(publish_status)
    author_id = _load_author_member_id(author_member_id)
    keywords = _fetch_keywords()
    topic_mappings = _fetch_topic_mappings()
    items = _fetch_rss_items(url, max_items=max_items)

    matched_items: List[Dict[str, Any]] = []
    created_posts: List[Dict[str, Any]] = []
    updated_posts: List[Dict[str, Any]] = []
    unchanged_posts: List[Dict[str, Any]] = []

    for item in items:
        matched = _matched_keywords(item, keywords)
        if not matched:
            continue
        mapped_topic = _mapped_topic(item, topic_mappings)
        topic_id = mapped_topic["id"] if mapped_topic else ""

        title = _trim_title(item.title)
        matched_payload = {
            "title": title,
            "link": item.link,
            "published_at": item.published_at,
            "rss_categories": item.categories,
            "mapped_topic": mapped_topic,
            "matched_keywords": matched,
        }

        existing = _existing_post(item)
        if existing:
            if _post_content_is_unchanged(existing, item, matched, topic_id):
                matched_payload["action"] = "unchanged"
                matched_payload["existing_post_id"] = existing.get("id")
                matched_items.append(matched_payload)
                if dry_run:
                    continue
                unchanged_posts.append(
                    {
                        "id": existing.get("id"),
                        "title": existing.get("title"),
                        "status": existing.get("status"),
                        "rssSourceUrl": existing.get("rssSourceUrl"),
                    }
                )
                continue
            matched_payload["action"] = "update"
            matched_payload["existing_post_id"] = existing.get("id")
            matched_items.append(matched_payload)
            if dry_run:
                continue
            updated_posts.append(
                _update_post(existing, item, matched, status, author_id, topic_id)
            )
            continue

        matched_payload["action"] = "create"
        matched_items.append(matched_payload)
        if dry_run:
            continue
        created_posts.append(
            _create_post(item, matched, status, author_id, topic_id)
        )

    return {
        "rss_url": url,
        "dry_run": dry_run,
        "publish_status": status,
        "author_member_id": author_id or None,
        "keywords_count": len(keywords),
        "topic_mappings_count": len(topic_mappings),
        "rss_items_count": len(items),
        "matched_count": len(matched_items),
        "created_count": len(created_posts),
        "updated_count": len(updated_posts),
        "unchanged_count": len(unchanged_posts),
        "matched_items": matched_items,
        "created_posts": created_posts,
        "updated_posts": updated_posts,
        "unchanged_posts": unchanged_posts,
    }
