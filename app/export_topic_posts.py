import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from google.cloud import storage

from .config import get_settings
from .keystone_gql import execute_gql, get_thread_local_gql_client

_PHOTO_FIELDS = """
id
name
resized {
  original
  w480
  w800
  w1200
}
urlOriginal
"""

_POST_CARD_SELECTION = f"""
    id
    title
    title_zh
    title_en
    title_vi
    title_id
    title_th
    content
    content_zh
    content_en
    content_vi
    content_id
    content_th
    language
    status
    isBoost
    createdAt
    updatedAt
    author {{
      id
      name
      nickname
      avatar_image {{
{_PHOTO_FIELDS}
      }}
      customId
      isOfficial
    }}
    isEditorChoice
    isLifeGuide
    topics {{
      id
      name
      name_zh
      name_en
      name_vi
      name_id
      name_th
      slug
    }}
    heroImages(orderBy: {{ sortOrder: asc }}) {{
{_PHOTO_FIELDS}
    }}
    poll {{
      id
      totalVotes
    }}
    commentsCount
    reactionsCount
    reactions(take: 5) {{
      id
      type
    }}
"""

QUERY_TOPICS_META = """
query ListTopicsMeta {
  topics(orderBy: { sortOrder: asc }) {
    id
    name
    slug
    sortOrder
  }
}
"""


def _normalize_prefix(prefix: str) -> str:
    return (prefix or "").strip().strip("/")


def _topic_slug_for_path(topic: Dict[str, Any]) -> str:
    slug = str(topic.get("slug") or "").strip()
    tid = str(topic.get("id") or "").strip()
    if slug:
        return slug.replace("/", "_").replace("\\", "_")
    if tid:
        return f"topic-{tid[:16]}"
    return "topic-unknown"


def _resolve_post_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s in ("active", "published"):
        return "published"
    if s in ("draft", "archived", "hidden"):
        return s
    raise ValueError(f"不支援的 post 狀態: {status}")


def _max_take_per_request() -> int:
    raw = (os.getenv("GQL_POST_MAX_TAKE") or "100").strip()
    try:
        return max(1, min(int(raw), 1000))
    except ValueError:
        return 100


def _hot_threshold() -> int:
    raw = (os.getenv("HOT_SCORE_THRESHOLD") or "5").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 5


def _weight_reaction() -> int:
    return 2


def _weight_poll_vote() -> int:
    return 3


def _weight_comment() -> int:
    return 5


def _pop_take_limit(max_take: int) -> int:
    """熱門清單最多 50 篇。"""
    return min(50, max_take)


def _where_topic_by_slug_status(status_token: str, *, with_poll: bool = False, with_since: bool = False) -> str:
    parts = [
        f"status: {{ equals: {status_token} }}",
        "topics: { slug: { equals: $slug } }",
    ]
    if with_since:
        parts.append("published_date: { gte: $since }")
    if with_poll:
        parts.append("NOT: [{ poll: null }]")
    return "\n      ".join(parts)


def _build_query_topic_latest(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token)
    return f"""
query TopicLatest($slug: String!, $take: Int!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ published_date: desc }}, {{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_topic_polls(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token, with_poll=True)
    return f"""
query TopicPolls($slug: String!, $take: Int!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ published_date: desc }}, {{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_topic_hot_window(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token, with_since=True)
    return f"""
query TopicHotWindow($slug: String!, $take: Int!, $skip: Int!, $since: DateTime!) {{
  posts(
    where: {{
      {w}
    }}
    orderBy: [{{ published_date: desc }}, {{ createdAt: desc }}]
    skip: $skip
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
  }})
}}
"""


def _build_query_topic_boost(status_token: str) -> str:
    w = _where_topic_by_slug_status(status_token)
    return f"""
query TopicBoost($slug: String!, $take: Int!) {{
  posts(
    where: {{
      {w}
      isBoost: {{ equals: true }}
    }}
    orderBy: [{{ published_date: desc }}, {{ createdAt: desc }}]
    take: $take
  ) {{
{_POST_CARD_SELECTION}
  }}
  postsCount(where: {{
      {w}
      isBoost: {{ equals: true }}
  }})
}}
"""


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _poll_participants(post: Dict[str, Any]) -> int:
    poll = post.get("poll")
    if not isinstance(poll, dict):
        return 0
    return _to_int(poll.get("totalVotes"))


def _hot_score(post: Dict[str, Any]) -> int:
    reactions = _to_int(post.get("reactionsCount"))
    comments = _to_int(post.get("commentsCount"))
    poll_votes = _poll_participants(post)
    return reactions * _weight_reaction() + poll_votes * _weight_poll_vote() + comments * _weight_comment()


def _hot_score_breakdown(post: Dict[str, Any]) -> Dict[str, int]:
    reactions = _to_int(post.get("reactionsCount"))
    comments = _to_int(post.get("commentsCount"))
    poll_votes = _poll_participants(post)
    reaction_score = reactions * _weight_reaction()
    poll_score = poll_votes * _weight_poll_vote()
    comment_score = comments * _weight_comment()
    return {
        "reactionsCount": reactions,
        "reactionScore": reaction_score,
        "pollVotes": poll_votes,
        "pollScore": poll_score,
        "commentsCount": comments,
        "commentScore": comment_score,
        "total": reaction_score + poll_score + comment_score,
    }


def _created_sort_key(post: Dict[str, Any]) -> str:
    return str(post.get("createdAt") or "")


def _rank_hot_posts(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(posts, key=lambda p: (_hot_score(p), _created_sort_key(p)), reverse=True)


def _merge_boost_first(boost_posts: List[Dict[str, Any]], ranked_posts: List[Dict[str, Any]], take: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for p in boost_posts + ranked_posts:
        pid = str(p.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        out.append(p)
        seen.add(pid)
        if len(out) >= take:
            break
    return out


def _append_unique_posts_with_reason(
    out: List[Dict[str, Any]],
    seen: Set[str],
    posts: List[Dict[str, Any]],
    *,
    take: int,
    reason: str,
) -> None:
    for post in posts:
        pid = str(post.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        row = dict(post)
        row["rankingReason"] = reason
        out.append(row)
        seen.add(pid)
        if len(out) >= take:
            return


def _count_unique_posts(existing_seen: Set[str], posts: List[Dict[str, Any]]) -> int:
    seen = set(existing_seen)
    count = 0
    for post in posts:
        pid = str(post.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        count += 1
    return count


def _build_pop_posts_with_reason(
    *,
    boost_posts: List[Dict[str, Any]],
    eligible_3d_posts: List[Dict[str, Any]],
    eligible_14d_posts: List[Dict[str, Any]],
    latest_posts: List[Dict[str, Any]],
    take: int,
) -> Tuple[List[Dict[str, Any]], int]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    _append_unique_posts_with_reason(
        out,
        seen,
        boost_posts,
        take=take,
        reason="boost",
    )
    _append_unique_posts_with_reason(
        out,
        seen,
        eligible_3d_posts,
        take=take,
        reason="3d-score",
    )

    if len(out) < take:
        _append_unique_posts_with_reason(
            out,
            seen,
            eligible_14d_posts,
            take=take,
            reason="14d-score",
        )

    has_interaction_posts = bool(eligible_3d_posts or eligible_14d_posts)
    if len(out) < take and not has_interaction_posts:
        _append_unique_posts_with_reason(
            out,
            seen,
            latest_posts[:10],
            take=take,
            reason="latest-fallback",
        )

    ordered_seen: Set[str] = set()
    total_available = 0
    candidate_pools = (
        (boost_posts, eligible_3d_posts, eligible_14d_posts)
        if has_interaction_posts
        else (boost_posts, latest_posts[:10])
    )
    for pool in candidate_pools:
        total_available += _count_unique_posts(ordered_seen, pool)
        for post in pool:
            pid = str(post.get("id") or "").strip()
            if pid:
                ordered_seen.add(pid)

    return out, total_available


def _trim_post_content(text: Any, limit: int = 120) -> Any:
    if not isinstance(text, str):
        return text
    if len(text) <= limit:
        return text
    return f"{text[:limit]}......"


def _prepare_posts_for_export(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    for post in posts:
        row = dict(post)
        row["content"] = _trim_post_content(row.get("content"))
        breakdown = _hot_score_breakdown(row)
        row["score"] = breakdown["total"]
        row["scoreBreakdown"] = breakdown
        prepared.append(row)
    return prepared


def _append_count_fields(payload: Dict[str, Any], total_count: int) -> Dict[str, Any]:
    payload["totalCount"] = total_count
    payload["hasAll"] = len(payload.get("posts") or []) >= total_count
    return payload


def _fetch_posts_in_pages(
    query: str,
    variables: Dict[str, Any],
    *,
    total_limit: int,
    client: Any = None,
) -> Tuple[List[Dict[str, Any]], int]:
    if total_limit <= 0:
        return [], 0

    page_size = min(_max_take_per_request(), total_limit)
    skip = 0
    posts: List[Dict[str, Any]] = []
    posts_count = 0

    while len(posts) < total_limit:
        page_take = min(page_size, total_limit - len(posts))
        data = execute_gql(
            query,
            {
                **variables,
                "skip": skip,
                "take": page_take,
            },
            client=client,
        )
        page_posts = data.get("posts") or []
        posts_count = _to_int(data.get("postsCount"))
        if not page_posts:
            break
        posts.extend(page_posts)
        skip += len(page_posts)
        if len(page_posts) < page_take:
            break

    return posts[:total_limit], posts_count


def _fetch_all_posts_in_pages(
    query: str,
    variables: Dict[str, Any],
    *,
    client: Any = None,
) -> Tuple[List[Dict[str, Any]], int]:
    page_size = _max_take_per_request()
    skip = 0
    posts: List[Dict[str, Any]] = []
    posts_count = 0

    while True:
        data = execute_gql(
            query,
            {
                **variables,
                "skip": skip,
                "take": page_size,
            },
            client=client,
        )
        page_posts = data.get("posts") or []
        posts_count = _to_int(data.get("postsCount"))
        if not page_posts:
            break
        posts.extend(page_posts)
        skip += len(page_posts)
        if len(page_posts) < page_size:
            break

    return posts, posts_count


def _topic_payload(
    generated_at: str,
    topic_row: Dict[str, Any],
    posts_count: int,
    posts: List[Dict[str, Any]],
    *,
    total_count: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_total_count = posts_count if total_count is None else total_count
    return _append_count_fields({
        "generatedAt": generated_at,
        "topic": {"id": topic_row.get("id"), "name": topic_row.get("name"), "slug": topic_row.get("slug")},
        "postsCount": posts_count,
        "posts": _prepare_posts_for_export(posts),
    }, resolved_total_count)


def _apply_cache_control(blob: storage.Blob, cache_control_seconds: Optional[int] = None) -> None:
    if cache_control_seconds is None:
        return
    blob.cache_control = f"public, max-age={cache_control_seconds}"


def _upload_json(
    bucket: storage.Bucket,
    path: str,
    payload: Dict[str, Any],
    cache_control_seconds: Optional[int] = None,
) -> None:
    blob = bucket.blob(path)
    _apply_cache_control(blob, cache_control_seconds)
    blob.upload_from_string(json.dumps(payload, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")


def _build_per_topic_result(
    *,
    topic_row: Dict[str, Any],
    client: Any,
    take: int,
    pop_take: int,
    hot_scan_take: int,
    threshold: int,
    q_latest: str,
    q_polls: str,
    q_hot_window: str,
    q_boost: str,
    since_3d: str,
    since_14d: str,
    generated_at: str,
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    slug = str(topic_row.get("slug") or "").strip()
    if not slug:
        return "", {}

    latest_data = execute_gql(q_latest, {"slug": slug, "take": take}, client=client)
    latest_posts = latest_data.get("posts") or []
    latest_count = _to_int(latest_data.get("postsCount"))

    polls_data = execute_gql(q_polls, {"slug": slug, "take": take}, client=client)
    polls_posts = polls_data.get("posts") or []
    polls_count = _to_int(polls_data.get("postsCount"))

    hot_3d_posts, _hot_3d_count = _fetch_all_posts_in_pages(
        q_hot_window,
        {"slug": slug, "since": since_3d},
        client=client,
    )

    boost_data = execute_gql(q_boost, {"slug": slug, "take": pop_take}, client=client)
    boost_posts = boost_data.get("posts") or []

    hot_14d_posts: List[Dict[str, Any]] = []
    ranked_3d = _rank_hot_posts(hot_3d_posts) if hot_3d_posts else []
    eligible_3d = [p for p in ranked_3d if _hot_score(p) >= threshold]

    need_14d = len(boost_posts) + len(eligible_3d) < pop_take
    if need_14d:
        hot_14d_posts, _hot_14d_count = _fetch_all_posts_in_pages(
            q_hot_window,
            {"slug": slug, "since": since_14d},
            client=client,
        )
    ranked_14d = _rank_hot_posts(hot_14d_posts) if hot_14d_posts else []
    eligible_14d = [p for p in ranked_14d if _hot_score(p) >= threshold]
    pop_posts, pop_count = _build_pop_posts_with_reason(
        boost_posts=boost_posts,
        eligible_3d_posts=eligible_3d,
        eligible_14d_posts=eligible_14d,
        latest_posts=latest_posts,
        take=pop_take,
    )

    out = {
        "latest": _topic_payload(generated_at, topic_row, latest_count, latest_posts),
        "polls": _topic_payload(generated_at, topic_row, polls_count, polls_posts),
        "pop": _topic_payload(
            generated_at,
            topic_row,
            len(pop_posts),
            pop_posts,
            total_count=pop_count,
        ),
    }
    if not pop_posts:
        out["pop"]["emptyMessage"] = "尚無貼文"
    return slug, out


def _export_topic_files_to_gcs(
    *,
    prefix: str,
    per_topic_limit: int,
    post_state: str,
    scan_multiplier: int,
    include_latest: bool,
    include_polls: bool,
    include_pop: bool,
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    bucket_name = settings.gcs_bucket
    if not bucket_name:
        raise RuntimeError("GCS_BUCKET 環境變數未設定")
    if per_topic_limit <= 0:
        raise ValueError("per_topic_limit 必須大於 0")
    if scan_multiplier <= 0:
        raise ValueError("scan_multiplier 必須大於 0")

    status_token = _resolve_post_status(post_state)
    take = min(per_topic_limit, _max_take_per_request())
    pop_take = _pop_take_limit(_max_take_per_request())
    hot_scan_take = max(pop_take, per_topic_limit * scan_multiplier)
    threshold = _hot_threshold()

    q_latest = _build_query_topic_latest(status_token)
    q_polls = _build_query_topic_polls(status_token)
    q_hot_window = _build_query_topic_hot_window(status_token)
    q_boost = _build_query_topic_boost(status_token)

    data_topics = execute_gql(QUERY_TOPICS_META, None)
    topics = data_topics.get("topics") or []

    now = datetime.now(timezone.utc)
    since_3d = (now - timedelta(days=3)).isoformat()
    since_14d = (now - timedelta(days=14)).isoformat()
    generated_at = now.isoformat()

    def _run_topic(topic_row: Dict[str, Any]) -> Tuple[str, Dict[str, Dict[str, Any]]]:
        client = get_thread_local_gql_client()
        return _build_per_topic_result(
            topic_row=topic_row,
            client=client,
            take=take,
            pop_take=pop_take,
            hot_scan_take=hot_scan_take,
            threshold=threshold,
            q_latest=q_latest,
            q_polls=q_polls,
            q_hot_window=q_hot_window,
            q_boost=q_boost,
            since_3d=since_3d,
            since_14d=since_14d,
            generated_at=generated_at,
        )

    per_topic_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
    valid_topics = [t for t in topics if str(t.get("slug") or "").strip()]
    if valid_topics:
        max_workers = min(16, max(4, len(valid_topics)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_run_topic, t) for t in valid_topics]
            for fut in as_completed(futs):
                slug, out = fut.result()
                if slug:
                    per_topic_results[slug] = out

    base_dir = _normalize_prefix(prefix)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    def _under_base(name: str) -> str:
        return f"{base_dir}/{name}" if base_dir else name

    uploaded_paths: List[str] = []
    used_stems: Set[str] = set()

    for t in valid_topics:
        slug = str(t.get("slug") or "").strip()
        topic_out = per_topic_results.get(slug)
        if not topic_out:
            continue
        tid = str(t.get("id") or "").strip()
        stem = _topic_slug_for_path(t)
        if stem in used_stems and tid:
            stem = f"{stem}-{tid[:8]}"
        used_stems.add(stem)

        targets: List[Tuple[str, str]] = []
        if include_latest:
            targets.append(("latest", "latest"))
        if include_pop:
            targets.append(("pop", "pop"))
        if include_polls:
            targets.append(("polls", "polls"))

        for kind, suffix in targets:
            payload = topic_out.get(kind)
            if not payload:
                continue
            object_path = _under_base(f"{stem}-{suffix}.json")
            _upload_json(bucket, object_path, payload, cache_control_seconds)
            uploaded_paths.append(object_path)

    return {
        "bucket": bucket_name,
        "prefix": base_dir,
        "files": uploaded_paths,
        "topics_count": len(topics),
        "topics_exported_with_slug": len(valid_topics),
        "per_topic_limit": per_topic_limit,
        "take_used": take,
        "pop_take_used": pop_take,
        "hot_scan_take": hot_scan_take,
        "hot_score_threshold": threshold,
        "post_state": status_token,
    }


def export_topic_posts_to_gcs(
    *,
    prefix: str = "exports/topic-posts",
    per_topic_limit: int = 10,
    post_state: str = "active",
    scan_multiplier: int = 10,
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """輸出每個 topic 的 latest/polls 兩種檔案（不含 pop）。"""
    return _export_topic_files_to_gcs(
        prefix=prefix,
        per_topic_limit=per_topic_limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
        include_latest=True,
        include_polls=True,
        include_pop=False,
        cache_control_seconds=cache_control_seconds,
    )


def export_topic_pops_to_gcs(
    *,
    prefix: str = "exports/topic-posts",
    per_topic_limit: int = 10,
    post_state: str = "active",
    scan_multiplier: int = 10,
    cache_control_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """只輸出每個 topic 的 pop 檔案。"""
    return _export_topic_files_to_gcs(
        prefix=prefix,
        per_topic_limit=per_topic_limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
        include_latest=False,
        include_polls=False,
        include_pop=True,
        cache_control_seconds=cache_control_seconds,
    )
