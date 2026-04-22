from typing import Optional

from pydantic import BaseModel, Field


_CACHE_CONTROL_DESCRIPTION = (
    "選填；上傳 GCS 物件時設定 Cache-Control: public, max-age=<秒數>"
)


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
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class ExportHomeSectionsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/home-sections",
        description="GCS 目錄前綴（不含時間戳）；寫入 footer.json / editor-choices.json / pop-polls.json，每次覆寫",
    )
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class ExportSidebarTopicsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/sidebar-topics",
        description="GCS 目錄前綴（不含時間戳）；寫入 topics.json，每次覆寫",
    )
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class ExportForbiddenKeywordsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/forbidden-keywords",
        description="GCS 目錄前綴（不含時間戳）；寫入 keywords.json，每次覆寫",
    )
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class ExportAdsToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/ads",
        description="GCS 目錄前綴（不含時間戳）；寫入 ads.json，每次覆寫",
    )
    take: int = Field(
        default=1,
        ge=1,
        le=100,
        description="最多輸出幾筆 active ads",
    )
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class ExportPostsSitemapToGcsRequest(BaseModel):
    prefix: str = Field(
        default="exports/sitemaps",
        description="GCS 目錄前綴（不含時間戳）；寫入 sitemap.xml，每次覆寫",
    )
    base_url: str = Field(
        default="",
        description="網站 base URL；若未提供則讀取 SITE_BASE_URL / PUBLIC_SITE_URL / FRONTEND_BASE_URL / BASE_URL 環境變數",
    )
    url_template: str = Field(
        default="/{lang}/posts/{id}",
        description="Post URL path template，可用 {lang} 與 {id}",
    )
    content_url_template: str = Field(
        default="/{lang}/{identifier}",
        description="Content URL path template，可用 {lang} 與 {identifier}",
    )
    page_size: int = Field(
        default=200,
        ge=1,
        le=1000,
        description="每次 GraphQL 擷取幾筆 published posts",
    )
    max_urls_per_file: int = Field(
        default=50000,
        ge=5,
        le=50000,
        description="每個 sitemap 檔最多包含幾個 URL；Google sitemap 上限為 50000",
    )
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
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
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
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
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
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
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
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
    cache_control_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        description=_CACHE_CONTROL_DESCRIPTION,
    )


class RetryMissingTranslationsRequest(BaseModel):
    targets: str = Field(
        default="posts,comments",
        description="要補送的資料類型，可用 posts、comments 或 posts,comments",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="每種 target 本次最多挑幾筆 spamScore 為 null 的資料",
    )
    dry_run: bool = Field(
        default=True,
        description="true 只列出待補送資料，不呼叫 message-services；scheduler 應設 false",
    )
    message_services_url: str = Field(
        default="",
        description="message-services 根網址；若未提供則讀取 MESSAGE_SERVICES_URL / MESSAGE_SERVICES_BASE_URL",
    )
    post_statuses: str = Field(
        default="published,pending,draft",
        description="Post status 篩選，逗號分隔；傳 all 表示不篩狀態",
    )
    comment_statuses: str = Field(
        default="published",
        description="Comment status 篩選，逗號分隔；傳 all 表示不篩狀態",
    )
