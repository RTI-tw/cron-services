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

產出 `latest.json`、`hot.json`、`with-poll.json` 三檔，寫在 **`prefix` 目錄下固定檔名**，每次執行 **覆寫**。

Query 參數：

| 參數 | 說明 |
|------|------|
| `prefix` | 預設 `exports/topic-posts` |
| `per_topic_limit` | 每個 topic 取幾筆，預設 `10`（1–200） |
| `post_state` | 預設 `active`（會映射為 Keystone `published`） |
| `scan_multiplier` | 預設 `10`（1–50），用於熱門／含投票排序前的掃描倍數 |

範例：

```
GET /export/topic-posts-to-gcs?prefix=exports/topic-posts/dev&per_topic_limit=10&post_state=active&scan_multiplier=10
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
