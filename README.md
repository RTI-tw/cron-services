# cron-services

定時或手動觸發的 **FastAPI** 服務：從 Keystone GraphQL 讀取資料、匯出 JSON 並上傳至 GCS。架構與同層 `message-services` 專案相同（`requirements.txt` + `uvicorn app.main:app` + `Dockerfile`）。

## 安裝依賴

```bash
cd cron-services
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 環境變數

請先確保 GCP 認證已設定（例如 `GOOGLE_APPLICATION_CREDENTIALS` 或 Workload Identity）。

- `GCS_BUCKET`：上傳 JSON 的 GCS bucket（必填）
- `KEYSTONE_GQL_ENDPOINT`：Keystone GraphQL URL（必填）
- `KEYSTONE_AUTH_TOKEN`：選填，Bearer token
- `GCP_PROJECT_ID`：選填，便於日後擴充
- `GQL_POST_MAX_TAKE`：選填，預設 `100`。Keystone 對 `Post` 的 `graphql.maximumTake` 常有上限（例如 100）；匯出查詢的 `take` 會受此上限限制，避免 GraphQL **HTTP 400**。
- `HOT_SCORE_THRESHOLD`：選填，預設 `5`。`-pop.json` 在「3天內」綜合分數達到此門檻才視為熱門。
- `SITE_BASE_URL`：選填，sitemap 預設網站 base URL；也可改用 `PUBLIC_SITE_URL`、`FRONTEND_BASE_URL` 或 `BASE_URL`。
- `MESSAGE_SERVICES_URL`：選填，`/maintenance/retry-missing-translations` 呼叫 message-services 的根網址；也可改用 `MESSAGE_SERVICES_BASE_URL`。

Cloud Run 執行身分需能寫入該 bucket（例如 `roles/storage.objectAdmin` 或最小必要權限）。

## 啟動

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- OpenAPI：`http://localhost:8000/docs`
- 健康檢查：`GET /health`、`GET /healthz`

## API（由 message-services 搬移）

所有 `/export/*-to-gcs` endpoint 都支援共用參數 `cache_control_seconds`：

| 參數 | 說明 |
|------|------|
| `cache_control_seconds` | 預設 `300`；GCS 物件會設定 `Cache-Control: public, max-age=<秒數>` |

例如：

```
GET /export/all-posts-pops-to-gcs?prefix=json/all-posts&limit=100
GET /export/posts-sitemap-to-gcs?prefix=json/sitemaps&base_url=https://example.com
```

### `GET /export/contents-to-gcs`

Query 參數（皆有預設值，可省略）：

| 參數 | 說明 |
|------|------|
| `prefix` | GCS 路徑前綴，預設 `exports/contents` |
| `page_size` | 每次 GraphQL 擷取筆數，預設 `200`（1–1000） |
| `slug` | 選填；Keystone 的 `identifier`（slug）。若提供則只匯出該筆，未提供則分頁匯出全部 |

每次執行都寫入 **同一個 `prefix` 路徑**，檔名為 **`{slug}.json`**（slug 即 `identifier`；若該筆無 `identifier` 則退回 `{id}.json`），會 **覆寫** 既有同名物件；若 Keystone 已刪除某筆 content，GCS 裡舊檔不會自動刪除。

範例：

```
GET /export/contents-to-gcs?prefix=exports/contents/dev&page_size=200
GET /export/contents-to-gcs?prefix=exports/contents/dev&slug=my-content-slug
```

### `GET /export/sidebar-topics-to-gcs`

依 Sidebar 使用的 GraphQL query 匯出單一檔案 **`topics.json`**，內容 shape 與 query 回傳一致：

```json
{
  "topics": [
    {
      "id": "1",
      "name": "時事",
      "name_zh": "時事",
      "name_en": "Current Events",
      "name_vi": "...",
      "name_id": "...",
      "name_th": "...",
      "slug": "current-events",
      "sortOrder": 1,
      "description": "...",
      "postsCount": 120,
      "todayPostsCount": 3
    }
  ],
  "topicsCount": 10
}
```

- **GraphQL 條件**：固定使用 `where: { state: { equals: "active" } }`
- **排序**：固定使用 `orderBy: [{ sortOrder: asc }]`
- **輸出檔名**：`{prefix}/topics.json`

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/sidebar-topics` |

範例：

```
GET /export/sidebar-topics-to-gcs?prefix=json/sidebar
```

### `GET /export/forbidden-keywords-to-gcs`

依 Keystone `ForbiddenKeyword` 匯出單一檔案 **`keywords.json`**。輸出 shape 與 forum-cms `forbidden-keywords-json.ts` 相同，只包含 `isEnabled=true` 的項目。

```json
{
  "generatedAt": "...",
  "total": 1,
  "keywords": [
    {
      "id": "1",
      "word": "禁詞",
      "language": "zh",
      "translations": {
        "zh": "禁詞",
        "en": "forbidden word",
        "vi": "...",
        "id": "...",
        "th": "..."
      },
      "exemptions": ["豁免詞"],
      "updatedAt": "..."
    }
  ]
}
```

- **GraphQL 條件**：固定使用 `where: { isEnabled: { equals: true } }`
- **排序**：固定使用 `orderBy: [{ updatedAt: desc }, { id: asc }]`
- **輸出檔名**：`{prefix}/keywords.json`

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/forbidden-keywords` |

```
GET /export/forbidden-keywords-to-gcs?prefix=json/forbidden-keywords
```

### `GET /export/ads-to-gcs`

依目前時間匯出 active ads，寫入單一檔案 **`ads.json`**。

- **GraphQL 條件**：`status=active`、`startAt <= current ISO time`、`endAt >= current ISO time`
- **輸出 shape**：與 `GetActiveAds` query 回傳一致：`{ "ads": [...] }`
- **輸出檔名**：`{prefix}/ads.json`

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/ads` |
| `take` | 預設 `1`，最多輸出幾筆 active ads |

```
GET /export/ads-to-gcs?prefix=json/ads&take=1
```

### `GET /export/posts-sitemap-to-gcs`

依 `published` posts 與 `published` contents 產出 Google Search 使用的 sitemap。每篇 post / content 會產五種語言 URL（`zh/en/vi/id/th`），並在每個 `<url>` 裡附上 `xhtml:link rel="alternate"`。

輸出會包含：

- `sitemap.xml`：sitemap index，指向所有分頁 sitemap
- `posts-sitemap-1.xml`、`posts-sitemap-2.xml`...：post URL 清單，每個檔案最多 `max_urls_per_file` 筆 URL
- `contents-sitemap-1.xml`、`contents-sitemap-2.xml`...：content URL 清單，每個檔案最多 `max_urls_per_file` 筆 URL

> 目前 Keystone `Post` 沒有 slug 欄位，因此預設 post URL template 使用 post `id`：`/{lang}/posts/{id}`。Content 則預設使用 `identifier`：`/{lang}/{identifier}`。若前端實際路徑不同，請用 template 參數覆寫。

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/sitemaps` |
| `base_url` | 網站 base URL，例如 `https://example.com`；若不傳，依序讀取環境變數 `SITE_BASE_URL` / `PUBLIC_SITE_URL` / `FRONTEND_BASE_URL` / `BASE_URL` |
| `url_template` | 預設 `/{lang}/posts/{id}`，可用 `{lang}` 與 `{id}` |
| `content_url_template` | 預設 `/{lang}/{identifier}`，可用 `{lang}` 與 `{identifier}` |
| `page_size` | 預設 `200`，每次 GraphQL 擷取幾筆 published posts |
| `max_urls_per_file` | 預設 `50000`，每個 sitemap 檔最多幾筆 URL |

```
GET /export/posts-sitemap-to-gcs?prefix=json/sitemaps&base_url=https://example.com&url_template=/{lang}/posts/{id}&content_url_template=/{lang}/{identifier}&page_size=200&max_urls_per_file=50000
```

### `GET /export/home-sections-to-gcs`

一次執行三支固定 GraphQL，並分別輸出到：

- `footer.json`：`contents`（欄位只取 `id/slug/title/order/status`）
- `editor-choices.json`：固定 `take=4`、`skip=0`、`orderBy=[{ sortOrder: asc }]`
- `pop-polls.json`：固定 `take=1`，條件 `expiresAt > now(ISO)`，排序 `totalVotes desc`

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/home-sections` |

範例：

```
GET /export/home-sections-to-gcs?prefix=exports/home-sections/dev
```

### `GET /export/home-editor-choices-to-gcs`

只輸出首頁四格編輯精選 **`editor-choices.json`**，方便獨立設定 scheduler。

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/home-sections` |

```
GET /export/home-editor-choices-to-gcs?prefix=exports/home-sections/dev
```

### `GET /export/home-pop-polls-to-gcs`

只輸出首頁熱門投票 **`pop-polls.json`**，方便設定每 10 分鐘更新。

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/home-sections` |

```
GET /export/home-pop-polls-to-gcs?prefix=exports/home-sections/dev
```

### `GET /export/topic-posts-to-gcs`

與前端論壇 GQL 對齊：每個有 `slug` 的 topic 寫入 `latest` / `polls` 兩種檔案（`prefix/` 下），每次執行覆寫。

| 檔名 | 對應前端 query | 差異 |
|------|----------------|------|
| `{slug}-latest.json` | `TopicLatest` | `orderBy: [{ createdAt: desc }]` |
| `{slug}-polls.json` | `TopicPolls` | `where` 多 `NOT: [{ poll: null }]` |

- **篩選**：`topics: { slug: { equals: $slug } }` + `status`（`post_state=active` 時為 enum：`equals: published`，**不可**寫成字串 `"published"`）。
- **變數**：`$slug`、`$take`；`take` = `per_topic_limit`（並受 `GQL_POST_MAX_TAKE` 上限，預設 100）。
- **`posts` 內容**：與前端 `PostCardFields` + `PhotoFields` 相同選取，**不做後端欄位轉換**，前端可直接沿用型別。
- **`postsCount`**：與前端相同之 `postsCount(where: …)`，代表符合條件的總筆數。
- **`totalCount`**：目前與 `postsCount` 相同，明確表示總筆數。
- **`hasAll`**：`posts.length >= totalCount`。
- **`content` trimming**：每筆 post 的 `content` 最多輸出 120 個字元；若原文超過，尾端補上 `......`。
- **debug 欄位**：每筆 post 會帶 `score` 與 `scoreBreakdown`，方便驗證熱門排序；`scoreBreakdown` 會拆出 `reactionsCount`、`reactionScore`、`pollVotes`、`pollScore`、`commentsCount`、`commentScore`、`total`。`pop` 類 JSON 另外會帶 `rankingReason`，值可能是 `boost`、`3d-score`、`14d-score`、`latest-fallback`。

每個 JSON 結構統一為：

```json
{
  "generatedAt": "...",
  "topic": { "id": "...", "name": "...", "slug": "..." },
  "postsCount": 42,
  "totalCount": 42,
  "hasAll": false,
  "posts": [ ... ]
}
```

**無 `slug` 的 topic** 不會產檔（前端 query 亦依賴 slug）。`scan_multiplier` 用於熱門候選池總量；服務會用分頁方式掃描最多 `max(50, per_topic_limit * scan_multiplier)` 篇候選，再依積分排序。

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/topic-posts` |
| `per_topic_limit` | 對應 GQL `take`（1–200），實際 `min(per_topic_limit, GQL_POST_MAX_TAKE)` |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 相容參數（此端點目前不使用） |

```
GET /export/topic-posts-to-gcs?prefix=json/topics&per_topic_limit=100&post_state=active
```

### `GET /export/topic-pops-to-gcs`

每個有 `slug` 的 topic 寫入 `{slug}-pop.json`，可用較低頻率排程。

- 先放該 topic `isBoost=true` 的貼文
- 再依熱門規則補齊：Reaction*2 + PollVote*3 + Comment*5，總分需 `>= HOT_SCORE_THRESHOLD`（預設 5）
- 逐層補滿：先補 3 天內達門檻且積分排序前 50 的文章；若不足，再補 14 天內達門檻且積分排序前 50 的文章；若 14 天內完全沒有互動熱門文，才改用最新文章前 10 篇遞補。若仍為空，JSON 會回傳 `emptyMessage: "尚無貼文"`
- `postsCount` 代表最終輸出的篇數；`totalCount` 代表依上述熱門規則可用來組成結果的總篇數（去重後）；`hasAll` 為 `posts.length >= totalCount`

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/topic-posts` |
| `per_topic_limit` | 查詢基礎 `take`（1–200）；`-pop.json` 最終輸出固定最多 50 篇 |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 熱門候選池倍率（影響 3天/14天候選範圍） |

```
GET /export/topic-pops-to-gcs?prefix=json/topics&per_topic_limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/curated-posts-to-gcs`

針對全站 `posts`，依兩個布林欄位分別產出六種 JSON：

| 檔名 | 篩選條件 | 排序 / 規則 |
|------|----------|-------------|
| `editor-choice-latest.json` | `isEditorChoice=true` | `createdAt desc` |
| `editor-choice-polls.json` | `isEditorChoice=true` 且 `poll != null` | `createdAt desc` |
| `editor-choice-pop.json` | `isEditorChoice=true` | 與 topic pop 相同：Boost 優先 + 3 天熱門 + 14 天熱門補滿 + 無互動時 latest fallback |
| `life-guide-latest.json` | `isLifeGuide=true` | `createdAt desc` |
| `life-guide-polls.json` | `isLifeGuide=true` 且 `poll != null` | `createdAt desc` |
| `life-guide-pop.json` | `isLifeGuide=true` | 與 topic pop 相同：Boost 優先 + 3 天熱門 + 14 天熱門補滿 + 無互動時 latest fallback |

- **篩選**：`status`（`post_state=active` 時為 enum：`equals: published`）加上對應布林欄位 `isEditorChoice` 或 `isLifeGuide`。
- **`posts` 內容**：與現有 topic export 一致，沿用前端 `PostCardFields` + `PhotoFields`。
- **`postsCount`**：`latest` / `polls` 為該條件下的 `postsCount(where: …)`；`pop` 則代表最終輸出的篇數。
- **`totalCount`**：`latest` / `polls` 為符合條件的總筆數；`pop` 為依熱門規則可用來組成結果的總篇數（去重後）。
- **`hasAll`**：`posts.length >= totalCount`。
- **`content` trimming**：每筆 post 的 `content` 最多輸出 120 個字元；若原文超過，尾端補上 `......`。
- **debug 欄位**：每筆 post 會帶 `score` 與 `scoreBreakdown`，方便驗證熱門排序；`pop` 類 JSON 另外會帶 `rankingReason`。

每個 JSON 結構統一為：

```json
{
  "generatedAt": "...",
  "collection": {
    "key": "editor-choice",
    "label": "編輯精選",
    "flag": "isEditorChoice"
  },
  "postsCount": 42,
  "totalCount": 42,
  "hasAll": false,
  "posts": [ ... ]
}
```

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/curated-posts` |
| `limit` | 每種 JSON 擷取幾則文章（1–200），實際 `min(limit, GQL_POST_MAX_TAKE)` |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 熱門候選池倍率（只影響 `*-pop.json`） |

```
GET /export/curated-posts-to-gcs?prefix=json/curated&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/curated-posts-latest-polls-to-gcs`

只輸出 curated posts 的 `latest / polls` 四個檔案：

- `editor-choice-latest.json`
- `editor-choice-polls.json`
- `life-guide-latest.json`
- `life-guide-polls.json`

Query 參數同 `/export/curated-posts-to-gcs`。

```
GET /export/curated-posts-latest-polls-to-gcs?prefix=json/curated&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/curated-posts-pops-to-gcs`

只輸出 curated posts 的 `pop` 兩個檔案：

- `editor-choice-pop.json`
- `life-guide-pop.json`

Query 參數同 `/export/curated-posts-to-gcs`。

```
GET /export/curated-posts-pops-to-gcs?prefix=json/curated&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/all-posts-to-gcs`

針對全站所有 `posts` 產出三種 JSON：

| 檔名 | 篩選條件 | 排序 / 規則 |
|------|----------|-------------|
| `all-posts-latest.json` | 所有符合 `status` 的文章 | `createdAt desc` |
| `all-posts-polls.json` | 所有符合 `status` 且 `poll != null` 的文章 | `createdAt desc` |
| `all-posts-pop.json` | 所有符合 `status` 的文章 | 與 topic pop 相同：Boost 優先 + 3 天熱門 + 14 天熱門補滿 + 無互動時 latest fallback |

- **篩選**：僅套用 `status`（`post_state=active` 時為 enum：`equals: published`）。
- **`posts` 內容**：與現有 topic export / curated export 一致，沿用前端 `PostCardFields` + `PhotoFields`。
- **`postsCount`**：`latest` / `polls` 為該條件下的 `postsCount(where: …)`；`pop` 則代表最終輸出的篇數。
- **`totalCount`**：`latest` / `polls` 為符合條件的總筆數；`pop` 為依熱門規則可用來組成結果的總篇數（去重後）。
- **`hasAll`**：`posts.length >= totalCount`。
- **`content` trimming**：每筆 post 的 `content` 最多輸出 120 個字元；若原文超過，尾端補上 `......`。
- **debug 欄位**：每筆 post 會帶 `score` 與 `scoreBreakdown`，方便驗證熱門排序；`pop` 類 JSON 另外會帶 `rankingReason`。

每個 JSON 結構統一為：

```json
{
  "generatedAt": "...",
  "collection": {
    "key": "all-posts",
    "label": "所有文章"
  },
  "postsCount": 42,
  "totalCount": 42,
  "hasAll": false,
  "posts": [ ... ]
}
```

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/all-posts` |
| `limit` | 每種 JSON 擷取幾則文章（1–200），實際 `min(limit, GQL_POST_MAX_TAKE)` |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 熱門候選池倍率（只影響 `all-posts-pop.json`） |

```
GET /export/all-posts-to-gcs?prefix=json/all-posts&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/all-posts-pops-to-gcs`

只輸出全站所有文章的熱門檔案 `all-posts-pop.json`，方便與 `latest / polls` 分開排程。

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/all-posts` |
| `limit` | 熱門候選池基礎設定（1–200），實際 `min(limit, GQL_POST_MAX_TAKE)` |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 熱門候選池倍率（只影響 `all-posts-pop.json`） |

```
GET /export/all-posts-pops-to-gcs?prefix=json/all-posts&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/all-posts-latest-polls-to-gcs`

只輸出全站所有文章的 `latest / polls` 兩個檔案：

- `all-posts-latest.json`
- `all-posts-polls.json`

Query 參數同 `/export/all-posts-to-gcs`。

```
GET /export/all-posts-latest-polls-to-gcs?prefix=json/all-posts&limit=100&post_state=active&scan_multiplier=10
```

### `GET /export/topics-daily-stats-to-gcs`

各 topic 在指定時區「當日」新文章數，合併為 **`prefix` 下的 `topics-daily.json`**（無時間戳子目錄；每次執行 **覆寫**）。

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/topic-daily-stats` |
| `timezone` | IANA 時區，預設 `Asia/Taipei` |
| `local_date` | 選填，`YYYY-MM-DD`；省略則為該時區的「今天」 |
| `post_state` | 預設 `active` |

範例：

```
GET /export/topics-daily-stats-to-gcs?prefix=exports/topic-daily-stats/dev&timezone=Asia/Taipei&post_state=active
```

`local_date` 可加上，例如 `&local_date=2026-04-07`。

### `GET /maintenance/retry-missing-translations`

掃描 `spamScore = null` 的 posts / comments，重新呼叫 message-services `POST /hooks/sync-translations` 補送 AI 翻譯與 spamScore。

- Post：預設篩選 `status in published,pending,draft`，且 `title` 或 `content` 不為空。
- Comment：預設篩選 `status in published`，`content` 不為空，且 `pauseAutoTranslation=false`。
- 預設 `dry_run=true`，只列出符合條件的資料，不會真的呼叫 message-services。

Query 參數：

| 參數 | 說明 |
|------|------|
| `targets` | 預設 `posts,comments`；可傳 `posts`、`comments` 或 `posts,comments` |
| `limit` | 預設 `100`；每種 target 本次最多挑幾筆 |
| `dry_run` | 預設 `true`；Cloud Scheduler 要實際補送時設 `false` |
| `message_services_url` | 選填；message-services 根網址。若不傳，讀 `MESSAGE_SERVICES_URL` / `MESSAGE_SERVICES_BASE_URL` |
| `post_statuses` | 預設 `published,pending,draft`；傳 `all` 表示不篩狀態 |
| `comment_statuses` | 預設 `published`；傳 `all` 表示不篩狀態 |

先 dry-run 檢查：

```
GET /maintenance/retry-missing-translations?targets=posts,comments&limit=50&dry_run=true
```

Cloud Scheduler 實際補送：

```
GET /maintenance/retry-missing-translations?targets=posts,comments&limit=100&dry_run=false
```

若環境沒有設定 `MESSAGE_SERVICES_URL`，也可以直接帶：

```
GET /maintenance/retry-missing-translations?targets=posts,comments&limit=100&dry_run=false&message_services_url=https://message-services-xxx.a.run.app
```
