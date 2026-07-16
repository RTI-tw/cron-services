import asyncio
import hmac
import os
import logging
from typing import Annotated, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from . import schemas
from .export_ads import export_active_ads_to_gcs
from .export_all_posts import (
    export_all_posts_latest_polls_to_gcs,
    export_all_posts_pops_to_gcs,
    export_all_posts_to_gcs,
)
from .export_contents import export_all_contents_to_gcs
from .export_curated_posts import (
    export_curated_posts_latest_polls_to_gcs,
    export_curated_posts_pops_to_gcs,
    export_curated_posts_to_gcs,
)
from .export_events import export_events_to_gcs
from .export_forbidden_keywords import export_forbidden_keywords_to_gcs
from .export_home_sections import (
    export_home_curated_images_to_gcs,
    export_home_editor_choices_to_gcs,
    export_home_pop_polls_to_gcs,
    export_home_sections_to_gcs,
)
from .export_posts_sitemap import export_posts_sitemap_to_gcs
from .export_sidebar_topics import export_sidebar_topics_to_gcs
from .export_topic_posts import export_topic_pops_to_gcs, export_topic_posts_to_gcs
from .export_topics_daily_stats import export_topics_daily_stats_to_gcs
from .import_rti_rss import import_rti_rss_posts
from .retry_missing_translations import retry_missing_translations

logger = logging.getLogger(__name__)
app = FastAPI(title="Forum Cron Services", version="0.1.0")

_EGRESS_IP_PROVIDER = "api.ipify.org"
_EGRESS_IP_URL = "https://api.ipify.org?format=json"

_CACHE_CONTROL_DESCRIPTION = schemas.ExportContentsToGcsRequest.model_fields[
    "cache_control_seconds"
].description
_CACHE_CONTROL_DEFAULT_SECONDS = schemas.ExportContentsToGcsRequest.model_fields[
    "cache_control_seconds"
].default


def _export_contents_query(
    prefix: str = Query(
        default="exports/contents",
        description=schemas.ExportContentsToGcsRequest.model_fields["prefix"].description,
    ),
    page_size: int = Query(
        default=200,
        ge=1,
        le=1000,
        description=schemas.ExportContentsToGcsRequest.model_fields["page_size"].description,
    ),
    slug: Optional[str] = Query(
        default=None,
        description=schemas.ExportContentsToGcsRequest.model_fields["slug"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportContentsToGcsRequest:
    return schemas.ExportContentsToGcsRequest(
        prefix=prefix,
        page_size=page_size,
        slug=slug,
        cache_control_seconds=cache_control_seconds,
    )


def _export_topic_posts_query(
    prefix: str = Query(
        default="exports/topic-posts",
        description=schemas.ExportTopicPostsToGcsRequest.model_fields["prefix"].description,
    ),
    per_topic_limit: int = Query(
        default=10,
        ge=1,
        le=200,
        description=schemas.ExportTopicPostsToGcsRequest.model_fields[
            "per_topic_limit"
        ].description,
    ),
    post_state: str = Query(
        default="active",
        description=schemas.ExportTopicPostsToGcsRequest.model_fields["post_state"].description,
    ),
    scan_multiplier: int = Query(
        default=10,
        ge=1,
        le=50,
        description=schemas.ExportTopicPostsToGcsRequest.model_fields[
            "scan_multiplier"
        ].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportTopicPostsToGcsRequest:
    return schemas.ExportTopicPostsToGcsRequest(
        prefix=prefix,
        per_topic_limit=per_topic_limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
        cache_control_seconds=cache_control_seconds,
    )


def _export_home_sections_query(
    prefix: str = Query(
        default="exports/home-sections",
        description=schemas.ExportHomeSectionsToGcsRequest.model_fields["prefix"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportHomeSectionsToGcsRequest:
    return schemas.ExportHomeSectionsToGcsRequest(
        prefix=prefix,
        cache_control_seconds=cache_control_seconds,
    )


def _export_sidebar_topics_query(
    prefix: str = Query(
        default="exports/sidebar-topics",
        description=schemas.ExportSidebarTopicsToGcsRequest.model_fields["prefix"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportSidebarTopicsToGcsRequest:
    return schemas.ExportSidebarTopicsToGcsRequest(
        prefix=prefix,
        cache_control_seconds=cache_control_seconds,
    )


def _export_forbidden_keywords_query(
    prefix: str = Query(
        default="exports/forbidden-keywords",
        description=schemas.ExportForbiddenKeywordsToGcsRequest.model_fields["prefix"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportForbiddenKeywordsToGcsRequest:
    return schemas.ExportForbiddenKeywordsToGcsRequest(
        prefix=prefix,
        cache_control_seconds=cache_control_seconds,
    )


def _export_ads_query(
    prefix: str = Query(
        default="exports/ads",
        description=schemas.ExportAdsToGcsRequest.model_fields["prefix"].description,
    ),
    take: int = Query(
        default=1,
        ge=1,
        le=100,
        description=schemas.ExportAdsToGcsRequest.model_fields["take"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportAdsToGcsRequest:
    return schemas.ExportAdsToGcsRequest(
        prefix=prefix,
        take=take,
        cache_control_seconds=cache_control_seconds,
    )


def _export_events_query(
    prefix: str = Query(
        default="exports/events",
        description=schemas.ExportEventsToGcsRequest.model_fields["prefix"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportEventsToGcsRequest:
    return schemas.ExportEventsToGcsRequest(
        prefix=prefix,
        cache_control_seconds=cache_control_seconds,
    )


def _export_posts_sitemap_query(
    prefix: str = Query(
        default="exports/sitemaps",
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["prefix"].description,
    ),
    base_url: str = Query(
        default="",
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["base_url"].description,
    ),
    url_template: str = Query(
        default="/{lang}/posts/{id}",
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["url_template"].description,
    ),
    content_url_template: str = Query(
        default="/{lang}/content/{identifier}",
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["content_url_template"].description,
    ),
    page_size: int = Query(
        default=200,
        ge=1,
        le=1000,
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["page_size"].description,
    ),
    max_urls_per_file: int = Query(
        default=50000,
        ge=5,
        le=50000,
        description=schemas.ExportPostsSitemapToGcsRequest.model_fields["max_urls_per_file"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportPostsSitemapToGcsRequest:
    return schemas.ExportPostsSitemapToGcsRequest(
        prefix=prefix,
        base_url=base_url,
        url_template=url_template,
        content_url_template=content_url_template,
        page_size=page_size,
        max_urls_per_file=max_urls_per_file,
        cache_control_seconds=cache_control_seconds,
    )


def _export_topics_daily_stats_query(
    prefix: str = Query(
        default="exports/topic-daily-stats",
        description=schemas.ExportTopicsDailyStatsToGcsRequest.model_fields["prefix"].description,
    ),
    timezone_name: str = Query(
        default="Asia/Taipei",
        alias="timezone",
        description=schemas.ExportTopicsDailyStatsToGcsRequest.model_fields["timezone"].description,
    ),
    local_date: Optional[str] = Query(
        default=None,
        description=schemas.ExportTopicsDailyStatsToGcsRequest.model_fields[
            "local_date"
        ].description,
    ),
    post_state: str = Query(
        default="active",
        description=schemas.ExportTopicsDailyStatsToGcsRequest.model_fields[
            "post_state"
        ].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportTopicsDailyStatsToGcsRequest:
    return schemas.ExportTopicsDailyStatsToGcsRequest(
        prefix=prefix,
        timezone=timezone_name,
        local_date=local_date,
        post_state=post_state,
        cache_control_seconds=cache_control_seconds,
    )


def _export_curated_posts_query(
    prefix: str = Query(
        default="exports/curated-posts",
        description=schemas.ExportCuratedPostsToGcsRequest.model_fields["prefix"].description,
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=200,
        description=schemas.ExportCuratedPostsToGcsRequest.model_fields["limit"].description,
    ),
    post_state: str = Query(
        default="active",
        description=schemas.ExportCuratedPostsToGcsRequest.model_fields["post_state"].description,
    ),
    scan_multiplier: int = Query(
        default=10,
        ge=1,
        le=50,
        description=schemas.ExportCuratedPostsToGcsRequest.model_fields["scan_multiplier"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportCuratedPostsToGcsRequest:
    return schemas.ExportCuratedPostsToGcsRequest(
        prefix=prefix,
        limit=limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
        cache_control_seconds=cache_control_seconds,
    )


def _export_all_posts_query(
    prefix: str = Query(
        default="exports/all-posts",
        description=schemas.ExportAllPostsToGcsRequest.model_fields["prefix"].description,
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=200,
        description=schemas.ExportAllPostsToGcsRequest.model_fields["limit"].description,
    ),
    post_state: str = Query(
        default="active",
        description=schemas.ExportAllPostsToGcsRequest.model_fields["post_state"].description,
    ),
    scan_multiplier: int = Query(
        default=10,
        ge=1,
        le=50,
        description=schemas.ExportAllPostsToGcsRequest.model_fields["scan_multiplier"].description,
    ),
    cache_control_seconds: Optional[int] = Query(
        default=_CACHE_CONTROL_DEFAULT_SECONDS,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    ),
) -> schemas.ExportAllPostsToGcsRequest:
    return schemas.ExportAllPostsToGcsRequest(
        prefix=prefix,
        limit=limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
        cache_control_seconds=cache_control_seconds,
    )


def _retry_missing_translations_query(
    targets: str = Query(
        default="posts,comments",
        description=schemas.RetryMissingTranslationsRequest.model_fields["targets"].description,
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description=schemas.RetryMissingTranslationsRequest.model_fields["limit"].description,
    ),
    dry_run: bool = Query(
        default=True,
        description=schemas.RetryMissingTranslationsRequest.model_fields["dry_run"].description,
    ),
    message_services_url: str = Query(
        default="",
        description=schemas.RetryMissingTranslationsRequest.model_fields["message_services_url"].description,
    ),
    post_statuses: str = Query(
        default="published,pending,draft",
        description=schemas.RetryMissingTranslationsRequest.model_fields["post_statuses"].description,
    ),
    comment_statuses: str = Query(
        default="published",
        description=schemas.RetryMissingTranslationsRequest.model_fields["comment_statuses"].description,
    ),
    sync_timeout_seconds: float = Query(
        default=60.0,
        ge=1,
        le=180,
        description=schemas.RetryMissingTranslationsRequest.model_fields[
            "sync_timeout_seconds"
        ].description,
    ),
    max_runtime_seconds: float = Query(
        default=170.0,
        ge=1,
        le=3600,
        description=schemas.RetryMissingTranslationsRequest.model_fields[
            "max_runtime_seconds"
        ].description,
    ),
) -> schemas.RetryMissingTranslationsRequest:
    return schemas.RetryMissingTranslationsRequest(
        targets=targets,
        limit=limit,
        dry_run=dry_run,
        message_services_url=message_services_url,
        post_statuses=post_statuses,
        comment_statuses=comment_statuses,
        sync_timeout_seconds=sync_timeout_seconds,
        max_runtime_seconds=max_runtime_seconds,
    )


def _import_rti_rss_posts_query(
    rss_url: str = Query(
        default="",
        description=schemas.ImportRtiRssPostsRequest.model_fields["rss_url"].description,
    ),
    max_items: int = Query(
        default=50,
        ge=1,
        le=200,
        description=schemas.ImportRtiRssPostsRequest.model_fields["max_items"].description,
    ),
    dry_run: bool = Query(
        default=True,
        description=schemas.ImportRtiRssPostsRequest.model_fields["dry_run"].description,
    ),
    publish_status: str = Query(
        default="pending",
        description=schemas.ImportRtiRssPostsRequest.model_fields[
            "publish_status"
        ].description,
    ),
    author_member_id: str = Query(
        default="",
        description=schemas.ImportRtiRssPostsRequest.model_fields[
            "author_member_id"
        ].description,
    ),
) -> schemas.ImportRtiRssPostsRequest:
    return schemas.ImportRtiRssPostsRequest(
        rss_url=rss_url,
        max_items=max_items,
        dry_run=dry_run,
        publish_status=publish_status,
        author_member_id=author_member_id,
    )


def _runtime_error_http_detail(exc: RuntimeError) -> dict:
    """讓呼叫端與 Cloud Logging 能快速分辨 503 原因。"""
    msg = str(exc)
    code = "runtime_error"
    if "KEYSTONE_GQL_ENDPOINT" in msg:
        code = "keystone_config"
    elif "GraphQL error" in msg:
        code = "graphql_error"
    return {"code": code, "message": msg}


def _assert_cron_trigger_allowed(dry_run: bool, x_cron_token: str) -> None:
    if dry_run:
        return
    expected = (os.getenv("CRON_SERVICE_TRIGGER_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "cron_trigger_token_missing",
                "message": "CRON_SERVICE_TRIGGER_TOKEN 未設定，拒絕正式寫入",
            },
        )
    if not hmac.compare_digest(x_cron_token, expected):
        raise HTTPException(status_code=403, detail="cron trigger token invalid")


def _fetch_egress_ip() -> dict[str, str]:
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        response = client.get(_EGRESS_IP_URL)
        response.raise_for_status()
        data = response.json()

    ip = str(data.get("ip") or "").strip()
    if not ip:
        raise RuntimeError("egress IP provider did not return an IP")
    return {"ip": ip, "provider": _EGRESS_IP_PROVIDER}


@app.get("/export/contents-to-gcs")
async def export_contents_to_gcs(
    body: Annotated[schemas.ExportContentsToGcsRequest, Depends(_export_contents_query)],
):
    """
    從 Keystone GraphQL 抓取全部 contents，逐筆輸出 JSON 檔並上傳到 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_all_contents_to_gcs,
            prefix=body.prefix,
            page_size=body.page_size,
            content_slug=body.slug,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/contents-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/contents-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/topic-posts-to-gcs")
async def export_topic_posts(
    body: Annotated[schemas.ExportTopicPostsToGcsRequest, Depends(_export_topic_posts_query)],
):
    """
    依前端相同 GQL 輸出每 topic 的 latest / polls JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_topic_posts_to_gcs,
            prefix=body.prefix,
            per_topic_limit=body.per_topic_limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/topic-posts-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/topic-posts-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/topic-pops-to-gcs")
async def export_topic_pops(
    body: Annotated[schemas.ExportTopicPostsToGcsRequest, Depends(_export_topic_posts_query)],
):
    """
    依熱門規則（Boost 優先 + 積分 + fallback）輸出每 topic 的 pop JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_topic_pops_to_gcs,
            prefix=body.prefix,
            per_topic_limit=body.per_topic_limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/topic-pops-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/topic-pops-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/home-sections-to-gcs")
async def export_home_sections(
    body: Annotated[
        schemas.ExportHomeSectionsToGcsRequest,
        Depends(_export_home_sections_query),
    ],
):
    """
    匯出首頁區塊（footer / editor-choices / pop-polls / curated-images）JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_home_sections_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/home-sections-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/home-sections-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/home-editor-choices-to-gcs")
async def export_home_editor_choices(
    body: Annotated[
        schemas.ExportHomeSectionsToGcsRequest,
        Depends(_export_home_sections_query),
    ],
):
    """
    只輸出首頁 active 編輯精選輪播 editor-choices.json，方便獨立排程。
    """
    try:
        return await asyncio.to_thread(
            export_home_editor_choices_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/home-editor-choices-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/home-editor-choices-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/home-curated-images-to-gcs")
async def export_home_curated_images(
    body: Annotated[
        schemas.ExportHomeSectionsToGcsRequest,
        Depends(_export_home_sections_query),
    ],
):
    """
    只輸出首頁策展圖片 curated-images.json（熱門投票下方），方便獨立排程。
    """
    try:
        return await asyncio.to_thread(
            export_home_curated_images_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/home-curated-images-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/home-curated-images-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/home-pop-polls-to-gcs")
async def export_home_pop_polls(
    body: Annotated[
        schemas.ExportHomeSectionsToGcsRequest,
        Depends(_export_home_sections_query),
    ],
):
    """
    只輸出首頁熱門投票 pop-polls.json，方便獨立排程。
    """
    try:
        return await asyncio.to_thread(
            export_home_pop_polls_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/home-pop-polls-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/home-pop-polls-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/sidebar-topics-to-gcs")
async def export_sidebar_topics(
    body: Annotated[
        schemas.ExportSidebarTopicsToGcsRequest,
        Depends(_export_sidebar_topics_query),
    ],
):
    """
    依 Sidebar GraphQL query 輸出 topics.json 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_sidebar_topics_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/sidebar-topics-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/sidebar-topics-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/forbidden-keywords-to-gcs")
async def export_forbidden_keywords(
    body: Annotated[
        schemas.ExportForbiddenKeywordsToGcsRequest,
        Depends(_export_forbidden_keywords_query),
    ],
):
    """
    依 ForbiddenKeyword 匯出 keywords.json 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_forbidden_keywords_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/forbidden-keywords-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/forbidden-keywords-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/ads-to-gcs")
async def export_ads(
    body: Annotated[
        schemas.ExportAdsToGcsRequest,
        Depends(_export_ads_query),
    ],
):
    """
    依目前時間輸出 active ads.json 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_active_ads_to_gcs,
            prefix=body.prefix,
            take=body.take,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/ads-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/ads-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/events-to-gcs")
async def export_events(
    body: Annotated[
        schemas.ExportEventsToGcsRequest,
        Depends(_export_events_query),
    ],
):
    """
    輸出活動預覽卡 previews.json 與首頁推廣活動 homepage.json 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_events_to_gcs,
            prefix=body.prefix,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/events-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/events-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/posts-sitemap-to-gcs")
async def export_posts_sitemap(
    body: Annotated[
        schemas.ExportPostsSitemapToGcsRequest,
        Depends(_export_posts_sitemap_query),
    ],
):
    """
    依重要靜態頁、published posts 與 contents 產出 Google Search sitemap.xml 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_posts_sitemap_to_gcs,
            prefix=body.prefix,
            base_url=body.base_url,
            url_template=body.url_template,
            content_url_template=body.content_url_template,
            page_size=body.page_size,
            max_urls_per_file=body.max_urls_per_file,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/posts-sitemap-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/posts-sitemap-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/topics-daily-stats-to-gcs")
async def export_topics_daily_stats(
    body: Annotated[
        schemas.ExportTopicsDailyStatsToGcsRequest,
        Depends(_export_topics_daily_stats_query),
    ],
):
    """
    列出目前所有 topic（Keystone 尚無 Topic.isActive，等同全部 topic），
    並附每個 topic 在指定時區「當日」已發佈新文章數，合併為單一 JSON 上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_topics_daily_stats_to_gcs,
            prefix=body.prefix,
            timezone_name=body.timezone,
            local_date_str=body.local_date,
            post_state=body.post_state,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/topics-daily-stats-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/topics-daily-stats-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/curated-posts-to-gcs")
async def export_curated_posts(
    body: Annotated[
        schemas.ExportCuratedPostsToGcsRequest,
        Depends(_export_curated_posts_query),
    ],
):
    """
    依全站文章中「編輯精選 / 生活須知」條件，分別輸出 latest / polls / pop 六種 JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_curated_posts_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/curated-posts-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/curated-posts-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/curated-posts-latest-polls-to-gcs")
async def export_curated_posts_latest_polls(
    body: Annotated[
        schemas.ExportCuratedPostsToGcsRequest,
        Depends(_export_curated_posts_query),
    ],
):
    """
    只輸出 curated posts 的 latest / polls JSON，方便與熱門分開排程。
    """
    try:
        return await asyncio.to_thread(
            export_curated_posts_latest_polls_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/curated-posts-latest-polls-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/curated-posts-latest-polls-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/curated-posts-pops-to-gcs")
async def export_curated_posts_pops(
    body: Annotated[
        schemas.ExportCuratedPostsToGcsRequest,
        Depends(_export_curated_posts_query),
    ],
):
    """
    只輸出 curated posts 的 pop JSON，方便獨立排程。
    """
    try:
        return await asyncio.to_thread(
            export_curated_posts_pops_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/curated-posts-pops-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/curated-posts-pops-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/all-posts-to-gcs")
async def export_all_posts(
    body: Annotated[
        schemas.ExportAllPostsToGcsRequest,
        Depends(_export_all_posts_query),
    ],
):
    """
    依全站所有文章條件，輸出 latest / polls / pop 三種 JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_all_posts_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/all-posts-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/all-posts-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/all-posts-pops-to-gcs")
async def export_all_posts_pops(
    body: Annotated[
        schemas.ExportAllPostsToGcsRequest,
        Depends(_export_all_posts_query),
    ],
):
    """
    只輸出全站所有文章的 pop JSON，方便獨立排程。
    """
    try:
        return await asyncio.to_thread(
            export_all_posts_pops_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/all-posts-pops-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/all-posts-pops-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/export/all-posts-latest-polls-to-gcs")
async def export_all_posts_latest_polls(
    body: Annotated[
        schemas.ExportAllPostsToGcsRequest,
        Depends(_export_all_posts_query),
    ],
):
    """
    只輸出全站所有文章的 latest / polls JSON，方便與熱門分開排程。
    """
    try:
        return await asyncio.to_thread(
            export_all_posts_latest_polls_to_gcs,
            prefix=body.prefix,
            limit=body.limit,
            post_state=body.post_state,
            scan_multiplier=body.scan_multiplier,
            cache_control_seconds=body.cache_control_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/all-posts-latest-polls-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/all-posts-latest-polls-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/maintenance/retry-missing-translations")
async def maintenance_retry_missing_translations(
    body: Annotated[
        schemas.RetryMissingTranslationsRequest,
        Depends(_retry_missing_translations_query),
    ],
):
    """
    掃描 spamScore 為 null 的 post/comment，重新呼叫 message-services 補送 AI 翻譯。
    """
    try:
        return await asyncio.to_thread(
            retry_missing_translations,
            targets=body.targets,
            limit=body.limit,
            dry_run=body.dry_run,
            message_services_url=body.message_services_url,
            post_statuses=body.post_statuses,
            comment_statuses=body.comment_statuses,
            sync_timeout_seconds=body.sync_timeout_seconds,
            max_runtime_seconds=body.max_runtime_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("maintenance/retry-missing-translations RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("maintenance/retry-missing-translations failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/import/rti-rss-posts")
async def import_rti_rss_posts_endpoint(
    body: Annotated[
        schemas.ImportRtiRssPostsRequest,
        Depends(_import_rti_rss_posts_query),
    ],
    x_cron_token: str = Header(default="", alias="X-Cron-Token"),
):
    """
    讀取央廣 RSS，依 CMS 的 RSS 關鍵字篩選新聞，並可建立為論壇文章。
    """
    try:
        _assert_cron_trigger_allowed(body.dry_run, x_cron_token)
        return await asyncio.to_thread(
            import_rti_rss_posts,
            rss_url=body.rss_url,
            max_items=body.max_items,
            dry_run=body.dry_run,
            publish_status=body.publish_status,
            author_member_id=body.author_member_id,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("import/rti-rss-posts RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except httpx.HTTPError as e:
        logger.warning("import/rti-rss-posts HTTPError: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("import/rti-rss-posts failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/debug/egress-ip")
async def debug_egress_ip(
    x_cron_token: str = Header(default="", alias="X-Cron-Token"),
):
    """
    回報目前 Cloud Run instance 對外連線看到的出口 IP。
    """
    try:
        _assert_cron_trigger_allowed(False, x_cron_token)
        return await asyncio.to_thread(_fetch_egress_ip)
    except HTTPException:
        raise
    except RuntimeError as e:
        logger.warning("debug/egress-ip RuntimeError: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except httpx.HTTPError as e:
        logger.warning("debug/egress-ip HTTPError: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("debug/egress-ip failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
