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

from app import export_posts_sitemap
from app.config import get_settings


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


class ExportPostsSitemapTest(unittest.TestCase):
    def tearDown(self):
        get_settings.cache_clear()

    def test_export_includes_home_and_important_listing_pages(self):
        bucket = FakeBucket()

        def fake_execute_gql(query, variables):
            if "posts(" in query:
                return {"posts": [], "postsCount": 0}
            if "contents(" in query:
                return {"contents": [], "contentsCount": 0}
            raise AssertionError(f"unexpected query: {query}")

        with patch.dict(os.environ, {"GCS_BUCKET": "rti-forum-cms"}, clear=True):
            get_settings.cache_clear()
            with patch.object(export_posts_sitemap, "execute_gql", fake_execute_gql):
                with patch.object(
                    export_posts_sitemap.storage,
                    "Client",
                    lambda: FakeStorageClient(bucket),
                ):
                    result = export_posts_sitemap.export_posts_sitemap_to_gcs(
                        prefix="json/sitemaps",
                        base_url="https://www.rtitalk.tw",
                    )

        self.assertIn("json/sitemaps/pages-sitemap-1.xml", result["files"])
        self.assertEqual(result["page_sitemap_files_count"], 1)
        self.assertEqual(result["static_page_url_count"], 15)

        index_xml = bucket.blobs["json/sitemaps/sitemap.xml"].uploaded
        self.assertIn(
            "<loc>https://www.rtitalk.tw/json/sitemaps/pages-sitemap-1.xml</loc>",
            index_xml,
        )

        pages_xml = bucket.blobs["json/sitemaps/pages-sitemap-1.xml"].uploaded
        self.assertIn("<loc>https://www.rtitalk.tw/zh</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/editors-pick</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/topics</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/en</loc>", pages_xml)
        self.assertEqual(pages_xml.count("<url>"), 15)

    def test_export_uses_frontend_content_route_by_default(self):
        bucket = FakeBucket()

        def fake_execute_gql(query, variables):
            if "posts(" in query:
                return {"posts": [], "postsCount": 0}
            if "contents(" in query:
                return {
                    "contents": [
                        {
                            "identifier": "terms",
                            "updatedAt": "2026-07-09T04:30:03+00:00",
                        }
                    ],
                    "contentsCount": 1,
                }
            raise AssertionError(f"unexpected query: {query}")

        with patch.dict(os.environ, {"GCS_BUCKET": "rti-forum-cms"}, clear=True):
            get_settings.cache_clear()
            with patch.object(export_posts_sitemap, "execute_gql", fake_execute_gql):
                with patch.object(
                    export_posts_sitemap.storage,
                    "Client",
                    lambda: FakeStorageClient(bucket),
                ):
                    export_posts_sitemap.export_posts_sitemap_to_gcs(
                        prefix="json/sitemaps",
                        base_url="https://www.rtitalk.tw",
                        content_url_template="",
                    )

        contents_xml = bucket.blobs["json/sitemaps/contents-sitemap-1.xml"].uploaded
        self.assertIn(
            "<loc>https://www.rtitalk.tw/zh/content/terms</loc>",
            contents_xml,
        )
        self.assertNotIn("<loc>https://www.rtitalk.tw/zh/terms</loc>", contents_xml)


if __name__ == "__main__":
    unittest.main()
