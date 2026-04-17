import asyncio
import logging
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query

from . import schemas
from .export_all_posts import export_all_posts_pops_to_gcs, export_all_posts_to_gcs
from .export_contents import export_all_contents_to_gcs
from .export_curated_posts import export_curated_posts_to_gcs
from .export_home_sections import export_home_sections_to_gcs
from .export_sidebar_topics import export_sidebar_topics_to_gcs
from .export_topic_posts import export_topic_pops_to_gcs, export_topic_posts_to_gcs
from .export_topics_daily_stats import export_topics_daily_stats_to_gcs

logger = logging.getLogger(__name__)
app = FastAPI(title="Forum Cron Services", version="0.1.0")


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
) -> schemas.ExportContentsToGcsRequest:
    return schemas.ExportContentsToGcsRequest(
        prefix=prefix, page_size=page_size, slug=slug
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
) -> schemas.ExportTopicPostsToGcsRequest:
    return schemas.ExportTopicPostsToGcsRequest(
        prefix=prefix,
        per_topic_limit=per_topic_limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
    )


def _export_home_sections_query(
    prefix: str = Query(
        default="exports/home-sections",
        description=schemas.ExportHomeSectionsToGcsRequest.model_fields["prefix"].description,
    ),
) -> schemas.ExportHomeSectionsToGcsRequest:
    return schemas.ExportHomeSectionsToGcsRequest(prefix=prefix)


def _export_sidebar_topics_query(
    prefix: str = Query(
        default="exports/sidebar-topics",
        description=schemas.ExportSidebarTopicsToGcsRequest.model_fields["prefix"].description,
    ),
) -> schemas.ExportSidebarTopicsToGcsRequest:
    return schemas.ExportSidebarTopicsToGcsRequest(prefix=prefix)


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
) -> schemas.ExportTopicsDailyStatsToGcsRequest:
    return schemas.ExportTopicsDailyStatsToGcsRequest(
        prefix=prefix,
        timezone=timezone_name,
        local_date=local_date,
        post_state=post_state,
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
) -> schemas.ExportCuratedPostsToGcsRequest:
    return schemas.ExportCuratedPostsToGcsRequest(
        prefix=prefix,
        limit=limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
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
) -> schemas.ExportAllPostsToGcsRequest:
    return schemas.ExportAllPostsToGcsRequest(
        prefix=prefix,
        limit=limit,
        post_state=post_state,
        scan_multiplier=scan_multiplier,
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
    匯出首頁區塊（footer / editor-choices / pop-polls）JSON 並上傳 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_home_sections_to_gcs,
            prefix=body.prefix,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/home-sections-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/home-sections-to-gcs failed: %s", e)
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/sidebar-topics-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/sidebar-topics-to-gcs failed: %s", e)
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/curated-posts-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/curated-posts-to-gcs failed: %s", e)
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/all-posts-pops-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/all-posts-pops-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
