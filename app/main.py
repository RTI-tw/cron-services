import asyncio
import logging

from fastapi import FastAPI, HTTPException, status

from . import schemas
from .export_contents import export_all_contents_to_gcs
from .export_topic_posts import export_topic_posts_to_gcs
from .export_topics_daily_stats import export_topics_daily_stats_to_gcs

logger = logging.getLogger(__name__)
app = FastAPI(title="Forum Cron Services", version="0.1.0")


def _runtime_error_http_detail(exc: RuntimeError) -> dict:
    """讓呼叫端與 Cloud Logging 能快速分辨 503 原因。"""
    msg = str(exc)
    code = "runtime_error"
    if "KEYSTONE_GQL_ENDPOINT" in msg:
        code = "keystone_config"
    elif "GraphQL error" in msg:
        code = "graphql_error"
    return {"code": code, "message": msg}


@app.post("/export/contents-to-gcs")
async def export_contents_to_gcs(body: schemas.ExportContentsToGcsRequest):
    """
    從 Keystone GraphQL 抓取全部 contents，逐筆輸出 JSON 檔並上傳到 GCS。
    """
    try:
        return await asyncio.to_thread(
            export_all_contents_to_gcs,
            prefix=body.prefix,
            page_size=body.page_size,
            content_id=body.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        logger.warning("export/contents-to-gcs RuntimeError: %s", e)
        raise HTTPException(status_code=503, detail=_runtime_error_http_detail(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("export/contents-to-gcs failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post("/export/topic-posts-to-gcs")
async def export_topic_posts(body: schemas.ExportTopicPostsToGcsRequest):
    """
    產出每個 topic 的最新/熱門/含投票貼文三份 JSON，並上傳到 GCS。
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


@app.post("/export/topics-daily-stats-to-gcs")
async def export_topics_daily_stats(body: schemas.ExportTopicsDailyStatsToGcsRequest):
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


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
