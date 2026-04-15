from typing import Optional

from pydantic import BaseModel, Field


class ExportContentsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/contents",
        description="GCS 目錄前綴（不含時間戳）；每次匯出覆寫該前綴下同名檔案",
    )
    page_size: int = Field(default=200, ge=1, le=1000, description="每次 GQL 擷取筆數")
    slug: Optional[str] = Field(
        default=None,
        description="選填；Keystone Content.identifier（slug）。若提供則只匯出該筆，未提供則匯出全部",
    )


class ExportTopicPostsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/topic-posts",
        description="GCS 目錄前綴（不含時間戳）；每個 topic 寫入 {slug}-latest/pop/polls.json，每次覆寫",
    )
    per_topic_limit: int = Field(
        default=10,
        ge=1,
        le=200,
        description="對應前端 GQL 變數 take（每 topic 每支查詢取幾則），並受環境變數 GQL_POST_MAX_TAKE 上限",
    )
    post_state: str = Field(
        default="active",
        description="文章狀態；active 會映射為 where status equals published",
    )
    scan_multiplier: int = Field(
        default=10,
        ge=1,
        le=50,
        description="熱門候選池倍率（只影響 -pop.json；候選數約為 per_topic_limit * scan_multiplier，並受 GQL_POST_MAX_TAKE 上限）",
    )


class ExportCuratedPostsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/curated-posts",
        description="GCS 目錄前綴（不含時間戳）；寫入 editor-choice/life-guide 的 latest/pop/polls.json，每次覆寫",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=200,
        description="每種 JSON 取幾則文章，並受環境變數 GQL_POST_MAX_TAKE 上限",
    )
    post_state: str = Field(
        default="active",
        description="文章狀態；active 會映射為 where status equals published",
    )
    scan_multiplier: int = Field(
        default=10,
        ge=1,
        le=50,
        description="熱門候選池倍率（只影響 -pop.json；候選數約為 limit * scan_multiplier，並受 GQL_POST_MAX_TAKE 上限）",
    )


class ExportAllPostsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/all-posts",
        description="GCS 目錄前綴（不含時間戳）；寫入 all-posts 的 latest/pop/polls.json，每次覆寫",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=200,
        description="每種 JSON 取幾則文章，並受環境變數 GQL_POST_MAX_TAKE 上限",
    )
    post_state: str = Field(
        default="active",
        description="文章狀態；active 會映射為 where status equals published",
    )
    scan_multiplier: int = Field(
        default=10,
        ge=1,
        le=50,
        description="熱門候選池倍率（只影響 -pop.json；候選數約為 limit * scan_multiplier，並受 GQL_POST_MAX_TAKE 上限）",
    )


class ExportTopicsDailyStatsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/topic-daily-stats",
        description="GCS 目錄前綴（不含時間戳）；topics-daily.json 每次覆寫",
    )
    timezone: str = Field(
        default="Asia/Taipei",
        description="IANA 時區名稱，用來界定「當日」起訖（本地日曆日）",
    )
    local_date: Optional[str] = Field(
        default=None,
        description="選填，YYYY-MM-DD；省略則取該時區之「今天」",
    )
    post_state: str = Field(
        default="active",
        description="文章狀態；active 會映射為 Keystone status=published",
    )
