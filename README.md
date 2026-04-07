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
- `GQL_POST_MAX_TAKE`：選填，預設 `100`。Keystone 對 `Post` 的 `graphql.maximumTake` 常有上限（例如 100）；`/export/topic-posts-to-gcs` 會依此 **分頁** 拉取 `per_topic_limit × scan_multiplier` 筆，避免單次 `take` 過大導致 GraphQL **HTTP 400**。

Cloud Run 執行身分需能寫入該 bucket（例如 `roles/storage.objectAdmin` 或最小必要權限）。

## 啟動

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- OpenAPI：`http://localhost:8000/docs`
- 健康檢查：`GET /health`、`GET /healthz`

## API（由 message-services 搬移）

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

### `GET /export/topic-posts-to-gcs`

與**前端論壇 GQL**對齊：每個 **有 `slug` 的 topic** 寫入三支查詢結果（`prefix/` 下），每次執行 **覆寫**。

| 檔名 | 對應前端 query | 差異 |
|------|----------------|------|
| `{slug}-latest.json` | `TopicLatest` | `orderBy: [{ createdAt: desc }]` |
| `{slug}-pop.json` | `TopicPopular` | `orderBy: [{ commentCount: desc }, { createdAt: desc }]` |
| `{slug}-polls.json` | `TopicPolls` | `where` 多 `NOT: [{ poll: null }]` |

- **篩選**：`topics: { some: { slug: { equals: $slug } } }` + `status`（`post_state=active` 時為 `equals: "published"`）。
- **變數**：`$slug`、`$take`；`take` = `per_topic_limit`（並受 `GQL_POST_MAX_TAKE` 上限，預設 100）。
- **`posts` 內容**：與前端 `PostCardFields` + `PhotoFields` 相同選取，**不做後端欄位轉換**，前端可直接沿用型別。
- **`postsCount`**：與前端相同之 `postsCount(where: …)`，供判斷是否還有更多筆。

每個 JSON 結構統一為：

```json
{
  "generatedAt": "...",
  "topic": { "id": "...", "name": "...", "slug": "..." },
  "postsCount": 42,
  "posts": [ ... ]
}
```

**無 `slug` 的 topic** 不會產檔（前端 query 亦依賴 slug）。`scan_multiplier` 僅保留 API 相容，**目前不使用**。

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/topic-posts` |
| `per_topic_limit` | 對應 GQL `take`（1–200），實際 `min(per_topic_limit, GQL_POST_MAX_TAKE)` |
| `post_state` | 預設 `active` → where 使用 `published` |
| `scan_multiplier` | 相容舊參數，目前忽略 |

```
GET /export/topic-posts-to-gcs?prefix=json/topics&per_topic_limit=100&post_state=active
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
