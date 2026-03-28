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

### `POST /export/contents-to-gcs`

```json
{
  "prefix": "exports/contents/dev",
  "page_size": 200,
  "id": "clxxxxxxxxxxxxxxxxxxxx"
}
```

- 有 `id`：只匯出該筆 content；無 `id`：分頁匯出全部。

### `POST /export/topic-posts-to-gcs`

產出 `latest.json`、`hot.json`、`with-poll.json` 三檔。

```json
{
  "prefix": "exports/topic-posts/dev",
  "per_topic_limit": 10,
  "post_state": "active",
  "scan_multiplier": 10
}
```

### `POST /export/topics-daily-stats-to-gcs`

各 topic 在指定時區「當日」新文章數，合併為 `topics-daily.json`。

```json
{
  "prefix": "exports/topic-daily-stats/dev",
  "timezone": "Asia/Taipei",
  "local_date": null,
  "post_state": "active"
}
```

`local_date` 可為 `YYYY-MM-DD`；省略則為該時區的「今天」。
