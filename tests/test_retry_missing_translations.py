import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main
from app import retry_missing_translations as retry_module


class RetryMissingTranslationsTest(unittest.TestCase):
    def test_downstream_timeout_is_returned_as_item_failure(self):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                pass

            def post(self, url, json, **kwargs):
                raise httpx.ReadTimeout("message-services timed out")

        post = {
            "id": "post-1",
            "title": "title",
            "content": "content",
            "status": "published",
        }

        with patch.object(
            retry_module,
            "_fetch_posts_missing_translation",
            return_value=([post], 1),
        ):
            with patch.object(
                retry_module,
                "_fetch_comments_missing_translation",
                return_value=([], 0),
            ):
                with patch.object(retry_module.httpx, "Client", FakeClient):
                    result = retry_module.retry_missing_translations(
                        targets="posts",
                        limit=1,
                        dry_run=False,
                        message_services_url="https://message-services.example",
                    )

        self.assertEqual(result["attemptedCount"], 1)
        self.assertEqual(result["successCount"], 0)
        self.assertEqual(result["failureCount"], 1)
        self.assertEqual(result["results"][0]["id"], "post-1")
        self.assertEqual(result["results"][0]["type"], "post")
        self.assertFalse(result["results"][0]["ok"])
        self.assertIsNone(result["results"][0]["status_code"])
        self.assertEqual(result["results"][0]["error_type"], "ReadTimeout")
        self.assertIn("message-services timed out", result["results"][0]["error"])

    def test_stops_before_next_item_when_runtime_budget_cannot_fit_timeout(self):
        calls = []

        class FakeResponse:
            status_code = 200
            text = '{"ok": true}'

            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                pass

            def post(self, url, json, **kwargs):
                calls.append(json["id"])
                return FakeResponse()

        posts = [
            {
                "id": "post-1",
                "title": "title",
                "content": "content",
                "status": "published",
            },
            {
                "id": "post-2",
                "title": "title",
                "content": "content",
                "status": "published",
            },
        ]

        with patch.object(
            retry_module,
            "_fetch_posts_missing_translation",
            return_value=(posts, 2),
        ):
            with patch.object(
                retry_module,
                "_fetch_comments_missing_translation",
                return_value=([], 0),
            ):
                with patch.object(retry_module.httpx, "Client", FakeClient):
                    with patch.object(
                        retry_module,
                        "monotonic",
                        side_effect=[0.0, 0.0, 31.0],
                        create=True,
                    ):
                        result = retry_module.retry_missing_translations(
                            targets="posts",
                            limit=2,
                            dry_run=False,
                            message_services_url="https://message-services.example",
                            sync_timeout_seconds=30.0,
                            max_runtime_seconds=60.0,
                        )

        self.assertEqual(calls, ["post-1"])
        self.assertEqual(result["attemptedCount"], 1)
        self.assertEqual(result["successCount"], 1)
        self.assertEqual(result["failureCount"], 0)
        self.assertTrue(result["stoppedEarly"])
        self.assertEqual(result["skippedCount"], 1)
        self.assertIn("max_runtime_seconds", result["stopReason"])


class RetryMissingTranslationsEndpointTest(unittest.TestCase):
    def test_passes_timeout_controls_to_worker(self):
        captured = {}

        def fake_retry_missing_translations(**kwargs):
            captured.update(kwargs)
            return {
                "dryRun": kwargs["dry_run"],
                "attemptedCount": 0,
                "successCount": 0,
                "failureCount": 0,
                "results": [],
            }

        client = TestClient(main.app)

        with patch.object(
            main,
            "retry_missing_translations",
            fake_retry_missing_translations,
        ):
            response = client.get(
                "/maintenance/retry-missing-translations"
                "?targets=posts"
                "&limit=1"
                "&dry_run=false"
                "&sync_timeout_seconds=12.5"
                "&max_runtime_seconds=45.5"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["targets"], "posts")
        self.assertEqual(captured["limit"], 1)
        self.assertFalse(captured["dry_run"])
        self.assertEqual(captured["sync_timeout_seconds"], 12.5)
        self.assertEqual(captured["max_runtime_seconds"], 45.5)


if __name__ == "__main__":
    unittest.main()
