import json
import os
import sys
import types
import unittest
from unittest.mock import patch


google_module = types.ModuleType("google")
auth_module = types.ModuleType("google.auth")
auth_transport_module = types.ModuleType("google.auth.transport")
auth_requests_module = types.ModuleType("google.auth.transport.requests")
cloud_module = types.ModuleType("google.cloud")
oauth2_module = types.ModuleType("google.oauth2")
oauth2_id_token_module = types.ModuleType("google.oauth2.id_token")
storage_module = types.ModuleType("google.cloud.storage")
httpx_module = types.ModuleType("httpx")
auth_module.transport = auth_transport_module
auth_transport_module.requests = auth_requests_module
cloud_module.storage = storage_module
google_module.cloud = cloud_module
google_module.auth = auth_module
google_module.oauth2 = oauth2_module
oauth2_module.id_token = oauth2_id_token_module
auth_requests_module.Request = object
oauth2_id_token_module.fetch_id_token = lambda request, audience: ""
storage_module.Blob = object
storage_module.Bucket = object
storage_module.Client = object
httpx_module.Client = object
httpx_module.Timeout = object
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.auth", auth_module)
sys.modules.setdefault("google.auth.transport", auth_transport_module)
sys.modules.setdefault("google.auth.transport.requests", auth_requests_module)
sys.modules.setdefault("google.cloud", cloud_module)
sys.modules.setdefault("google.cloud.storage", storage_module)
sys.modules.setdefault("google.oauth2", oauth2_module)
sys.modules.setdefault("google.oauth2.id_token", oauth2_id_token_module)
sys.modules.setdefault("httpx", httpx_module)

from app import export_contents, export_topic_posts  # noqa: E402
from app.config import get_settings  # noqa: E402


class FakeBlob:
    def __init__(self, path):
        self.path = path
        self.cache_control = None
        self.uploaded = None
        self.content_type = None

    def upload_from_string(self, payload, content_type=None):
        self.uploaded = payload
        self.content_type = content_type


class FakeBucket:
    def __init__(self, name="rti-forum-cms"):
        self.name = name
        self.blobs = {}

    def blob(self, path):
        blob = FakeBlob(path)
        self.blobs[path] = blob
        return blob


class FakeStorageClient:
    def __init__(self, bucket):
        self.bucket_obj = bucket

    def bucket(self, name):
        self.bucket_obj.name = name
        return self.bucket_obj


class JsonPayloadUrlRewriteTest(unittest.TestCase):
    def tearDown(self):
        get_settings.cache_clear()

    def test_upload_json_rewrites_nested_gcs_public_urls_with_web_url_base(self):
        payload = {
            "editorChoices": [
                {
                    "post": {
                        "heroImages": [
                            {
                                "resized": {
                                    "original": "https://storage.googleapis.com/rti-forum-cms/json/home-sections/editor-choices.json",
                                    "w480": "https://storage.googleapis.com/other-bucket/json/home-sections/editor-choices.json",
                                }
                            }
                        ]
                    }
                }
            ],
            "count": 1,
        }
        bucket = FakeBucket()

        with patch.dict(
            os.environ,
            {
                "GCS_BUCKET": "rti-forum-cms",
                "WEB_URL_BASE": "https://cdn.example.com/base/",
            },
            clear=True,
        ):
            get_settings.cache_clear()
            export_topic_posts._upload_json(bucket, "json/output.json", payload)

        uploaded = json.loads(bucket.blobs["json/output.json"].uploaded)
        resized = uploaded["editorChoices"][0]["post"]["heroImages"][0]["resized"]
        self.assertEqual(
            resized["original"],
            "https://cdn.example.com/base/json/home-sections/editor-choices.json",
        )
        self.assertEqual(
            resized["w480"],
            "https://storage.googleapis.com/other-bucket/json/home-sections/editor-choices.json",
        )
        original = payload["editorChoices"][0]["post"]["heroImages"][0]["resized"]
        self.assertEqual(
            original["original"],
            "https://storage.googleapis.com/rti-forum-cms/json/home-sections/editor-choices.json",
        )

    def test_upload_json_leaves_urls_unchanged_without_web_url_base(self):
        payload = {
            "url": "https://storage.googleapis.com/rti-forum-cms/json/home-sections/editor-choices.json"
        }
        bucket = FakeBucket()

        with patch.dict(os.environ, {"GCS_BUCKET": "rti-forum-cms"}, clear=True):
            get_settings.cache_clear()
            export_topic_posts._upload_json(bucket, "json/output.json", payload)

        uploaded = json.loads(bucket.blobs["json/output.json"].uploaded)
        self.assertEqual(uploaded, payload)

    def test_content_export_rewrites_direct_json_upload_payloads(self):
        bucket = FakeBucket()
        content_row = {
            "id": "content-1",
            "identifier": "about",
            "photos": [
                {
                    "urlOriginal": "https://storage.googleapis.com/rti-forum-cms/json/home-sections/editor-choices.json",
                }
            ],
        }

        def fake_execute_gql(query, variables):
            return {"content": content_row}

        with patch.dict(
            os.environ,
            {
                "GCS_BUCKET": "rti-forum-cms",
                "WEB_URL_BASE": "https://cdn.example.com",
            },
            clear=True,
        ), patch.object(
            export_contents, "execute_gql", fake_execute_gql
        ), patch.object(
            export_contents.storage,
            "Client",
            lambda: FakeStorageClient(bucket),
        ):
            get_settings.cache_clear()
            export_contents.export_all_contents_to_gcs(
                prefix="json/contents",
                content_slug="about",
            )

        uploaded = json.loads(bucket.blobs["json/contents/about.json"].uploaded)
        self.assertEqual(
            uploaded["photos"][0]["urlOriginal"],
            "https://cdn.example.com/json/home-sections/editor-choices.json",
        )


if __name__ == "__main__":
    unittest.main()
