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


def make_fake_gql(posts=None, contents=None, events=None, topics=None):
    """One dispatcher for every list the exporter pages through. Each query is matched
    on its own root field so a new one can't silently fall through to another's data."""

    def fake_execute_gql(query, variables):
        if "posts(" in query:
            items = posts or []
            return {"posts": items, "postsCount": len(items)}
        if "contents(" in query:
            items = contents or []
            return {"contents": items, "contentsCount": len(items)}
        if "events(" in query:
            items = events or []
            return {"events": items, "eventsCount": len(items)}
        if "topics(" in query:
            items = topics or []
            return {"topics": items, "topicsCount": len(items)}
        raise AssertionError(f"unexpected query: {query}")

    return fake_execute_gql


class ExportPostsSitemapTest(unittest.TestCase):
    def tearDown(self):
        get_settings.cache_clear()

    def test_export_includes_home_and_important_listing_pages(self):
        bucket = FakeBucket()
        fake_execute_gql = make_fake_gql()

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

        expected_url_count = len(export_posts_sitemap.STATIC_PAGE_PATH_TEMPLATES) * len(
            export_posts_sitemap.LANGUAGES
        )

        self.assertIn("json/sitemaps/pages-sitemap-1.xml", result["files"])
        self.assertEqual(result["page_sitemap_files_count"], 1)
        self.assertEqual(result["static_page_url_count"], expected_url_count)

        index_xml = bucket.blobs["json/sitemaps/sitemap.xml"].uploaded
        self.assertIn(
            "<loc>https://www.rtitalk.tw/json/sitemaps/pages-sitemap-1.xml</loc>",
            index_xml,
        )

        pages_xml = bucket.blobs["json/sitemaps/pages-sitemap-1.xml"].uploaded
        self.assertIn("<loc>https://www.rtitalk.tw/zh</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/editors-pick</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/topics</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/life-guide</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/rti-choice</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/zh/events</loc>", pages_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/en</loc>", pages_xml)
        self.assertEqual(pages_xml.count("<url>"), expected_url_count)

    def test_export_uses_frontend_content_route_by_default(self):
        bucket = FakeBucket()
        fake_execute_gql = make_fake_gql(
            contents=[{"identifier": "terms", "updatedAt": "2026-07-09T04:30:03+00:00"}]
        )

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

    def _run_export(self, bucket, fake_execute_gql):
        with patch.dict(os.environ, {"GCS_BUCKET": "rti-forum-cms"}, clear=True):
            get_settings.cache_clear()
            with patch.object(export_posts_sitemap, "execute_gql", fake_execute_gql):
                with patch.object(
                    export_posts_sitemap.storage,
                    "Client",
                    lambda: FakeStorageClient(bucket),
                ):
                    return export_posts_sitemap.export_posts_sitemap_to_gcs(
                        prefix="json/sitemaps",
                        base_url="https://www.rtitalk.tw",
                    )

    def test_event_detail_pages_are_listed(self):
        """They are server-rendered with their own metadata, but nothing linked to them
        and nothing listed them, so a crawler had no route to them at all."""
        bucket = FakeBucket()
        result = self._run_export(
            bucket,
            make_fake_gql(
                events=[
                    {
                        "slug": "launch",
                        "updatedAt": "2026-07-13T00:00:00Z",
                        "post": {"updatedAt": "2026-07-14T02:00:00Z"},
                    }
                ]
            ),
        )

        self.assertEqual(result["events_count"], 1)
        events_xml = bucket.blobs["json/sitemaps/events-sitemap-1.xml"].uploaded
        self.assertIn("<loc>https://www.rtitalk.tw/zh/events/launch</loc>", events_xml)
        self.assertIn("<loc>https://www.rtitalk.tw/th/events/launch</loc>", events_xml)
        self.assertEqual(events_xml.count("<url>"), len(export_posts_sitemap.LANGUAGES))
        # The post is newer than the event row, and the page renders the post.
        self.assertIn("<lastmod>2026-07-14T02:00:00Z</lastmod>", events_xml)

        index_xml = bucket.blobs["json/sitemaps/sitemap.xml"].uploaded
        self.assertIn("/json/sitemaps/events-sitemap-1.xml", index_xml)

    def test_topic_pages_are_listed(self):
        bucket = FakeBucket()
        result = self._run_export(
            bucket,
            make_fake_gql(topics=[{"slug": "current-events", "updatedAt": "2026-07-01T00:00:00Z"}]),
        )

        self.assertEqual(result["topics_count"], 1)
        topics_xml = bucket.blobs["json/sitemaps/topics-sitemap-1.xml"].uploaded
        self.assertIn("<loc>https://www.rtitalk.tw/zh/topics/current-events</loc>", topics_xml)
        self.assertIn(
            '<xhtml:link rel="alternate" hreflang="en" '
            'href="https://www.rtitalk.tw/en/topics/current-events" />',
            topics_xml,
        )

    def test_posts_query_excludes_event_backed_posts(self):
        """/posts/<id> 307-redirects to the event page when the post backs an event, so
        listing it would put a URL in the sitemap that never answers 200."""
        self.assertIn(
            "events: { none: {} }",
            export_posts_sitemap.QUERY_PUBLISHED_POSTS_FOR_SITEMAP,
        )

    def test_listing_pages_lastmod_tracks_content_not_the_clock(self):
        """Stamping them with the export time claimed every listing page changed on every
        run — a freshness signal Google learns to discount."""
        bucket = FakeBucket()
        self._run_export(
            bucket,
            make_fake_gql(
                posts=[
                    {"id": "9", "updatedAt": "2026-07-14T01:00:00Z", "createdAt": "2026-01-01T00:00:00Z"},
                    {"id": "8", "updatedAt": "2026-05-02T00:00:00Z", "createdAt": "2026-01-01T00:00:00Z"},
                ]
            ),
        )

        pages_xml = bucket.blobs["json/sitemaps/pages-sitemap-1.xml"].uploaded
        self.assertIn("<lastmod>2026-07-14T01:00:00Z</lastmod>", pages_xml)
        self.assertNotIn("2026-05-02", pages_xml)


if __name__ == "__main__":
    unittest.main()
